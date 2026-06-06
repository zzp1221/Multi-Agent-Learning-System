package com.project.application.resource;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.resource.dto.ResourceDetailResponse;
import com.project.api.resource.dto.ResourceItemResponse;
import com.project.api.resource.dto.ResourceListResponse;
import com.project.api.resource.dto.ResourceProgressRequest;
import com.project.api.resource.dto.ResourceSemanticResultResponse;
import com.project.api.resource.dto.ResourceSemanticSearchResponse;
import com.project.api.resource.dto.ResourceStatsResponse;
import com.project.api.resource.dto.ResourceTagResponse;
import com.project.api.resource.dto.ResourceUserStateResponse;
import com.project.application.common.ApplicationException;
import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.net.URLDecoder;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.nio.charset.StandardCharsets;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;

@Service
public class ResourceLibraryService {

    private static final int DEFAULT_PAGE_SIZE = 12;
    private static final int MAX_PAGE_SIZE = 60;
    private static final TypeReference<List<String>> STRING_LIST = new TypeReference<>() {
    };
    private static final TypeReference<Map<String, Object>> STRING_OBJECT_MAP = new TypeReference<>() {
    };

    private final NamedParameterJdbcTemplate jdbcTemplate;
    private final ObjectMapper objectMapper;
    private final ResourceSemanticSearchClient semanticSearchClient;

    public ResourceLibraryService(
        NamedParameterJdbcTemplate jdbcTemplate,
        ObjectMapper objectMapper,
        ResourceSemanticSearchClient semanticSearchClient
    ) {
        this.jdbcTemplate = jdbcTemplate;
        this.objectMapper = objectMapper;
        this.semanticSearchClient = semanticSearchClient;
    }

    @Transactional(readOnly = true)
    public ResourceListResponse listResources(
        UUID userId,
        String keyword,
        String type,
        String domain,
        String category,
        String subcategory,
        String difficulty,
        String source,
        Boolean favoriteOnly,
        String sort,
        Integer page,
        Integer size
    ) {
        int safePage = Math.max(0, page == null ? 0 : page);
        int safeSize = Math.max(1, Math.min(MAX_PAGE_SIZE, size == null ? DEFAULT_PAGE_SIZE : size));
        MapSqlParameterSource params = baseParams(userId)
            .addValue("limit", safeSize)
            .addValue("offset", safePage * safeSize);

        List<String> conditions = resourceConditions(keyword, type, domain, category, subcategory, difficulty, source, favoriteOnly, params);
        String whereClause = " WHERE " + String.join(" AND ", conditions);
        String dataSql = resourceSelectSql() + whereClause + "\n" + orderByClause(sort) + "\nLIMIT :limit OFFSET :offset";
        String countSql = """
            SELECT COUNT(*)
            FROM app.learning_resource lr
            LEFT JOIN app.user_resource_state urs ON urs.resource_id = lr.id AND urs.user_id = :userId
            """ + whereClause;

        List<ResourceItemResponse> items = jdbcTemplate.query(dataSql, params, resourceRowMapper());
        Long total = jdbcTemplate.queryForObject(countSql, params, Long.class);
        return new ResourceListResponse(items, total == null ? 0 : total, safePage, safeSize);
    }

    @Transactional(readOnly = true)
    public ResourceDetailResponse getResource(UUID userId, UUID resourceId) {
        ResourceItemResponse resource = findResource(userId, resourceId);
        MapSqlParameterSource params = baseParams(userId).addValue("resourceId", resourceId);
        Integer chunkCount = jdbcTemplate.queryForObject(
            "SELECT COUNT(*) FROM rag.resource_chunk WHERE resource_id = :resourceId",
            params,
            Integer.class
        );
        List<String> previewChunks = jdbcTemplate.query(
            """
            SELECT content
            FROM rag.resource_chunk
            WHERE resource_id = :resourceId
            ORDER BY chunk_no
            LIMIT 3
            """,
            params,
            (rs, rowNum) -> rs.getString("content")
        );
        return new ResourceDetailResponse(resource, chunkCount != null && chunkCount > 0, chunkCount == null ? 0 : chunkCount, previewChunks);
    }

    @Transactional
    public ResourceUserStateResponse setFavorite(UUID userId, UUID resourceId, boolean favorite) {
        ensureReadableResource(userId, resourceId);
        jdbcTemplate.update(
            """
            INSERT INTO app.user_resource_state(user_id, resource_id, is_favorite)
            VALUES (:userId, :resourceId, :favorite)
            ON CONFLICT (user_id, resource_id) DO UPDATE SET
              is_favorite = EXCLUDED.is_favorite,
              updated_at = now()
            """,
            baseParams(userId).addValue("resourceId", resourceId).addValue("favorite", favorite)
        );
        return getState(userId, resourceId);
    }

    @Transactional
    public ResourceUserStateResponse updateProgress(UUID userId, UUID resourceId, ResourceProgressRequest request) {
        ensureReadableResource(userId, resourceId);
        int progress = normalizeProgress(request == null ? null : request.progress());
        boolean completed = Boolean.TRUE.equals(request == null ? null : request.completed()) || progress >= 100;
        if (completed) {
            progress = 100;
        }
        jdbcTemplate.update(
            """
            INSERT INTO app.user_resource_state(user_id, resource_id, progress_percent, completed, last_study_at)
            VALUES (:userId, :resourceId, :progress, :completed, now())
            ON CONFLICT (user_id, resource_id) DO UPDATE SET
              progress_percent = EXCLUDED.progress_percent,
              completed = EXCLUDED.completed,
              last_study_at = now(),
              updated_at = now()
            """,
            baseParams(userId)
                .addValue("resourceId", resourceId)
                .addValue("progress", progress)
                .addValue("completed", completed)
        );
        return getState(userId, resourceId);
    }

    @Transactional(readOnly = true)
    public ResourceSemanticSearchResponse semanticSearch(UUID userId, String query, Integer topK) {
        String normalizedQuery = query == null ? "" : query.trim();
        if (normalizedQuery.isBlank()) {
            throw new ApplicationException("INVALID_QUERY", "搜索内容不能为空", HttpStatus.BAD_REQUEST);
        }
        int limit = Math.max(1, Math.min(20, topK == null ? 8 : topK));
        try {
            ResourceSemanticSearchResponse raw = semanticSearchClient.search(userId, normalizedQuery, limit);
            List<ResourceSemanticResultResponse> hydrated = raw.results().stream()
                .map(result -> new ResourceSemanticResultResponse(
                    result.resourceId(),
                    findResourceOrNull(userId, result.resourceId()),
                    result.score(),
                    result.reason(),
                    result.hits()
                ))
                .filter(result -> result.resource() != null)
                .toList();
            return new ResourceSemanticSearchResponse(raw.query(), raw.available(), raw.message(), hydrated);
        } catch (RuntimeException ex) {
            return new ResourceSemanticSearchResponse(
                normalizedQuery,
                false,
                "语义搜索暂不可用：" + ex.getMessage(),
                List.of()
            );
        }
    }

    @Transactional(readOnly = true)
    public List<ResourceItemResponse> recommendations(UUID userId, Integer limit) {
        int safeLimit = Math.max(1, Math.min(20, limit == null ? 6 : limit));
        MapSqlParameterSource params = baseParams(userId).addValue("limit", safeLimit);
        String recommendationScore = recommendationScoreSql();
        return jdbcTemplate.query(
            recommendationContextCtesSql() + """
            , scored_resources AS (
            """ + resourceSelectSql("""
              , """ + recommendationScore + """
               AS recommendation_score
              , ROW_NUMBER() OVER (
                  PARTITION BY upper(COALESCE(NULLIF(lr.metadata_json ->> 'csCategory', ''), 'GENERAL_CS'))
                  ORDER BY
                    """ + recommendationScore + """
                     DESC,
                    lr.updated_at DESC
                ) AS category_rank
            """) + """
              WHERE lr.status = 'ACTIVE'
                AND COALESCE(urs.completed, false) = false
                AND
              """ + readableResourceCondition() + """
            )
            SELECT *
            FROM scored_resources
            ORDER BY
              CASE
                WHEN EXISTS (
                  SELECT 1
                  FROM context_signals cs
                  WHERE cs.preferred_category IS NULL
                ) THEN category_rank
                ELSE 1
              END ASC,
              recommendation_score DESC,
              updated_at DESC
            LIMIT :limit
            """,
            params,
            resourceRowMapper()
        );
    }

    @Transactional(readOnly = true)
    public ResourceStatsResponse stats(UUID userId) {
        MapSqlParameterSource params = baseParams(userId);
        Map<String, Object> row = jdbcTemplate.queryForMap(
            """
            SELECT
              COUNT(lr.id) AS total_resources,
              COUNT(*) FILTER (WHERE COALESCE(urs.is_favorite, false)) AS favorite_resources,
              COUNT(*) FILTER (WHERE COALESCE(urs.progress_percent, 0) > 0) AS started_resources,
              COUNT(*) FILTER (WHERE COALESCE(urs.completed, false)) AS completed_resources,
              COALESCE(AVG(COALESCE(urs.progress_percent, 0)), 0) AS average_progress
            FROM app.learning_resource lr
            LEFT JOIN app.user_resource_state urs ON urs.resource_id = lr.id AND urs.user_id = :userId
            WHERE lr.status = 'ACTIVE'
              AND 
            """ + readableResourceCondition(),
            params
        );
        return new ResourceStatsResponse(
            readLong(row.get("total_resources")),
            readLong(row.get("favorite_resources")),
            readLong(row.get("started_resources")),
            readLong(row.get("completed_resources")),
            readDouble(row.get("average_progress")),
            typeCounts(userId),
            categoryCounts(userId),
            subcategoryCounts(userId),
            tags(userId, 10)
        );
    }

    @Transactional(readOnly = true)
    public List<ResourceTagResponse> tags(UUID userId, Integer limit) {
        int safeLimit = Math.max(1, Math.min(50, limit == null ? 20 : limit));
        return jdbcTemplate.query(
            """
            SELECT tag, COUNT(*) AS count
            FROM app.learning_resource lr
            CROSS JOIN LATERAL jsonb_array_elements_text(
              CASE WHEN jsonb_typeof(lr.tags) = 'array' THEN lr.tags ELSE '[]'::jsonb END
            ) AS tag
            WHERE lr.status = 'ACTIVE'
              AND 
            """ + readableResourceCondition() + """
            GROUP BY tag
            ORDER BY count DESC, tag ASC
            LIMIT :limit
            """,
            baseParams(userId).addValue("limit", safeLimit),
            (rs, rowNum) -> new ResourceTagResponse(rs.getString("tag"), rs.getLong("count"))
        );
    }

    private ResourceUserStateResponse getState(UUID userId, UUID resourceId) {
        try {
            return jdbcTemplate.queryForObject(
                """
                SELECT resource_id,
                       is_favorite,
                       progress_percent,
                       completed,
                       last_study_at
                FROM app.user_resource_state
                WHERE user_id = :userId AND resource_id = :resourceId
                """,
                baseParams(userId).addValue("resourceId", resourceId),
                (rs, rowNum) -> new ResourceUserStateResponse(
                    (UUID) rs.getObject("resource_id"),
                    rs.getBoolean("is_favorite"),
                    rs.getInt("progress_percent"),
                    rs.getBoolean("completed"),
                    readOffsetDateTime(rs, "last_study_at")
                )
            );
        } catch (EmptyResultDataAccessException ex) {
            return new ResourceUserStateResponse(resourceId, false, 0, false, null);
        }
    }

    private ResourceItemResponse findResource(UUID userId, UUID resourceId) {
        ResourceItemResponse resource = findResourceOrNull(userId, resourceId);
        if (resource == null) {
            throw new ApplicationException("RESOURCE_NOT_FOUND", "资源不存在或无权访问", HttpStatus.NOT_FOUND);
        }
        return resource;
    }

    private ResourceItemResponse findResourceOrNull(UUID userId, UUID resourceId) {
        List<ResourceItemResponse> items = jdbcTemplate.query(
            resourceSelectSql() + """
            WHERE lr.id = :resourceId
              AND lr.status = 'ACTIVE'
              AND 
            """ + readableResourceCondition(),
            baseParams(userId).addValue("resourceId", resourceId),
            resourceRowMapper()
        );
        return items.isEmpty() ? null : items.get(0);
    }

    private void ensureReadableResource(UUID userId, UUID resourceId) {
        Boolean exists = jdbcTemplate.queryForObject(
            """
            SELECT EXISTS(
              SELECT 1
              FROM app.learning_resource lr
              WHERE lr.id = :resourceId
                AND lr.status = 'ACTIVE'
                AND 
            """ + readableResourceCondition() + """
            )
            """,
            baseParams(userId).addValue("resourceId", resourceId),
            Boolean.class
        );
        if (!Boolean.TRUE.equals(exists)) {
            throw new ApplicationException("RESOURCE_NOT_FOUND", "资源不存在", HttpStatus.NOT_FOUND);
        }
    }

    private List<String> resourceConditions(
        String keyword,
        String type,
        String domain,
        String category,
        String subcategory,
        String difficulty,
        String source,
        Boolean favoriteOnly,
        MapSqlParameterSource params
    ) {
        List<String> conditions = new ArrayList<>();
        conditions.add("lr.status = 'ACTIVE'");
        conditions.add(readableResourceCondition());
        if (keyword != null && !keyword.isBlank()) {
            conditions.add("(lr.title ILIKE :keyword OR COALESCE(lr.summary_text, '') ILIKE :keyword OR lr.tags::text ILIKE :keyword)");
            params.addValue("keyword", "%" + keyword.trim() + "%");
        }
        ResourceTypeFilter typeFilter = resolveResourceTypeFilter(type);
        if (!typeFilter.resourceTypes().isEmpty() || !typeFilter.displayTypes().isEmpty()) {
            conditions.add("""
                (
                  COALESCE(NULLIF(upper(lr.metadata_json ->> 'displayType'), ''), lr.resource_type::text) IN (:displayTypes)
                  OR lr.resource_type::text IN (:resourceTypes)
                )
                """);
            params.addValue("displayTypes", typeFilter.displayTypes());
            params.addValue("resourceTypes", typeFilter.resourceTypes());
        }
        if (domain != null && !domain.isBlank()) {
            conditions.add("lr.domain = :domain");
            params.addValue("domain", domain.trim());
        }
        if (category != null && !category.isBlank()) {
            conditions.add("upper(COALESCE(lr.metadata_json ->> 'csCategory', 'GENERAL_CS')) = :category");
            params.addValue("category", category.trim().toUpperCase(Locale.ROOT));
        }
        if (subcategory != null && !subcategory.isBlank()) {
            conditions.add("COALESCE(lr.metadata_json ->> 'csSubcategory', '') ILIKE :subcategory");
            params.addValue("subcategory", subcategory.trim());
        }
        if (difficulty != null && !difficulty.isBlank()) {
            conditions.add("lr.difficulty_level::text = :difficulty");
            params.addValue("difficulty", difficulty.trim().toUpperCase(Locale.ROOT));
        }
        if (source != null && !source.isBlank()) {
            conditions.add("(lr.source_kind::text = :source OR COALESCE(lr.metadata_json ->> 'sourceName', '') ILIKE :sourceLike)");
            params.addValue("source", source.trim().toUpperCase(Locale.ROOT));
            params.addValue("sourceLike", "%" + source.trim() + "%");
        }
        if (Boolean.TRUE.equals(favoriteOnly)) {
            conditions.add("COALESCE(urs.is_favorite, false) = true");
        }
        return conditions;
    }

    private String resourceSelectSql() {
        return resourceSelectSql("");
    }

    private String resourceSelectSql(String extraColumns) {
        return """
            SELECT
              lr.id,
              lr.title,
              lr.domain,
              lr.resource_type::text AS resource_type,
              lr.difficulty_level::text AS difficulty_level,
              lr.source_kind::text AS source_kind,
              lr.summary_text,
              lr.tags::text AS tags_json,
              lr.metadata_json::text AS metadata_json,
              lr.created_at,
              lr.updated_at,
              COALESCE(urs.is_favorite, false) AS is_favorite,
              COALESCE(urs.progress_percent, 0) AS progress_percent,
              COALESCE(urs.completed, false) AS completed,
              urs.last_study_at,
              COALESCE(fav.favorite_count, 0) AS favorite_count
            """ + extraColumns + """
            FROM app.learning_resource lr
            LEFT JOIN app.user_resource_state urs ON urs.resource_id = lr.id AND urs.user_id = :userId
            LEFT JOIN (
              SELECT resource_id, COUNT(*) AS favorite_count
              FROM app.user_resource_state
              WHERE is_favorite = true
              GROUP BY resource_id
            ) fav ON fav.resource_id = lr.id
            """;
    }

    private String orderByClause(String sort) {
        String normalized = sort == null ? "" : sort.trim().toLowerCase(Locale.ROOT);
        return switch (normalized) {
            case "latest" -> "ORDER BY lr.updated_at DESC, lr.created_at DESC";
            case "popular", "hot" -> "ORDER BY " + numericMetadataSql("popularityScore", "0") + " DESC, favorite_count DESC, lr.updated_at DESC";
            case "progress" -> "ORDER BY progress_percent DESC, urs.last_study_at DESC NULLS LAST, lr.updated_at DESC";
            case "quality" -> "ORDER BY " + numericMetadataSql("qualityScore", "0.5") + " DESC, lr.updated_at DESC";
            default -> "ORDER BY " + numericMetadataSql("qualityScore", "0.5") + " DESC, " + numericMetadataSql("popularityScore", "0") + " DESC, lr.updated_at DESC";
        };
    }

    private String numericMetadataSql(String key, String fallback) {
        return "COALESCE(CASE WHEN lr.metadata_json ->> '" + key + "' ~ '^-?[0-9]+([.][0-9]+)?$' THEN (lr.metadata_json ->> '" + key + "')::numeric END, " + fallback + ")";
    }

    private String recommendationScoreSql() {
        String displayType = "COALESCE(NULLIF(upper(lr.metadata_json ->> 'displayType'), ''), lr.resource_type::text)";
        String qualityScore = numericMetadataSql("qualityScore", "0.5");
        String popularityScore = numericMetadataSql("popularityScore", "0");
        String profileResourcePreference = normalizedResourcePreferenceSql("up.profile_json ->> 'resourcePreference'");
        String preferredResourceType = normalizedResourcePreferenceSql("preferred_type.value");
        String category = "upper(COALESCE(NULLIF(lr.metadata_json ->> 'csCategory', ''), 'GENERAL_CS'))";
        String haystack = "concat_ws(' ', lr.title, COALESCE(lr.summary_text, ''), lr.tags::text, COALESCE(lr.metadata_json ->> 'csSubcategory', ''), COALESCE(lr.metadata_json ->> 'sourceName', ''))";
        return """
            (
              %s * 0.45
              + %s * 0.20
              + CASE WHEN EXISTS (
                  SELECT 1
                  FROM app.user_profile_current up
                  WHERE up.user_id = :userId
                    AND (
                      %s = %s
                      OR EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements_text(
                          CASE WHEN jsonb_typeof(up.profile_json -> 'preferredResourceTypes') = 'array'
                            THEN up.profile_json -> 'preferredResourceTypes'
                            ELSE '[]'::jsonb
                          END
                        ) AS preferred_type(value)
                        WHERE %s = %s
                      )
                    )
                ) THEN 0.20 ELSE 0 END
              + CASE WHEN EXISTS (
                  SELECT 1
                  FROM app.user_resource_state history_state
                  JOIN app.learning_resource history_lr ON history_lr.id = history_state.resource_id
                  CROSS JOIN LATERAL jsonb_array_elements_text(
                    CASE WHEN jsonb_typeof(history_lr.tags) = 'array' THEN history_lr.tags ELSE '[]'::jsonb END
                  ) AS history_tag(tag)
                  WHERE history_state.user_id = :userId
                    AND (COALESCE(history_state.is_favorite, false) OR COALESCE(history_state.progress_percent, 0) > 0)
                    AND jsonb_exists(lr.tags, history_tag.tag)
                ) THEN 0.12 ELSE 0 END
              + CASE WHEN COALESCE(urs.progress_percent, 0) > 0 THEN 0.03 ELSE 0 END
              + CASE WHEN EXISTS (
                  SELECT 1
                  FROM context_signals cs
                  WHERE cs.preferred_category IS NOT NULL
                    AND %s = cs.preferred_category
                ) THEN 0.95 ELSE 0 END
              + CASE WHEN EXISTS (
                  SELECT 1
                  FROM context_signals cs
                  WHERE cs.preferred_category IS NOT NULL
                    AND %s <> cs.preferred_category
                ) THEN -0.25 ELSE 0 END
              + CASE WHEN EXISTS (
                  SELECT 1
                  FROM context_signals cs
                  WHERE cs.ai_ml_context
                    AND %s ~* '(深度学习|机器学习|神经网络|反向传播|损失函数|优化器|正则化|梯度下降|deep[- ]?learning|machine[- ]?learning|neural|backprop|optimizer|loss|pytorch|tensorflow)'
                ) THEN 0.35 ELSE 0 END
              + CASE WHEN lower(lr.title) LIKE 'redirecting%%'
                  OR lower(COALESCE(lr.summary_text, '')) LIKE 'redirecting%%'
                THEN -0.30 ELSE 0 END
            )
            """.formatted(
            qualityScore,
            popularityScore,
            profileResourcePreference,
            displayType,
            preferredResourceType,
            displayType,
            category,
            category,
            haystack
        );
    }

    private String recommendationContextCtesSql() {
        return """
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
            path_steps AS (
              SELECT step_item.step,
                     step_item.ordinality,
                     upper(regexp_replace(COALESCE(step_item.step ->> 'status', ''), '[^A-Za-z0-9]+', '_', 'g')) AS normalized_status
              FROM current_learning_path clp
              CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(clp.learning_path -> 'steps') = 'array'
                  THEN clp.learning_path -> 'steps'
                  ELSE '[]'::jsonb
                END
              ) WITH ORDINALITY AS step_item(step, ordinality)
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
            profile_context AS (
              SELECT up.profile_json
              FROM app.user_profile_current up
              WHERE up.user_id = :userId
              LIMIT 1
            ),
            learning_context AS (
              SELECT lower(concat_ws(' ',
                clp.learning_path ->> 'goal',
                clp.learning_path ->> 'summary',
                clp.learning_path ->> 'summaryText',
                astep.step ->> 'title',
                astep.step ->> 'objective',
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
                pc.profile_json ->> 'learningGoal',
                (
                  SELECT string_agg(wp.value, ' ')
                  FROM jsonb_array_elements_text(
                    CASE WHEN jsonb_typeof(pc.profile_json -> 'weakPoints') = 'array'
                      THEN pc.profile_json -> 'weakPoints'
                      ELSE '[]'::jsonb
                    END
                  ) AS wp(value)
                ),
                (
                  SELECT string_agg(focus.value ->> 'topic', ' ')
                  FROM jsonb_array_elements(
                    CASE WHEN jsonb_typeof(pc.profile_json -> 'weakPointDetails') = 'array'
                      THEN pc.profile_json -> 'weakPointDetails'
                      ELSE '[]'::jsonb
                    END
                  ) AS focus(value)
                )
              )) AS context_text
              FROM current_learning_path clp
              LEFT JOIN active_step astep ON TRUE
              LEFT JOIN profile_context pc ON TRUE
            ),
            context_signals AS (
              SELECT context_text,
                     CASE
                       WHEN context_text ~* '(深度学习|机器学习|神经网络|反向传播|损失函数|优化器|正则化|梯度下降|人工智能|deep[- ]?learning|machine[- ]?learning|neural|backprop|optimizer|pytorch|tensorflow|ai|ml)' THEN 'AI_ML'
                       WHEN context_text ~* '(前端|网页|浏览器|html|css|javascript|typescript|react|vue|dom|web)' THEN 'FRONTEND_WEB'
                       WHEN context_text ~* '(数据库|索引|事务|sql|mysql|postgres|redis|查询优化)' THEN 'DATABASES'
                       WHEN context_text ~* '(操作系统|进程|线程|内存|linux|kernel|os|调度)' THEN 'OPERATING_SYSTEMS'
                       WHEN context_text ~* '(网络|tcp|udp|http|dns|路由|拥塞|computer network)' THEN 'COMPUTER_NETWORKS'
                       WHEN context_text ~* '(数据结构|算法|动态规划|排序|图论|树|堆|hash|algorithm|graph|tree)' THEN 'DATA_STRUCTURES_ALGORITHMS'
                       WHEN context_text ~* '(编译|词法|语法分析|parser|lexer|compiler)' THEN 'COMPILERS'
                       WHEN context_text ~* '(分布式|云原生|kubernetes|docker|微服务|一致性|consensus)' THEN 'DISTRIBUTED_CLOUD'
                       WHEN context_text ~* '(安全|密码|漏洞|攻防|xss|csrf|sql injection|security)' THEN 'SECURITY'
                       WHEN context_text ~* '(后端|spring|接口|api|rest|并发|线程池|backend)' THEN 'BACKEND_SYSTEMS'
                       WHEN context_text ~* '(数学|线性代数|概率|统计|微积分|math)' THEN 'MATH_FOUNDATIONS'
                       WHEN context_text ~* '(体系结构|计算机组成|cpu|cache|指令|流水线|architecture)' THEN 'COMPUTER_ARCHITECTURE'
                       WHEN context_text ~* '(软件工程|需求|测试|设计模式|重构|software engineering)' THEN 'SOFTWARE_ENGINEERING'
                       WHEN context_text ~* '(开发工具|git|ci|debug|调试|devops)' THEN 'DEV_TOOLS'
                       WHEN context_text ~* '(编程语言|python|java|c\\+\\+|rust|golang|go language)' THEN 'PROGRAMMING_LANGUAGES'
                       ELSE NULL
                     END AS preferred_category,
                     context_text ~* '(深度学习|机器学习|神经网络|反向传播|损失函数|优化器|正则化|梯度下降|deep[- ]?learning|machine[- ]?learning|neural|backprop|optimizer|pytorch|tensorflow)' AS ai_ml_context
              FROM learning_context
            )
            """;
    }

    private String normalizedResourcePreferenceSql(String valueSql) {
        return """
            CASE upper(%s)
              WHEN 'CODE_CASE' THEN 'CASE'
              WHEN 'PRACTICAL_CASE' THEN 'CASE'
              WHEN 'EXERCISE' THEN 'QUIZ'
              WHEN 'QUESTION' THEN 'QUIZ'
              WHEN 'QUESTIONS' THEN 'QUIZ'
              WHEN 'MINDMAP' THEN 'NOTE'
              ELSE upper(%s)
            END
            """.formatted(valueSql, valueSql);
    }

    private Map<String, Long> typeCounts(UUID userId) {
        List<Map<String, Object>> rows = jdbcTemplate.queryForList(
            """
            SELECT COALESCE(NULLIF(upper(lr.metadata_json ->> 'displayType'), ''), lr.resource_type::text) AS display_type,
                   COUNT(*) AS count
            FROM app.learning_resource lr
            WHERE lr.status = 'ACTIVE'
              AND 
            """ + readableResourceCondition() + """
            GROUP BY display_type
            ORDER BY count DESC
            """,
            baseParams(userId)
        );
        Map<String, Long> counts = new LinkedHashMap<>();
        for (Map<String, Object> row : rows) {
            counts.put(String.valueOf(row.get("display_type")), readLong(row.get("count")));
        }
        return counts;
    }

    private Map<String, Long> categoryCounts(UUID userId) {
        return metadataCounts(userId, "csCategory", "GENERAL_CS", true);
    }

    private Map<String, Long> subcategoryCounts(UUID userId) {
        return metadataCounts(userId, "csSubcategory", "General", false);
    }

    private Map<String, Long> metadataCounts(UUID userId, String key, String fallback, boolean uppercase) {
        String valueSql = uppercase
            ? "upper(COALESCE(NULLIF(lr.metadata_json ->> '" + key + "', ''), '" + fallback + "'))"
            : "COALESCE(NULLIF(lr.metadata_json ->> '" + key + "', ''), '" + fallback + "')";
        List<Map<String, Object>> rows = jdbcTemplate.queryForList(
            """
            SELECT %s AS metadata_value,
                   COUNT(*) AS count
            FROM app.learning_resource lr
            WHERE lr.status = 'ACTIVE'
              AND 
            """.formatted(valueSql) + readableResourceCondition() + """
            GROUP BY metadata_value
            ORDER BY count DESC, metadata_value ASC
            """,
            baseParams(userId)
        );
        Map<String, Long> counts = new LinkedHashMap<>();
        for (Map<String, Object> row : rows) {
            counts.put(String.valueOf(row.get("metadata_value")), readLong(row.get("count")));
        }
        return counts;
    }

    private String readableResourceCondition() {
        return """
            (
              lr.access_scope::text = 'GLOBAL'
              OR (lr.access_scope::text = 'USER' AND lr.owner_user_id = :userId)
              OR (
                lr.access_scope::text = 'COURSE'
                AND EXISTS (
                  SELECT 1
                  FROM app.user_course_enrollments e
                  WHERE e.user_id = :userId AND e.course_id = lr.course_id
                )
              )
            )
            """;
    }

    private RowMapper<ResourceItemResponse> resourceRowMapper() {
        return (rs, rowNum) -> {
            Map<String, Object> metadata = parseObject(rs.getString("metadata_json"));
            List<String> tags = parseStringList(rs.getString("tags_json"));
            String resourceType = rs.getString("resource_type");
            return new ResourceItemResponse(
                (UUID) rs.getObject("id"),
                displayTitle(rs.getString("title"), metadata),
                rs.getString("domain"),
                resourceType,
                displayType(resourceType, metadata),
                rs.getString("difficulty_level"),
                rs.getString("source_kind"),
                rs.getString("summary_text"),
                tags,
                readString(metadata, "sourceUrl", "sourceURL", "url"),
                readString(metadata, "sourceName", "provider", "siteName"),
                readString(metadata, "coverUrl", "coverURL", "thumbnailUrl"),
                readString(metadata, "license"),
                readString(metadata, "copyrightStatus"),
                readString(metadata, "accessibilityStatus"),
                readInteger(metadata, "httpStatus"),
                readOffsetDateTime(metadata, "lastCheckedAt"),
                readDoubleOrNull(metadata, "qualityScore"),
                readDoubleOrNull(metadata, "popularityScore"),
                rs.getLong("favorite_count"),
                readLongOrNull(metadata, "viewCount"),
                readLongOrNull(metadata, "likeCount"),
                readInteger(metadata, "durationMinutes"),
                readLongOrNull(metadata, "fileSizeBytes"),
                rs.getBoolean("is_favorite"),
                rs.getInt("progress_percent"),
                rs.getBoolean("completed"),
                readOffsetDateTime(rs, "last_study_at"),
                readOffsetDateTime(rs, "created_at"),
                readOffsetDateTime(rs, "updated_at"),
                readString(metadata, "csCategory"),
                readString(metadata, "csSubcategory"),
                metadata
            );
        };
    }

    private MapSqlParameterSource baseParams(UUID userId) {
        return new MapSqlParameterSource("userId", userId);
    }

    private int normalizeProgress(Integer progress) {
        if (progress == null) {
            return 0;
        }
        return Math.max(0, Math.min(100, progress));
    }

    private ResourceTypeFilter resolveResourceTypeFilter(String type) {
        if (type == null || type.isBlank() || "ALL".equalsIgnoreCase(type)) {
            return new ResourceTypeFilter(List.of(), List.of());
        }
        String normalized = type.trim().toUpperCase(Locale.ROOT);
        return switch (normalized) {
            case "COURSE" -> new ResourceTypeFilter(List.of("READING", "SLIDES", "PPT", "DOCUMENT"), List.of("COURSE"));
            case "CASE", "PRACTICAL_CASE", "CODE_CASE" -> new ResourceTypeFilter(List.of("CODE", "PRACTICE"), List.of("CASE"));
            case "NOTE", "NOTES" -> new ResourceTypeFilter(List.of("MINDMAP"), List.of("NOTE"));
            case "EXERCISE", "QUESTION", "QUESTIONS" -> new ResourceTypeFilter(List.of("QUIZ", "PRACTICE"), List.of("QUIZ"));
            default -> new ResourceTypeFilter(List.of(normalized), List.of(normalized));
        };
    }

    private record ResourceTypeFilter(List<String> resourceTypes, List<String> displayTypes) {
    }

    private String displayType(String resourceType, Map<String, Object> metadata) {
        String displayType = readString(metadata, "displayType");
        if (displayType != null && !displayType.isBlank()) {
            return displayType;
        }
        return switch (resourceType == null ? "" : resourceType) {
            case "VIDEO" -> "VIDEO";
            case "QUIZ", "PRACTICE" -> "QUIZ";
            case "CODE" -> "CASE";
            case "MINDMAP" -> "NOTE";
            case "SLIDES", "PPT" -> "COURSE";
            default -> "DOCUMENT";
        };
    }

    private String displayTitle(String rawTitle, Map<String, Object> metadata) {
        String title = rawTitle == null ? "" : rawTitle.trim();
        if (!isPlaceholderTitle(title)) {
            return title;
        }
        String metadataTitle = readString(metadata, "title", "sourceTitle", "pageTitle");
        if (metadataTitle != null && !isPlaceholderTitle(metadataTitle)) {
            return metadataTitle;
        }
        String derivedTitle = deriveTitleFromUrl(readString(metadata, "sourceUrl", "originalUrl", "url"));
        if (derivedTitle == null || derivedTitle.isBlank()) {
            return title.isBlank() ? "Learning resource" : title;
        }
        String sourceName = readString(metadata, "sourceName");
        if (sourceName != null && sourceName.toLowerCase(Locale.ROOT).contains("pytorch")
            && !derivedTitle.toLowerCase(Locale.ROOT).contains("pytorch")) {
            return "PyTorch: " + derivedTitle;
        }
        return derivedTitle;
    }

    private boolean isPlaceholderTitle(String title) {
        if (title == null || title.isBlank()) {
            return true;
        }
        String normalized = title.trim().toLowerCase(Locale.ROOT);
        return normalized.equals("redirecting...")
            || normalized.equals("redirecting…")
            || normalized.equals("untitled resource");
    }

    private String deriveTitleFromUrl(String url) {
        if (url == null || url.isBlank()) {
            return null;
        }
        try {
            String path = java.net.URI.create(url.trim()).getPath();
            if (path == null || path.isBlank()) {
                return null;
            }
            String segment = path.substring(path.lastIndexOf('/') + 1);
            if (segment.isBlank()) {
                return null;
            }
            int dotIndex = segment.lastIndexOf('.');
            if (dotIndex > 0) {
                segment = segment.substring(0, dotIndex);
            }
            String decoded = URLDecoder.decode(segment, StandardCharsets.UTF_8);
            return decoded.replace('-', ' ').replace('_', ' ').trim();
        } catch (IllegalArgumentException ex) {
            return null;
        }
    }

    private List<String> parseStringList(String rawJson) {
        if (rawJson == null || rawJson.isBlank()) {
            return List.of();
        }
        try {
            return objectMapper.readValue(rawJson, STRING_LIST);
        } catch (JsonProcessingException ex) {
            return List.of();
        }
    }

    private Map<String, Object> parseObject(String rawJson) {
        if (rawJson == null || rawJson.isBlank()) {
            return Map.of();
        }
        try {
            return objectMapper.readValue(rawJson, STRING_OBJECT_MAP);
        } catch (JsonProcessingException ex) {
            return Map.of();
        }
    }

    private String readString(Map<String, Object> map, String... keys) {
        for (String key : keys) {
            Object value = map.get(key);
            if (value != null && !String.valueOf(value).isBlank()) {
                return String.valueOf(value).trim();
            }
        }
        return null;
    }

    private Integer readInteger(Map<String, Object> map, String key) {
        Object value = map.get(key);
        if (value instanceof Number number) {
            return number.intValue();
        }
        if (value instanceof String text && !text.isBlank()) {
            try {
                return Integer.parseInt(text.trim());
            } catch (NumberFormatException ex) {
                return null;
            }
        }
        return null;
    }

    private Long readLongOrNull(Map<String, Object> map, String key) {
        Object value = map.get(key);
        if (value instanceof Number number) {
            return number.longValue();
        }
        if (value instanceof String text && !text.isBlank()) {
            try {
                return Long.parseLong(text.trim());
            } catch (NumberFormatException ex) {
                return null;
            }
        }
        return null;
    }

    private Double readDoubleOrNull(Map<String, Object> map, String key) {
        Object value = map.get(key);
        if (value instanceof Number number) {
            return number.doubleValue();
        }
        if (value instanceof String text && !text.isBlank()) {
            try {
                return Double.parseDouble(text.trim());
            } catch (NumberFormatException ex) {
                return null;
            }
        }
        return null;
    }

    private double readDouble(Object value) {
        if (value instanceof Number number) {
            return number.doubleValue();
        }
        if (value != null) {
            try {
                return Double.parseDouble(String.valueOf(value));
            } catch (NumberFormatException ex) {
                return 0;
            }
        }
        return 0;
    }

    private long readLong(Object value) {
        if (value instanceof Number number) {
            return number.longValue();
        }
        if (value != null) {
            try {
                return Long.parseLong(String.valueOf(value));
            } catch (NumberFormatException ex) {
                return 0;
            }
        }
        return 0;
    }

    private OffsetDateTime readOffsetDateTime(Map<String, Object> map, String key) {
        Object value = map.get(key);
        if (value == null || String.valueOf(value).isBlank()) {
            return null;
        }
        try {
            return OffsetDateTime.parse(String.valueOf(value));
        } catch (RuntimeException ex) {
            return null;
        }
    }

    private OffsetDateTime readOffsetDateTime(ResultSet rs, String column) throws SQLException {
        Object value = rs.getObject(column);
        if (value == null) {
            return null;
        }
        if (value instanceof OffsetDateTime offsetDateTime) {
            return offsetDateTime;
        }
        if (value instanceof Timestamp timestamp) {
            return timestamp.toInstant().atOffset(ZoneOffset.UTC);
        }
        return OffsetDateTime.parse(String.valueOf(value));
    }
}
