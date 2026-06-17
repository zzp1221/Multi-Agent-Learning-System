package com.project.application.resource;

import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.UUID;

final class ResourceLibrarySqlBuilder {

    private static final double WIKI_BOUND_LEXICAL_CONFIDENCE_MIN = 0.60;

    List<String> resourceConditions(
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

    String resourceSelectSql() {
        return resourceSelectSql("");
    }

    String resourceSelectSql(String extraColumns) {
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

    String listResourceSelectSql(String sort, String whereClause, int stageRankedIdCount) {
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
            """ + recommendationContextCtesSql() + """
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

    void bindStageRankedIds(MapSqlParameterSource params, List<UUID> stageRankedIds) {
        for (int index = 0; index < stageRankedIds.size(); index += 1) {
            params.addValue("stageRankedId" + index, stageRankedIds.get(index));
        }
    }

    String stageRankedValuesSql(int stageRankedIdCount) {
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

    String numericMetadataSql(String key, String fallback) {
        return "COALESCE(CASE WHEN lr.metadata_json ->> '" + key + "' ~ '^-?[0-9]+([.][0-9]+)?$' THEN (lr.metadata_json ->> '" + key + "')::numeric END, " + fallback + ")";
    }

    String recommendationContextCtesSql() {
        String profileResourcePreference = normalizedResourcePreferenceSql("up.profile_json ->> 'resourcePreference'");
        String preferredResourceType = normalizedResourcePreferenceSql("preferred_type.value");
        return """
            profile_preferences AS (
              SELECT
                %s AS profile_resource_preference,
                ARRAY(
                  SELECT %s
                  FROM jsonb_array_elements_text(
                    CASE WHEN jsonb_typeof(up.profile_json -> 'preferredResourceTypes') = 'array'
                      THEN up.profile_json -> 'preferredResourceTypes'
                      ELSE '[]'::jsonb
                    END
                  ) AS preferred_type(value)
                ) AS preferred_resource_types
              FROM app.user_profile_current up
              WHERE up.user_id = :userId
            ),
            history_tags AS (
              SELECT DISTINCT history_tag.tag
              FROM app.user_resource_state history_state
              JOIN app.learning_resource history_lr ON history_lr.id = history_state.resource_id
              CROSS JOIN LATERAL jsonb_array_elements_text(
                CASE WHEN jsonb_typeof(history_lr.tags) = 'array' THEN history_lr.tags ELSE '[]'::jsonb END
              ) AS history_tag(tag)
              WHERE history_state.user_id = :userId
                AND (COALESCE(history_state.is_favorite, false) OR COALESCE(history_state.progress_percent, 0) > 0)
            ),
            """.formatted(profileResourcePreference, preferredResourceType);
    }

    String visibleResourceCondition() {
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
                AND
                """ + highConfidenceExternalResourceCondition() + """
              )
            )
            """;
    }

    String readableResourceCondition() {
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

    String recommendationScoreSql() {
        String displayType = "COALESCE(NULLIF(upper(lr.metadata_json ->> 'displayType'), ''), lr.resource_type::text)";
        String qualityScore = numericMetadataSql("qualityScore", "0.5");
        String popularityScore = numericMetadataSql("popularityScore", "0");
        return """
            (
              %s * 0.45
              + %s * 0.20
              + CASE WHEN EXISTS (
                  SELECT 1
                  FROM profile_preferences pp
                  WHERE pp.profile_resource_preference = %s
                    OR %s = ANY(pp.preferred_resource_types)
                ) THEN 0.20 ELSE 0 END
              + CASE WHEN EXISTS (
                  SELECT 1
                  FROM history_tags ht
                  WHERE jsonb_exists(lr.tags, ht.tag)
                ) THEN 0.12 ELSE 0 END
              + CASE WHEN COALESCE(urs.is_favorite, false) THEN 0.18 ELSE 0 END
              + LEAST(0.12, COALESCE(urs.progress_percent, 0) * 0.0012)
              + CASE WHEN lower(lr.title) LIKE 'redirecting%%'
                  OR lower(COALESCE(lr.summary_text, '')) LIKE 'redirecting%%'
                THEN -0.30 ELSE 0 END
            )
            """.formatted(
            qualityScore,
            popularityScore,
            displayType,
            displayType
        );
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

    private String highConfidenceExternalResourceCondition() {
        return """
            (
              COALESCE(lr.metadata_json ->> 'ingestedBy', '') <> 'wiki_resource_importer'
              OR COALESCE(lr.summary_text, '') !~* 'generic lexical score [0-9]+[.][0-9]+'
              OR COALESCE(
                   NULLIF((regexp_match(COALESCE(lr.summary_text, ''), 'generic lexical score ([0-9]+[.][0-9]+)', 'i'))[1], '')::numeric,
                   0
                 ) >= %s
            )
            AND COALESCE(lr.metadata_json ->> 'wikiBindingStatus', '') <> 'LOW_CONFIDENCE_DROPPED'
            """.formatted(WIKI_BOUND_LEXICAL_CONFIDENCE_MIN);
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

    private record ResourceTypeFilter(List<String> resourceTypes, List<String> displayTypes) {
    }
}
