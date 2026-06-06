package com.project.application.learningpath;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.learningpath.dto.LearningPathCurrentResponse;
import com.project.api.smartengine.dto.TaskStatusResponse;
import com.project.application.smartengine.TaskStateMachineService;
import com.project.domain.task.ServiceType;
import com.project.domain.task.SmartEngineTask;
import com.project.domain.task.SmartEngineTaskRepository;
import com.project.domain.task.TaskStatus;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

/**
 * 读取当前持久化学习路径，资源计划来自最近一次个性化后台任务。
 */
@Service
public class LearningPathQueryService {

    private static final Duration LIVE_TASK_STALE_AFTER = Duration.ofMinutes(30);

    private static final String CURRENT_PLAN_SQL = """
        SELECT p.id,
               p.user_id,
               p.course_id,
               p.status,
               p.plan_json,
               p.updated_at,
               s.version,
               s.trigger_source,
               s.summary_text
        FROM app.learning_plan p
        LEFT JOIN LATERAL (
            SELECT version, trigger_source, summary_text
            FROM app.learning_plan_snapshot
            WHERE plan_id = p.id
            ORDER BY version DESC, created_at DESC
            LIMIT 1
        ) s ON TRUE
        WHERE p.user_id = :userId
          AND p.status = 'ACTIVE'
        ORDER BY p.updated_at DESC
        LIMIT 1
        """;

    private final NamedParameterJdbcTemplate jdbcTemplate;
    private final SmartEngineTaskRepository taskRepository;
    private final TaskStateMachineService taskStateMachineService;
    private final ObjectMapper objectMapper;

    public LearningPathQueryService(
        NamedParameterJdbcTemplate jdbcTemplate,
        SmartEngineTaskRepository taskRepository,
        TaskStateMachineService taskStateMachineService,
        ObjectMapper objectMapper
    ) {
        this.jdbcTemplate = jdbcTemplate;
        this.taskRepository = taskRepository;
        this.taskStateMachineService = taskStateMachineService;
        this.objectMapper = objectMapper;
    }

    @Transactional(readOnly = true)
    public LearningPathCurrentResponse getCurrent(UUID userId) {
        Optional<PlanRecord> currentPlan = loadCurrentPlan(userId);
        Optional<SmartEngineTask> refreshTask = taskRepository.findFirstByUserIdAndServiceTypeOrderByCreatedAtDesc(
            userId,
            ServiceType.PERSONALIZED_LEARNING
        );
        Optional<SmartEngineTask> resourceRefreshTask = taskRepository.findFirstByUserIdAndServiceTypeOrderByCreatedAtDesc(
            userId,
            ServiceType.RESOURCE_PUSH
        );
        TaskStatusResponse refreshTaskResponse = refreshTask
            .map(task -> taskStatusResponse(task, userId))
            .orElse(null);
        TaskStatusResponse resourceRefreshTaskResponse = resourceRefreshTask
            .map(task -> taskStatusResponse(task, userId))
            .orElse(null);
        Map<String, Object> latestCompletedSummary = taskRepository.findTop5ByUserIdAndServiceTypeOrderByCreatedAtDesc(
                userId,
                ServiceType.PERSONALIZED_LEARNING
            ).stream()
            .filter(task -> task.getTaskStatus() == com.project.domain.task.TaskStatus.COMPLETED)
            .map(task -> safeMap(task.getResponseSummary()))
            .filter(summary -> !summary.isEmpty())
            .findFirst()
            .orElseGet(LinkedHashMap::new);
        Map<String, Object> latestResourceSummary = taskRepository.findTop5ByUserIdAndServiceTypeOrderByCreatedAtDesc(
                userId,
                ServiceType.RESOURCE_PUSH
            ).stream()
            .filter(task -> task.getTaskStatus() == com.project.domain.task.TaskStatus.COMPLETED)
            .map(task -> safeMap(task.getResponseSummary()))
            .filter(summary -> !summary.isEmpty())
            .findFirst()
            .orElseGet(LinkedHashMap::new);
        Map<String, Object> fallbackLearningPath = safeMap(latestCompletedSummary.get("learningPath"));
        Map<String, Object> resourcePushPlan = firstNonEmptyMap(
            latestResourceSummary.get("resourcePushPlan"),
            latestCompletedSummary.get("resourcePushPlan")
        );
        List<Map<String, Object>> pushedResources = firstNonEmptyListOfMaps(
            latestResourceSummary.get("pushedResources"),
            latestCompletedSummary.get("pushedResources")
        );

        return currentPlan
            .map(plan -> {
                Map<String, Object> learningPath = plan.planJson().isEmpty()
                    ? fallbackLearningPath
                    : plan.planJson();
                return new LearningPathCurrentResponse(
                    plan.planId(),
                    plan.userId(),
                    plan.courseId(),
                    plan.status(),
                    learningPath,
                    resolveActiveStep(learningPath),
                    resourcePushPlan,
                    pushedResources,
                    plan.version(),
                    plan.triggerSource(),
                    plan.summary(),
                    plan.updatedAt(),
                    refreshTaskResponse,
                    resourceRefreshTaskResponse
                );
            })
            .orElseGet(() -> new LearningPathCurrentResponse(
                null,
                userId,
                null,
                fallbackLearningPath.isEmpty() ? "EMPTY" : "ACTIVE",
                fallbackLearningPath,
                resolveActiveStep(fallbackLearningPath),
                resourcePushPlan,
                pushedResources,
                null,
                fallbackLearningPath.isEmpty() ? null : "TASK_RESPONSE_FALLBACK",
                readString(latestCompletedSummary.get("summary")),
                null,
                refreshTaskResponse,
                resourceRefreshTaskResponse
            ));
    }

    @Transactional(readOnly = true)
    public Map<String, Object> getCurrentLearningPathPayload(UUID userId) {
        LearningPathCurrentResponse current = getCurrent(userId);
        return safeMap(current.learningPath());
    }

    private Optional<PlanRecord> loadCurrentPlan(UUID userId) {
        List<PlanRecord> records = jdbcTemplate.query(
            CURRENT_PLAN_SQL,
            new MapSqlParameterSource("userId", userId),
            (rs, rowNum) -> mapPlanRecord(rs)
        );
        return records.stream().findFirst();
    }

    private TaskStatusResponse taskStatusResponse(SmartEngineTask task, UUID userId) {
        if (!isStaleLiveTask(task)) {
            return taskStateMachineService.getOwnedTaskStatus(task.getId(), userId);
        }
        return new TaskStatusResponse(
            task.getId(),
            task.getTraceId(),
            task.getServiceType(),
            TaskStatus.TIMEOUT,
            "timeout",
            task.getProgressPercent(),
            "TASK_STALE",
            "后台任务长时间未完成，请重新生成学习路径",
            task.getResponseSummary()
        );
    }

    private boolean isStaleLiveTask(SmartEngineTask task) {
        TaskStatus status = task.getTaskStatus();
        if (status != TaskStatus.PENDING && status != TaskStatus.RUNNING) {
            return false;
        }
        OffsetDateTime createdAt = task.getCreatedAt();
        return createdAt != null && createdAt.isBefore(OffsetDateTime.now().minus(LIVE_TASK_STALE_AFTER));
    }

    private PlanRecord mapPlanRecord(ResultSet rs) throws SQLException {
        return new PlanRecord(
            rs.getObject("id", UUID.class),
            rs.getObject("user_id", UUID.class),
            rs.getObject("course_id", UUID.class),
            rs.getString("status"),
            readMap(rs.getObject("plan_json")),
            rs.getObject("version") == null ? null : rs.getInt("version"),
            rs.getString("trigger_source"),
            rs.getString("summary_text"),
            toOffsetDateTime(rs.getObject("updated_at"))
        );
    }

    private OffsetDateTime toOffsetDateTime(Object value) {
        if (value instanceof OffsetDateTime offsetDateTime) {
            return offsetDateTime;
        }
        if (value instanceof Timestamp timestamp) {
            return timestamp.toInstant().atZone(ZoneId.systemDefault()).toOffsetDateTime();
        }
        return null;
    }

    private static Map<String, Object> safeMap(Object value) {
        if (value instanceof Map<?, ?> rawMap) {
            Map<String, Object> normalized = new LinkedHashMap<>();
            for (Map.Entry<?, ?> entry : rawMap.entrySet()) {
                if (entry.getKey() != null) {
                    normalized.put(String.valueOf(entry.getKey()), entry.getValue());
                }
            }
            return normalized;
        }
        return new LinkedHashMap<>();
    }

    private Map<String, Object> readMap(Object value) {
        Map<String, Object> directMap = safeMap(value);
        if (!directMap.isEmpty() || value instanceof Map<?, ?>) {
            return directMap;
        }
        if (value == null) {
            return new LinkedHashMap<>();
        }
        String rawJson = String.valueOf(value).trim();
        if (rawJson.isEmpty()) {
            return new LinkedHashMap<>();
        }
        try {
            return objectMapper.readValue(rawJson, new TypeReference<LinkedHashMap<String, Object>>() {
            });
        } catch (Exception ex) {
            return new LinkedHashMap<>();
        }
    }

    private static List<Map<String, Object>> safeListOfMaps(Object value) {
        if (!(value instanceof List<?> rawList)) {
            return List.of();
        }
        List<Map<String, Object>> normalized = new ArrayList<>();
        for (Object item : rawList) {
            Map<String, Object> map = safeMap(item);
            if (!map.isEmpty()) {
                normalized.add(map);
            }
        }
        return normalized;
    }

    private static Map<String, Object> firstNonEmptyMap(Object... values) {
        for (Object value : values) {
            Map<String, Object> map = safeMap(value);
            if (!map.isEmpty()) {
                return map;
            }
        }
        return new LinkedHashMap<>();
    }

    private static List<Map<String, Object>> firstNonEmptyListOfMaps(Object... values) {
        for (Object value : values) {
            List<Map<String, Object>> list = safeListOfMaps(value);
            if (!list.isEmpty()) {
                return list;
            }
        }
        return List.of();
    }

    private static Map<String, Object> resolveActiveStep(Map<String, Object> learningPath) {
        List<Map<String, Object>> steps = safeListOfMaps(learningPath.get("steps"));
        List<Map<String, Object>> normalizedSteps = new ArrayList<>();
        boolean hasExplicitStatus = false;
        for (int index = 0; index < steps.size(); index += 1) {
            Map<String, Object> step = new LinkedHashMap<>(steps.get(index));
            step.putIfAbsent("stepId", "step-" + (index + 1));
            normalizedSteps.add(step);
            if (!readString(step.get("status")).isBlank()) {
                hasExplicitStatus = true;
            }
        }
        Optional<Map<String, Object>> explicitActive = normalizedSteps.stream()
            .filter(LearningPathQueryService::isActiveStep)
            .findFirst()
            .map(LinkedHashMap::new);
        if (explicitActive.isPresent()) {
            return explicitActive.get();
        }
        if (!hasExplicitStatus) {
            return normalizedSteps.isEmpty() ? null : new LinkedHashMap<>(normalizedSteps.getFirst());
        }
        return normalizedSteps.stream()
            .filter(step -> !isCompletedStep(step))
            .filter(step -> !isInactiveOrPendingStep(step))
            .findFirst()
            .map(LinkedHashMap::new)
            .orElse(null);
    }

    private static boolean isActiveStep(Map<String, Object> step) {
        String normalized = readString(step.get("status")).toUpperCase()
            .replaceAll("[^A-Z0-9]+", "_")
            .replaceAll("^_+|_+$", "");
        if (normalized.isBlank()
            || normalized.startsWith("NOT_")
            || normalized.contains("INACTIVE")
            || normalized.equals("PENDING")
            || normalized.equals("COMPLETED")
            || normalized.equals("DONE")) {
            return false;
        }
        if (normalized.equals("IN_PROGRESS")) {
            return true;
        }
        return List.of(normalized.split("_+")).stream()
            .anyMatch(token -> token.equals("RUNNING")
                || token.equals("RUN")
                || token.equals("PROGRESS")
                || token.equals("ACTIVE"));
    }

    private static boolean isCompletedStep(Map<String, Object> step) {
        String normalized = normalizeStatus(step.get("status"));
        return normalized.equals("COMPLETED") || normalized.equals("DONE") || normalized.contains("MASTER");
    }

    private static boolean isInactiveOrPendingStep(Map<String, Object> step) {
        String normalized = normalizeStatus(step.get("status"));
        return normalized.startsWith("NOT_")
            || normalized.contains("INACTIVE")
            || normalized.equals("PENDING");
    }

    private static String normalizeStatus(Object value) {
        return readString(value).toUpperCase()
            .replaceAll("[^A-Z0-9]+", "_")
            .replaceAll("^_+|_+$", "");
    }

    private static String readString(Object value) {
        return value instanceof String text ? text.trim() : "";
    }

    private record PlanRecord(
        UUID planId,
        UUID userId,
        UUID courseId,
        String status,
        Map<String, Object> planJson,
        Integer version,
        String triggerSource,
        String summary,
        OffsetDateTime updatedAt
    ) {
    }
}
