package com.project.application.resource;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.resource.dto.ResourceExternalCandidateResponse;
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
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

@Service
public class ResourceLibraryService {

    private static final int DEFAULT_PAGE_SIZE = 12;
    private static final int MAX_PAGE_SIZE = 60;
    private static final UUID UUID_NAMESPACE_URL = UUID.fromString("6ba7b811-9dad-11d1-80b4-00c04fd430c8");
    private static final UUID EXTERNAL_RESOURCE_NAMESPACE = uuid5(UUID_NAMESPACE_URL, "zhixue-ai-resource-library");
    private static final TypeReference<List<String>> STRING_LIST = new TypeReference<>() {
    };
    private static final TypeReference<Map<String, Object>> STRING_OBJECT_MAP = new TypeReference<>() {
    };

    private final NamedParameterJdbcTemplate jdbcTemplate;
    private final ObjectMapper objectMapper;
    private final ResourceSemanticSearchClient semanticSearchClient;
    private final ResourceSemanticWarmupService semanticWarmupService;

    public ResourceLibraryService(
        NamedParameterJdbcTemplate jdbcTemplate,
        ObjectMapper objectMapper,
        ResourceSemanticSearchClient semanticSearchClient,
        ResourceSemanticWarmupService semanticWarmupService
    ) {
        this.jdbcTemplate = jdbcTemplate;
        this.objectMapper = objectMapper;
        this.semanticSearchClient = semanticSearchClient;
        this.semanticWarmupService = semanticWarmupService;
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
        boolean stageRecommendedSort = isStageRecommendedSort(sort);
        List<UUID> stageRankedIds = stageRecommendedSort
            ? semanticWarmupService.stageRankedIds(userId, safeSize + safePage * safeSize)
            : List.of();
        bindStageRankedIds(params, stageRankedIds);
        String dataSql = listResourceSelectSql(sort, whereClause, stageRankedIds.size());
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

    @Transactional
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
                    hydrateSemanticResource(userId, result),
                    result.score(),
                    result.reason(),
                    result.hits(),
                    result.externalResource()
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
        List<UUID> rankedIds = semanticWarmupService.recommendationIds(userId, safeLimit);
        if (!rankedIds.isEmpty()) {
            List<ResourceItemResponse> warmRecommendations = resourcesByIds(userId, rankedIds, safeLimit, true);
            if (warmRecommendations.size() >= safeLimit) {
                return warmRecommendations;
            }
            List<ResourceItemResponse> fallback = fallbackRecommendations(userId, safeLimit, resourceIds(warmRecommendations));
            return mergeResources(warmRecommendations, fallback, safeLimit);
        }
        return fallbackRecommendations(userId, safeLimit, Set.of());
    }

    private List<ResourceItemResponse> resourcesByIds(UUID userId, List<UUID> resourceIds, int limit, boolean incompleteOnly) {
        if (resourceIds.isEmpty()) {
            return List.of();
        }
        MapSqlParameterSource params = baseParams(userId)
            .addValue("limit", limit);
        bindStageRankedIds(params, resourceIds);
        String completedCondition = incompleteOnly ? "AND COALESCE(urs.completed, false) = false\n" : "";
        return jdbcTemplate.query(
            """
            WITH ranked AS (
              SELECT resource_id, stage_rank
              FROM (VALUES
            """ + stageRankedValuesSql(resourceIds.size()) + """
              ) AS ranked(resource_id, stage_rank)
            )
            """ + resourceSelectSql(", ranked.stage_rank AS semantic_rank\n") + """
            JOIN ranked ON ranked.resource_id = lr.id
            WHERE lr.status = 'ACTIVE'
              """ + completedCondition + """
              AND
            """ + visibleResourceCondition() + """
              AND
            """ + readableResourceCondition() + """
            ORDER BY semantic_rank ASC
            LIMIT :limit
            """,
            params,
            resourceRowMapper()
        );
    }

    private ResourceItemResponse hydrateSemanticResource(UUID userId, ResourceSemanticResultResponse result) {
        ResourceItemResponse existing = findResourceOrNull(userId, result.resourceId());
        if (existing != null) {
            return existing;
        }
        ResourceExternalCandidateResponse externalResource = result.externalResource();
        if (externalResource == null || externalResource.sourceUrl() == null || externalResource.sourceUrl().isBlank()) {
            return null;
        }
        UUID resourceId = upsertExternalResource(externalResource);
        return findResourceOrNull(userId, resourceId);
    }

    private UUID upsertExternalResource(ResourceExternalCandidateResponse externalResource) {
        String sourceUrl = normalizeUrl(externalResource.sourceUrl());
        UUID resourceId = resourceUuid(sourceUrl);
        String resourceType = normalizeExternalResourceType(externalResource.resourceType(), externalResource.displayType());
        String displayType = normalizeDisplayType(externalResource.displayType(), resourceType);
        String difficultyLevel = normalizeDifficulty(externalResource.difficultyLevel());
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("sourceUrl", sourceUrl);
        metadata.put("originalUrl", sourceUrl);
        metadata.put("sourceName", safeString(externalResource.sourceName()));
        metadata.put("displayType", displayType);
        metadata.put("accessibilityStatus", "ACCESSIBLE");
        metadata.put("copyrightStatus", "LINK_ONLY");
        metadata.put("license", "");
        metadata.put("qualityScore", clampScore(externalResource.qualityScore(), 0.6));
        metadata.put("popularityScore", clampScore(externalResource.popularityScore(), 0.0));
        metadata.put("coverUrl", safeString(externalResource.coverUrl()));
        metadata.put("discoveredBy", "tavily");
        metadata.put("ingestedBy", "resource_library_tavily_fallback");
        metadata.put("ragReady", false);
        metadata.put("ragStatus", "METADATA_ONLY");

        jdbcTemplate.update(
            """
            INSERT INTO app.learning_resource (
              id, title, domain, resource_type, difficulty_level, source_kind,
              access_scope, summary_text, tags, metadata_json, status
            )
            VALUES (
              :resourceId, :title, 'COMPUTER_SCIENCE', :resourceType::app.resource_type,
              :difficultyLevel::app.difficulty_level, 'WEB'::app.source_kind,
              'GLOBAL'::app.access_scope, :summaryText, :tags::jsonb, :metadata::jsonb, 'ACTIVE'
            )
            ON CONFLICT (id) DO UPDATE SET
              title = EXCLUDED.title,
              domain = EXCLUDED.domain,
              resource_type = EXCLUDED.resource_type,
              difficulty_level = EXCLUDED.difficulty_level,
              source_kind = EXCLUDED.source_kind,
              access_scope = EXCLUDED.access_scope,
              summary_text = EXCLUDED.summary_text,
              tags = EXCLUDED.tags,
              metadata_json = app.learning_resource.metadata_json || EXCLUDED.metadata_json,
              status = 'ACTIVE',
              updated_at = now()
            """,
            new MapSqlParameterSource()
                .addValue("resourceId", resourceId)
                .addValue("title", safeTitle(externalResource.title(), sourceUrl))
                .addValue("resourceType", resourceType)
                .addValue("difficultyLevel", difficultyLevel)
                .addValue("summaryText", safeString(externalResource.summaryText()))
                .addValue("tags", toJson(externalResource.tags() == null ? List.of() : externalResource.tags()))
                .addValue("metadata", toJson(metadata))
        );
        return resourceId;
    }

    private List<ResourceItemResponse> fallbackRecommendations(UUID userId, int limit, Set<UUID> excludedIds) {
        MapSqlParameterSource params = baseParams(userId)
            .addValue("limit", limit)
            .addValue("excludedIds", excludedIds.isEmpty() ? List.of(UUID.randomUUID()) : List.copyOf(excludedIds))
            .addValue("excludedIdsEmpty", excludedIds.isEmpty());
        String recommendationScore = recommendationScoreSql();
        return jdbcTemplate.query(
            """
            WITH scored_resources AS (
            """ + resourceSelectSql("""
              , """ + recommendationScore + """
               AS recommendation_score
            """) + """
              WHERE lr.status = 'ACTIVE'
                AND (:excludedIdsEmpty = true OR lr.id NOT IN (:excludedIds))
                AND COALESCE(urs.completed, false) = false
                AND
              """ + visibleResourceCondition() + """
                AND
              """ + readableResourceCondition() + """
            )
            SELECT *
            FROM scored_resources
            ORDER BY
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
              """ + visibleResourceCondition() + """
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
              """ + visibleResourceCondition() + """
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
            """ + visibleResourceCondition() + """
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
            """ + visibleResourceCondition() + """
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
        conditions.add(visibleResourceCondition());
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

    private String listResourceSelectSql(String sort, String whereClause, int stageRankedIdCount) {
        String normalized = sort == null ? "" : sort.trim().toLowerCase(Locale.ROOT);
        if (!normalized.isBlank()
            && !normalized.equals("comprehensive")
            && !normalized.equals("recommended")
            && !normalized.equals("recommendation")) {
            return resourceSelectSql() + whereClause + "\n" + orderByClause(sort) + "\nLIMIT :limit OFFSET :offset";
        }
        String scoreSql = recommendationScoreSql();
        String stageRankCte = stageRankedIdCount > 0 ? """
            WITH stage_ranked AS (
              SELECT resource_id, stage_rank
              FROM (VALUES
            """ + stageRankedValuesSql(stageRankedIdCount) + """
              ) AS ranked(resource_id, stage_rank)
            ),
            """ : """
            WITH
            """;
        String stageRankColumn = stageRankedIdCount > 0
            ? ", sr.stage_rank\n"
            : ", NULL::int AS stage_rank\n";
        String stageRankJoin = stageRankedIdCount > 0
            ? "LEFT JOIN stage_ranked sr ON sr.resource_id = lr.id\n"
            : "";
        return stageRankCte + """
            scored_resources AS (
            """ + resourceSelectSql("""
              , """ + scoreSql + """
               AS recommendation_score
            """ + stageRankColumn) + stageRankJoin + whereClause + """
            )
            SELECT *
            FROM scored_resources
            ORDER BY
              stage_rank ASC NULLS LAST,
              recommendation_score DESC,
              updated_at DESC
            LIMIT :limit OFFSET :offset
            """;
    }

    private void bindStageRankedIds(MapSqlParameterSource params, List<UUID> stageRankedIds) {
        for (int index = 0; index < stageRankedIds.size(); index += 1) {
            params.addValue("stageRankedId" + index, stageRankedIds.get(index));
        }
    }

    private String stageRankedValuesSql(int stageRankedIdCount) {
        StringBuilder values = new StringBuilder();
        for (int index = 0; index < stageRankedIdCount; index += 1) {
            if (index > 0) {
                values.append(",\n");
            }
            values.append("                (CAST(:stageRankedId")
                .append(index)
                .append(" AS uuid), ")
                .append(index)
                .append(")");
        }
        return values.toString();
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

    private String visibleResourceCondition() {
        return """
            (
              (
                COALESCE(NULLIF(upper(lr.metadata_json ->> 'displayType'), ''), lr.resource_type::text) = 'NOTE'
                AND lr.access_scope::text = 'USER'
                AND lr.owner_user_id = :userId
                AND COALESCE(lr.metadata_json ->> 'noteId', '') <> ''
              )
              OR (
                lr.access_scope::text = 'GLOBAL'
                AND COALESCE(NULLIF(upper(lr.metadata_json ->> 'displayType'), ''), lr.resource_type::text) <> 'NOTE'
                AND lr.resource_type::text NOT IN ('QUIZ', 'PRACTICE')
                AND COALESCE(NULLIF(upper(lr.metadata_json ->> 'displayType'), ''), lr.resource_type::text) NOT IN ('QUIZ', 'PRACTICE')
                AND COALESCE(lr.metadata_json ->> 'sourceUrl', '') ~* '^https?://'
                AND COALESCE(lr.metadata_json ->> 'accessibilityStatus', '') = 'ACCESSIBLE'
              )
            )
            """;
    }

    private String recommendationScoreSql() {
        String displayType = "COALESCE(NULLIF(upper(lr.metadata_json ->> 'displayType'), ''), lr.resource_type::text)";
        String qualityScore = numericMetadataSql("qualityScore", "0.5");
        String popularityScore = numericMetadataSql("popularityScore", "0");
        String profileResourcePreference = normalizedResourcePreferenceSql("up.profile_json ->> 'resourcePreference'");
        String preferredResourceType = normalizedResourcePreferenceSql("preferred_type.value");
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
            displayType
        );
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
            """ + visibleResourceCondition() + """
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
            """.formatted(valueSql) + visibleResourceCondition() + """
              AND
            """ + readableResourceCondition() + """
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

    private boolean isStageRecommendedSort(String sort) {
        String normalized = sort == null ? "" : sort.trim().toLowerCase(Locale.ROOT);
        return normalized.isBlank()
            || normalized.equals("comprehensive")
            || normalized.equals("recommended")
            || normalized.equals("recommendation");
    }

    private List<ResourceItemResponse> mergeResources(
        List<ResourceItemResponse> primary,
        List<ResourceItemResponse> secondary,
        int limit
    ) {
        List<ResourceItemResponse> merged = new ArrayList<>();
        Set<UUID> seen = new LinkedHashSet<>();
        for (ResourceItemResponse resource : primary) {
            if (resource != null && seen.add(resource.id())) {
                merged.add(resource);
            }
            if (merged.size() >= limit) {
                return merged;
            }
        }
        for (ResourceItemResponse resource : secondary) {
            if (resource != null && seen.add(resource.id())) {
                merged.add(resource);
            }
            if (merged.size() >= limit) {
                break;
            }
        }
        return merged;
    }

    private Set<UUID> resourceIds(List<ResourceItemResponse> resources) {
        Set<UUID> ids = new LinkedHashSet<>();
        for (ResourceItemResponse resource : resources) {
            if (resource != null) {
                ids.add(resource.id());
            }
        }
        return ids;
    }

    private UUID resourceUuid(String sourceUrl) {
        return uuid5(EXTERNAL_RESOURCE_NAMESPACE, "resource:" + sourceUrl);
    }

    private static UUID uuid5(UUID namespace, String name) {
        try {
            MessageDigest sha1 = MessageDigest.getInstance("SHA-1");
            ByteBuffer namespaceBytes = ByteBuffer.allocate(16);
            namespaceBytes.putLong(namespace.getMostSignificantBits());
            namespaceBytes.putLong(namespace.getLeastSignificantBits());
            sha1.update(namespaceBytes.array());
            byte[] hash = sha1.digest(name.getBytes(StandardCharsets.UTF_8));
            hash[6] &= 0x0f;
            hash[6] |= 0x50;
            hash[8] &= 0x3f;
            hash[8] |= (byte) 0x80;
            ByteBuffer buffer = ByteBuffer.wrap(hash, 0, 16);
            return new UUID(buffer.getLong(), buffer.getLong());
        } catch (NoSuchAlgorithmException ex) {
            throw new IllegalStateException("SHA-1 digest is unavailable", ex);
        }
    }

    private String toJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException ex) {
            return "{}";
        }
    }

    private String normalizeUrl(String sourceUrl) {
        String normalized = safeString(sourceUrl);
        while (normalized.endsWith("/")) {
            normalized = normalized.substring(0, normalized.length() - 1);
        }
        return normalized;
    }

    private String normalizeExternalResourceType(String resourceType, String displayType) {
        String normalized = safeString(resourceType).toUpperCase(Locale.ROOT);
        if (normalized.equals("PRACTICAL_CASE") || normalized.equals("CODE_CASE") || safeString(displayType).equalsIgnoreCase("CASE")) {
            return "CODE";
        }
        if (normalized.equals("COURSE")) {
            return "READING";
        }
        if (List.of("DOCUMENT", "PPT", "QUIZ", "VIDEO", "AUDIO", "IMAGE", "CODE", "MINDMAP", "READING", "PRACTICE", "SLIDES", "VIDEO_SCRIPT")
            .contains(normalized)) {
            return normalized;
        }
        return "READING";
    }

    private String normalizeDisplayType(String displayType, String resourceType) {
        String normalized = safeString(displayType).toUpperCase(Locale.ROOT);
        if (!normalized.isBlank()) {
            return normalized;
        }
        return displayType(resourceType, Map.of());
    }

    private String normalizeDifficulty(String difficultyLevel) {
        String normalized = safeString(difficultyLevel).toUpperCase(Locale.ROOT);
        return List.of("BASIC", "INTERMEDIATE", "ADVANCED", "MIXED").contains(normalized) ? normalized : "MIXED";
    }

    private String safeTitle(String title, String sourceUrl) {
        String normalized = safeString(title);
        if (!normalized.isBlank()) {
            return normalized;
        }
        String derived = deriveTitleFromUrl(sourceUrl);
        return derived == null || derived.isBlank() ? "Learning resource" : derived;
    }

    private String safeString(String value) {
        return value == null ? "" : value.trim();
    }

    private double clampScore(Double value, double fallback) {
        double score = value == null ? fallback : value;
        if (Double.isNaN(score) || Double.isInfinite(score)) {
            return fallback;
        }
        return Math.max(0.0, Math.min(1.0, score));
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
            case "NOTE", "NOTES" -> new ResourceTypeFilter(List.of("__NO_RESOURCE_TYPE__"), List.of("NOTE"));
            case "EXERCISE", "QUESTION", "QUESTIONS", "QUIZ", "PRACTICE" -> new ResourceTypeFilter(List.of("__NO_RESOURCE_TYPE__"), List.of("__NO_DISPLAY_TYPE__"));
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
