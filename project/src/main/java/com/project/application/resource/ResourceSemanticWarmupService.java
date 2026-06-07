package com.project.application.resource;

import com.project.api.resource.dto.ResourceSemanticResultResponse;
import com.project.api.resource.dto.ResourceSemanticSearchResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.core.task.TaskExecutor;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

@Service
public class ResourceSemanticWarmupService {

    private static final Logger LOGGER = LoggerFactory.getLogger(ResourceSemanticWarmupService.class);
    private static final Duration READY_TTL = Duration.ofMinutes(15);
    private static final Duration FAILED_TTL = Duration.ofMinutes(2);
    private static final int DEFAULT_TOP_K = 20;
    private static final int MIN_TOP_K = 6;

    private final NamedParameterJdbcTemplate jdbcTemplate;
    private final ResourceSemanticSearchClient semanticSearchClient;
    private final TaskExecutor warmupTaskExecutor;
    private final ConcurrentHashMap<UUID, WarmupEntry> entries = new ConcurrentHashMap<>();
    private final Set<UUID> inFlightUsers = ConcurrentHashMap.newKeySet();

    public ResourceSemanticWarmupService(
        NamedParameterJdbcTemplate jdbcTemplate,
        ResourceSemanticSearchClient semanticSearchClient,
        @Qualifier("resourceSemanticWarmupTaskExecutor") TaskExecutor warmupTaskExecutor
    ) {
        this.jdbcTemplate = jdbcTemplate;
        this.semanticSearchClient = semanticSearchClient;
        this.warmupTaskExecutor = warmupTaskExecutor;
    }

    public List<UUID> stageRankedIds(UUID userId, int desiredLimit) {
        WarmupEntry entry = entries.get(userId);
        if (entry != null && entry.isReady()) {
            return entry.prefix(desiredLimit);
        }
        if (entry != null && !entry.isExpired()) {
            return List.of();
        }
        String query = currentLearningStageQuery(userId);
        if (query.isBlank()) {
            entries.remove(userId);
            return List.of();
        }
        submitWarmup(userId, query, normalizeTopK(desiredLimit));
        return List.of();
    }

    public List<UUID> recommendationIds(UUID userId, int desiredLimit) {
        return stageRankedIds(userId, Math.max(desiredLimit * 3, desiredLimit));
    }

    public void submitCurrentStageWarmup(UUID userId) {
        submitCurrentStageWarmup(userId, DEFAULT_TOP_K);
    }

    public void submitCurrentStageWarmup(UUID userId, int desiredLimit) {
        if (userId == null || !inFlightUsers.add(userId)) {
            return;
        }
        int topK = normalizeTopK(desiredLimit);
        warmupTaskExecutor.execute(() -> {
            try {
                String query = currentLearningStageQuery(userId);
                if (query.isBlank()) {
                    entries.remove(userId);
                    return;
                }
                runWarmup(userId, query, topK);
            } finally {
                inFlightUsers.remove(userId);
            }
        });
    }

    private void submitWarmup(UUID userId, String query, int topK) {
        if (!inFlightUsers.add(userId)) {
            return;
        }
        warmupTaskExecutor.execute(() -> {
            try {
                runWarmup(userId, query, topK);
            } finally {
                inFlightUsers.remove(userId);
            }
        });
    }

    private void runWarmup(UUID userId, String query, int topK) {
        WarmupEntry current = entries.get(userId);
        if (current != null && current.isReady() && current.query().equals(query) && current.resourceIds().size() >= topK) {
            return;
        }
        try {
            ResourceSemanticSearchResponse response = semanticSearchClient.search(userId, query, topK);
            List<UUID> resourceIds = response.results() == null
                ? List.of()
                : distinctResourceIds(response.results(), topK);
            entries.put(userId, WarmupEntry.ready(query, resourceIds));
        } catch (RuntimeException ex) {
            LOGGER.warn("Failed to warm up resource semantic ranking userId={}: {}", userId, ex.getMessage());
            entries.put(userId, WarmupEntry.failed(query, ex.getMessage()));
        }
    }

    private List<UUID> distinctResourceIds(List<ResourceSemanticResultResponse> results, int topK) {
        Set<UUID> seen = new LinkedHashSet<>();
        for (ResourceSemanticResultResponse result : results) {
            if (result.resourceId() != null) {
                seen.add(result.resourceId());
            }
            if (seen.size() >= topK) {
                break;
            }
        }
        return new ArrayList<>(seen);
    }

    private String currentLearningStageQuery(UUID userId) {
        List<String> values = jdbcTemplate.query(
            """
            WITH latest_plan AS (
              SELECT p.plan_json
              FROM app.learning_plan p
              WHERE p.user_id = :userId
                AND p.status = 'ACTIVE'
              ORDER BY p.updated_at DESC
              LIMIT 1
            ),
            latest_task_path AS (
              SELECT t.response_summary -> 'learningPath' AS learning_path
              FROM app.smart_engine_task t
              WHERE t.user_id = :userId
                AND t.service_type = 'PERSONALIZED_LEARNING'
                AND t.task_status::text = 'COMPLETED'
                AND jsonb_exists(t.response_summary, 'learningPath')
              ORDER BY t.created_at DESC
              LIMIT 1
            ),
            current_learning_path AS (
              SELECT COALESCE(
                (SELECT plan_json FROM latest_plan WHERE jsonb_typeof(plan_json) = 'object'),
                (SELECT learning_path FROM latest_task_path WHERE jsonb_typeof(learning_path) = 'object'),
                '{}'::jsonb
              ) AS learning_path
            ),
            root_steps AS (
              SELECT step_item.step,
                     step_item.ordinality::numeric AS ordinality
              FROM current_learning_path clp
              CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(clp.learning_path -> 'steps') = 'array'
                  THEN clp.learning_path -> 'steps'
                  ELSE '[]'::jsonb
                END
              ) WITH ORDINALITY AS step_item(step, ordinality)
            ),
            stage_steps AS (
              SELECT stage_step.step || jsonb_build_object(
                       'stageTitle', stage_item.stage ->> 'title',
                       'stageDescription', stage_item.stage ->> 'description'
                     ) AS step,
                     (stage_item.ordinality * 1000 + stage_step.ordinality)::numeric AS ordinality
              FROM current_learning_path clp
              CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(clp.learning_path -> 'stages') = 'array'
                  THEN clp.learning_path -> 'stages'
                  ELSE '[]'::jsonb
                END
              ) WITH ORDINALITY AS stage_item(stage, ordinality)
              CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(stage_item.stage -> 'steps') = 'array'
                  THEN stage_item.stage -> 'steps'
                  ELSE jsonb_build_array(stage_item.stage)
                END
              ) WITH ORDINALITY AS stage_step(step, ordinality)
            ),
            path_steps AS (
              SELECT step,
                     ordinality,
                     upper(regexp_replace(COALESCE(step ->> 'status', ''), '[^A-Za-z0-9]+', '_', 'g')) AS normalized_status
              FROM root_steps
              UNION ALL
              SELECT step,
                     ordinality,
                     upper(regexp_replace(COALESCE(step ->> 'status', ''), '[^A-Za-z0-9]+', '_', 'g')) AS normalized_status
              FROM stage_steps
              WHERE NOT EXISTS (SELECT 1 FROM root_steps)
            ),
            active_step AS (
              SELECT step
              FROM path_steps
              ORDER BY
                CASE
                  WHEN normalized_status = 'IN_PROGRESS'
                    OR normalized_status = 'ACTIVE'
                    OR normalized_status LIKE '%%RUNNING%%'
                    OR normalized_status LIKE '%%PROGRESS%%'
                  THEN 0
                  WHEN normalized_status = '' THEN 1
                  WHEN normalized_status IN ('COMPLETED', 'DONE', 'PENDING', 'INACTIVE', 'NOT_STARTED')
                    OR normalized_status LIKE 'NOT_%%'
                    OR normalized_status LIKE '%%INACTIVE%%'
                  THEN 3
                  ELSE 2
                END,
                ordinality
              LIMIT 1
            ),
            stage_context AS (
              SELECT trim(concat_ws(' ',
                astep.step ->> 'stageTitle',
                astep.step ->> 'stageDescription',
                astep.step ->> 'title',
                astep.step ->> 'objective',
                astep.step ->> 'description',
                astep.step ->> 'checkpoint',
                astep.step ->> 'successCriteria',
                (
                  SELECT string_agg(kp.value, ' ')
                  FROM jsonb_array_elements_text(
                    CASE WHEN jsonb_typeof(astep.step -> 'targetKnowledgePoints') = 'array'
                      THEN astep.step -> 'targetKnowledgePoints'
                      ELSE '[]'::jsonb
                    END
                  ) AS kp(value)
                ),
                clp.learning_path ->> 'goal',
                clp.learning_path ->> 'summary',
                clp.learning_path ->> 'summaryText'
              )) AS query_text
              FROM current_learning_path clp
              LEFT JOIN active_step astep ON TRUE
            )
            SELECT query_text
            FROM stage_context
            """,
            new MapSqlParameterSource("userId", userId),
            (rs, rowNum) -> rs.getString("query_text")
        );
        return values.stream()
            .filter(value -> value != null && !value.isBlank())
            .findFirst()
            .orElse("");
    }

    @Scheduled(fixedDelay = 300_000)
    public void evictExpiredEntries() {
        entries.entrySet().removeIf(entry -> entry.getValue().isExpired());
    }

    private int normalizeTopK(int desiredLimit) {
        return Math.max(MIN_TOP_K, Math.min(DEFAULT_TOP_K, desiredLimit));
    }

    private enum WarmupStatus {
        READY,
        FAILED
    }

    private record WarmupEntry(
        String query,
        WarmupStatus status,
        List<UUID> resourceIds,
        Instant expiresAt
    ) {
        static WarmupEntry ready(String query, List<UUID> resourceIds) {
            return new WarmupEntry(query, WarmupStatus.READY, List.copyOf(resourceIds), Instant.now().plus(READY_TTL));
        }

        static WarmupEntry failed(String query, String message) {
            return new WarmupEntry(query, WarmupStatus.FAILED, List.of(), Instant.now().plus(FAILED_TTL));
        }

        boolean isReady() {
            return status == WarmupStatus.READY && !isExpired();
        }

        List<UUID> prefix(int desiredLimit) {
            if (desiredLimit <= 0 || resourceIds.isEmpty()) {
                return List.of();
            }
            return resourceIds.subList(0, Math.min(desiredLimit, resourceIds.size()));
        }

        boolean isExpired() {
            return !Instant.now().isBefore(expiresAt);
        }
    }
}
