package com.project.application.resource;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.resource.dto.ResourceItemResponse;
import com.project.api.resource.dto.ResourceListResponse;
import com.project.api.resource.dto.ResourceProgressRequest;
import com.project.api.resource.dto.ResourceSemanticResultResponse;
import com.project.api.resource.dto.ResourceSemanticSearchResponse;
import com.project.application.common.ApplicationException;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.jdbc.core.namedparam.SqlParameterSource;

import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ResourceLibraryServiceTest {

    @Test
    void listResourcesUsesFiltersPaginationAndFavoriteState() {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000000001");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        ResourceSemanticSearchClient semanticClient = mock(ResourceSemanticSearchClient.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());
        when(jdbcTemplate.queryForObject(anyString(), any(MapSqlParameterSource.class), eq(Long.class)))
            .thenReturn(0L);

        ResourceLibraryService service = service(jdbcTemplate, semanticClient);

        ResourceListResponse response = service.listResources(
            userId,
            "PyTorch",
            "video",
            "COMPUTER_SCIENCE",
            "AI_ML",
            "Deep Learning",
            "basic",
            "WEB",
            true,
            "latest",
            2,
            15
        );

        assertThat(response.page()).isEqualTo(2);
        assertThat(response.size()).isEqualTo(15);
        verify(jdbcTemplate).query(
            org.mockito.ArgumentMatchers.argThat((String sql) ->
                sql.contains("lr.resource_type::text IN (:resourceTypes)")
                    && sql.contains("metadata_json ->> 'displayType'")
                    && sql.contains(":displayTypes")
                    && sql.contains("metadata_json ->> 'csCategory'")
                    && sql.contains("metadata_json ->> 'csSubcategory'")
                    && sql.contains("sourceUrl")
                    && sql.contains("accessibilityStatus")
                    && sql.contains("resource_type::text NOT IN ('QUIZ', 'PRACTICE')")
            ),
            org.mockito.ArgumentMatchers.<SqlParameterSource>argThat(params ->
                params instanceof MapSqlParameterSource source
                    && "%PyTorch%".equals(source.getValue("keyword"))
                    && source.getValue("resourceTypes").equals(List.of("VIDEO"))
                    && source.getValue("displayTypes").equals(List.of("VIDEO"))
                    && "COMPUTER_SCIENCE".equals(source.getValue("domain"))
                    && "AI_ML".equals(source.getValue("category"))
                    && "Deep Learning".equals(source.getValue("subcategory"))
                    && "BASIC".equals(source.getValue("difficulty"))
                    && "WEB".equals(source.getValue("source"))
                    && Integer.valueOf(15).equals(source.getValue("limit"))
                    && Integer.valueOf(30).equals(source.getValue("offset"))
            ),
            any(RowMapper.class)
        );
    }

    @Test
    void listResourcesPrioritizesCurrentStageSemanticRankForDefaultComprehensiveSort() {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000000011");
        UUID resourceId = UUID.fromString("70000000-0000-0000-0000-000000000011");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        ResourceSemanticSearchClient semanticClient = mock(ResourceSemanticSearchClient.class);
        ResourceSemanticWarmupService warmupService = mock(ResourceSemanticWarmupService.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());
        when(warmupService.stageRankedIds(userId, 12)).thenReturn(List.of(resourceId));
        when(jdbcTemplate.queryForObject(anyString(), any(MapSqlParameterSource.class), eq(Long.class)))
            .thenReturn(0L);

        ResourceLibraryService service = service(jdbcTemplate, semanticClient, warmupService);
        service.listResources(userId, null, null, null, null, null, null, null, false, "comprehensive", 0, 12);

        verify(jdbcTemplate).query(
            org.mockito.ArgumentMatchers.argThat((String sql) ->
                sql.contains("WITH stage_ranked AS")
                    && sql.contains("FROM (VALUES")
                    && sql.contains("CAST(:stageRankedId0 AS uuid)")
                    && sql.contains("stage_rank ASC NULLS LAST")
                    && sql.contains("recommendation_score")
                    && sql.contains("sourceUrl")
                    && sql.contains("accessibilityStatus")
                    && sql.contains("resource_type::text NOT IN ('QUIZ', 'PRACTICE')")
                    && !sql.contains("unnest(:stageRankedIds::uuid[])")
                    && !sql.contains("context_signals AS")
                    && !sql.contains("preferred_category")
            ),
            org.mockito.ArgumentMatchers.<SqlParameterSource>argThat(params ->
                params instanceof MapSqlParameterSource source
                    && Integer.valueOf(12).equals(source.getValue("limit"))
                    && Integer.valueOf(0).equals(source.getValue("offset"))
                    && source.getValue("stageRankedId0").equals(resourceId)
            ),
            any(RowMapper.class)
        );
        verify(semanticClient, never()).search(any(UUID.class), anyString(), any(Integer.class));
    }

    @Test
    void listResourcesRejectsQuizTypeForResourceLibrary() {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000000010");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        ResourceSemanticSearchClient semanticClient = mock(ResourceSemanticSearchClient.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());
        when(jdbcTemplate.queryForObject(anyString(), any(MapSqlParameterSource.class), eq(Long.class)))
            .thenReturn(0L);

        ResourceLibraryService service = service(jdbcTemplate, semanticClient);
        service.listResources(userId, null, "QUIZ", null, null, null, null, null, false, "quality", 0, 12);

        verify(jdbcTemplate).query(
            org.mockito.ArgumentMatchers.argThat((String sql) ->
                sql.contains("resource_type::text NOT IN ('QUIZ', 'PRACTICE')")
                    && sql.contains("IN (:displayTypes)")
                    && sql.contains("IN (:resourceTypes)")
            ),
            org.mockito.ArgumentMatchers.<SqlParameterSource>argThat(params ->
                params instanceof MapSqlParameterSource source
                    && source.getValue("resourceTypes").equals(List.of("__NO_RESOURCE_TYPE__"))
                    && source.getValue("displayTypes").equals(List.of("__NO_DISPLAY_TYPE__"))
            ),
            any(RowMapper.class)
        );
    }

    @Test
    void listResourcesNoteTypeOnlyReturnsUserNotes() {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000000012");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        ResourceSemanticSearchClient semanticClient = mock(ResourceSemanticSearchClient.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());
        when(jdbcTemplate.queryForObject(anyString(), any(MapSqlParameterSource.class), eq(Long.class)))
            .thenReturn(0L);

        ResourceLibraryService service = service(jdbcTemplate, semanticClient);
        service.listResources(userId, null, "NOTE", null, null, null, null, null, false, "latest", 0, 12);

        verify(jdbcTemplate).query(
            org.mockito.ArgumentMatchers.argThat((String sql) ->
                sql.contains("COALESCE(NULLIF(upper(lr.metadata_json ->> 'displayType'), ''), lr.resource_type::text) IN (:displayTypes)")
                    && sql.contains("lr.resource_type::text IN (:resourceTypes)")
                    && sql.contains("lr.access_scope::text = 'USER'")
                    && sql.contains("lr.owner_user_id = :userId")
                    && sql.contains("metadata_json ->> 'noteId'")
            ),
            org.mockito.ArgumentMatchers.<SqlParameterSource>argThat(params ->
                params instanceof MapSqlParameterSource source
                    && source.getValue("resourceTypes").equals(List.of("__NO_RESOURCE_TYPE__"))
                    && source.getValue("displayTypes").equals(List.of("NOTE"))
            ),
            any(RowMapper.class)
        );
    }

    @Test
    void updateProgressCompletesWhenProgressReachesOneHundred() {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000000002");
        UUID resourceId = UUID.fromString("70000000-0000-0000-0000-000000000002");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        ResourceSemanticSearchClient semanticClient = mock(ResourceSemanticSearchClient.class);
        when(jdbcTemplate.queryForObject(anyString(), any(MapSqlParameterSource.class), eq(Boolean.class)))
            .thenReturn(true);
        when(jdbcTemplate.update(anyString(), any(MapSqlParameterSource.class))).thenReturn(1);
        when(jdbcTemplate.queryForObject(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(null);

        ResourceLibraryService service = service(jdbcTemplate, semanticClient);
        service.updateProgress(userId, resourceId, new ResourceProgressRequest(100, false));

        verify(jdbcTemplate).queryForObject(
            org.mockito.ArgumentMatchers.argThat((String sql) ->
                sql.contains("lr.access_scope::text = 'GLOBAL'")
                    && sql.contains("lr.owner_user_id = :userId")
                    && sql.contains("app.user_course_enrollments")
            ),
            org.mockito.ArgumentMatchers.<SqlParameterSource>argThat(params ->
                params instanceof MapSqlParameterSource source
                    && userId.equals(source.getValue("userId"))
                    && resourceId.equals(source.getValue("resourceId"))
            ),
            eq(Boolean.class)
        );
        verify(jdbcTemplate).update(
            org.mockito.ArgumentMatchers.contains("progress_percent"),
            org.mockito.ArgumentMatchers.<SqlParameterSource>argThat(params ->
                params instanceof MapSqlParameterSource source
                    && Integer.valueOf(100).equals(source.getValue("progress"))
                    && Boolean.TRUE.equals(source.getValue("completed"))
            )
        );
    }

    @Test
    void setFavoriteRejectsUnreadableResource() {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000000005");
        UUID resourceId = UUID.fromString("70000000-0000-0000-0000-000000000005");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        ResourceSemanticSearchClient semanticClient = mock(ResourceSemanticSearchClient.class);
        when(jdbcTemplate.queryForObject(anyString(), any(MapSqlParameterSource.class), eq(Boolean.class)))
            .thenReturn(false);

        ResourceLibraryService service = service(jdbcTemplate, semanticClient);

        assertThatThrownBy(() -> service.setFavorite(userId, resourceId, true))
            .isInstanceOf(ApplicationException.class);
        verify(jdbcTemplate).queryForObject(
            org.mockito.ArgumentMatchers.argThat((String sql) ->
                sql.contains("lr.access_scope::text = 'GLOBAL'")
                    && sql.contains("lr.owner_user_id = :userId")
                    && sql.contains("app.user_course_enrollments")
            ),
            org.mockito.ArgumentMatchers.<SqlParameterSource>argThat(params ->
                params instanceof MapSqlParameterSource source
                    && userId.equals(source.getValue("userId"))
                    && resourceId.equals(source.getValue("resourceId"))
            ),
            eq(Boolean.class)
        );
        verify(jdbcTemplate, never()).update(anyString(), any(MapSqlParameterSource.class));
    }

    @Test
    void recommendationsUseWarmSemanticRankResults() {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000000004");
        UUID resourceId = UUID.fromString("70000000-0000-0000-0000-000000000004");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        ResourceSemanticSearchClient semanticClient = mock(ResourceSemanticSearchClient.class);
        ResourceSemanticWarmupService warmupService = mock(ResourceSemanticWarmupService.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of(resourceItem(resourceId, "图着色回溯案例", false)));
        when(warmupService.recommendationIds(userId, 5)).thenReturn(List.of(resourceId));

        ResourceLibraryService service = service(jdbcTemplate, semanticClient, warmupService);
        List<ResourceItemResponse> recommendations = service.recommendations(userId, 5);

        assertThat(recommendations).hasSize(1);
        assertThat(recommendations.get(0).id()).isEqualTo(resourceId);
        verify(semanticClient, never()).search(any(UUID.class), anyString(), any(Integer.class));
        verify(jdbcTemplate).query(
            org.mockito.ArgumentMatchers.argThat((String sql) ->
                sql.contains("WITH ranked AS")
                    && sql.contains("FROM (VALUES")
                    && sql.contains("CAST(:stageRankedId0 AS uuid)")
                    && sql.contains("ORDER BY semantic_rank ASC")
                    && !sql.contains("unnest(:rankedIds::uuid[])")
            ),
            org.mockito.ArgumentMatchers.<SqlParameterSource>argThat(params ->
                params instanceof MapSqlParameterSource source
                    && source.getValue("stageRankedId0").equals(resourceId)
                    && Integer.valueOf(5).equals(source.getValue("limit"))
            ),
            any(RowMapper.class)
        );
    }

    @Test
    void recommendationsFallBackToProfileHistoryAndGuardedNumericMetadataScoreWhenRagEmpty() {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000000014");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        ResourceSemanticSearchClient semanticClient = mock(ResourceSemanticSearchClient.class);
        ResourceSemanticWarmupService warmupService = mock(ResourceSemanticWarmupService.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());
        when(warmupService.recommendationIds(userId, 5)).thenReturn(List.of());

        ResourceLibraryService service = service(jdbcTemplate, semanticClient, warmupService);
        service.recommendations(userId, 5);

        verify(jdbcTemplate).query(
            org.mockito.ArgumentMatchers.argThat((String sql) ->
                sql.contains("CASE WHEN lr.metadata_json ->> 'qualityScore'")
                    && sql.contains("~ '^-?[0-9]+([.][0-9]+)?$'")
                    && sql.contains("COALESCE(urs.completed, false) = false")
                    && sql.contains("FROM app.user_profile_current up")
                    && sql.contains("up.profile_json ->> 'resourcePreference'")
                    && sql.contains("preferredResourceTypes")
                    && sql.contains("'CODE_CASE'")
                    && sql.contains("'PRACTICAL_CASE'")
                    && sql.contains("'MINDMAP'")
                    && sql.contains("app.user_resource_state history_state")
                    && sql.contains("jsonb_exists(lr.tags, history_tag.tag)")
                    && sql.contains("lower(lr.title) LIKE 'redirecting%'")
                    && !sql.contains("context_signals AS")
                    && !sql.contains("preferred_category")
                    && !sql.contains("'AI_ML'")
                    && !sql.contains("deep[- ]?learning")
                    && !sql.contains("ROW_NUMBER() OVER")
                    && !sql.contains("category_rank")
            ),
            org.mockito.ArgumentMatchers.<SqlParameterSource>argThat(params ->
                params instanceof MapSqlParameterSource source
                    && Integer.valueOf(5).equals(source.getValue("limit"))
            ),
            any(RowMapper.class)
        );
        verify(semanticClient, never()).search(any(UUID.class), anyString(), any(Integer.class));
    }

    @Test
    void recommendationsFallBackWithoutBuildingStageQueryInRequestThread() {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000000015");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        ResourceSemanticSearchClient semanticClient = mock(ResourceSemanticSearchClient.class);
        ResourceSemanticWarmupService warmupService = mock(ResourceSemanticWarmupService.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());
        when(warmupService.recommendationIds(userId, 5)).thenReturn(List.of());

        ResourceLibraryService service = service(jdbcTemplate, semanticClient, warmupService);
        service.recommendations(userId, 5);

        verify(jdbcTemplate).query(
            org.mockito.ArgumentMatchers.argThat((String sql) ->
                sql.contains("recommendation_score")
                    && !sql.contains("stage_steps AS")
                    && !sql.contains("latest_plan AS")
            ),
            org.mockito.ArgumentMatchers.<SqlParameterSource>argThat(params ->
                params instanceof MapSqlParameterSource source
                    && userId.equals(source.getValue("userId"))
                    && Integer.valueOf(5).equals(source.getValue("limit"))
            ),
            any(RowMapper.class)
        );
        verify(semanticClient, never()).search(any(UUID.class), anyString(), any(Integer.class));
    }

    @Test
    void semanticSearchUpsertsTavilyFallbackResourceBeforeHydration() {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000000016");
        UUID resourceId = UUID.fromString("18d9bc57-06be-5217-aeb1-effdda45804a");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        ResourceSemanticSearchClient semanticClient = mock(ResourceSemanticSearchClient.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of())
            .thenReturn(List.of(resourceItem(resourceId, "External graph guide", false)));
        when(jdbcTemplate.update(anyString(), any(MapSqlParameterSource.class))).thenReturn(1);
        when(semanticClient.search(eq(userId), eq("graph traversal"), eq(8)))
            .thenReturn(new ResourceSemanticSearchResponse(
                "graph traversal",
                true,
                "ok",
                List.of(new ResourceSemanticResultResponse(
                    resourceId,
                    null,
                    0.72,
                    "tavily_current_stage_fallback",
                    List.of(),
                    new com.project.api.resource.dto.ResourceExternalCandidateResponse(
                        "External graph guide",
                        "https://example.com/graph",
                        "example.com",
                        "graph traversal tutorial",
                        "READING",
                        "DOCUMENT",
                        "MIXED",
                        "",
                        0.6,
                        0.0,
                        List.of("graph", "traversal")
                    )
                ))
            ));

        ResourceLibraryService service = service(jdbcTemplate, semanticClient);
        ResourceSemanticSearchResponse response = service.semanticSearch(userId, "graph traversal", 8);

        assertThat(response.results()).hasSize(1);
        assertThat(response.results().get(0).resource().id()).isEqualTo(resourceId);
        verify(jdbcTemplate).update(
            org.mockito.ArgumentMatchers.contains("INSERT INTO app.learning_resource"),
            org.mockito.ArgumentMatchers.<MapSqlParameterSource>argThat(params ->
                resourceId.equals(params.getValue("resourceId"))
                    && "READING".equals(params.getValue("resourceType"))
                    && String.valueOf(params.getValue("metadata")).contains("resource_library_tavily_fallback")
            )
        );
    }

    @Test
    void tagsGuardAgainstNonArrayTagMetadata() {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000000006");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        ResourceSemanticSearchClient semanticClient = mock(ResourceSemanticSearchClient.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());

        ResourceLibraryService service = service(jdbcTemplate, semanticClient);
        service.tags(userId, 12);

        verify(jdbcTemplate).query(
            org.mockito.ArgumentMatchers.argThat((String sql) ->
                sql.contains("jsonb_typeof(lr.tags) = 'array'")
                    && sql.contains("ELSE '[]'::jsonb END")
            ),
            org.mockito.ArgumentMatchers.<SqlParameterSource>argThat(params ->
                params instanceof MapSqlParameterSource source
                    && Integer.valueOf(12).equals(source.getValue("limit"))
            ),
            any(RowMapper.class)
        );
    }

    @Test
    void statsTypeCountsPreferDisplayTypeMetadata() {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000000009");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        ResourceSemanticSearchClient semanticClient = mock(ResourceSemanticSearchClient.class);
        when(jdbcTemplate.queryForMap(anyString(), any(MapSqlParameterSource.class)))
            .thenReturn(java.util.Map.of(
                "total_resources", 0L,
                "favorite_resources", 0L,
                "started_resources", 0L,
                "completed_resources", 0L,
                "average_progress", 0
            ));
        when(jdbcTemplate.queryForList(anyString(), any(MapSqlParameterSource.class)))
            .thenReturn(List.of());
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());

        ResourceLibraryService service = service(jdbcTemplate, semanticClient);
        service.stats(userId);

        verify(jdbcTemplate).queryForList(
            org.mockito.ArgumentMatchers.argThat((String sql) ->
                sql.contains("metadata_json ->> 'displayType'")
                    && sql.contains("AS display_type")
                    && sql.contains("GROUP BY display_type")
                    && sql.contains("sourceUrl")
                    && sql.contains("accessibilityStatus")
                    && sql.contains("resource_type::text NOT IN ('QUIZ', 'PRACTICE')")
            ),
            org.mockito.ArgumentMatchers.<SqlParameterSource>argThat(params ->
                params instanceof MapSqlParameterSource source
                    && userId.equals(source.getValue("userId"))
            )
        );
        verify(jdbcTemplate).queryForList(
            org.mockito.ArgumentMatchers.argThat((String sql) ->
                sql.contains("metadata_json ->> 'csCategory'")
                    && sql.contains("AS metadata_value")
                    && sql.contains("GROUP BY metadata_value")
                    && sql.contains("sourceUrl")
                    && sql.contains("accessibilityStatus")
                    && sql.contains("resource_type::text NOT IN ('QUIZ', 'PRACTICE')")
            ),
            org.mockito.ArgumentMatchers.<SqlParameterSource>argThat(params ->
                params instanceof MapSqlParameterSource source
                    && userId.equals(source.getValue("userId"))
            )
        );
    }

    @Test
    void semanticSearchReturnsGracefulUnavailableResponseWhenPythonFails() {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000000003");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        ResourceSemanticSearchClient semanticClient = mock(ResourceSemanticSearchClient.class);
        when(semanticClient.search(eq(userId), eq("动态规划"), eq(8)))
            .thenThrow(new IllegalStateException("missing api key"));

        ResourceLibraryService service = service(jdbcTemplate, semanticClient);

        ResourceSemanticSearchResponse response = service.semanticSearch(userId, "动态规划", 8);

        assertThat(response.available()).isFalse();
        assertThat(response.results()).isEmpty();
        assertThat(response.message()).contains("missing api key");
    }

    private static ResourceItemResponse resourceItem(UUID id, String title, boolean completed) {
        return new ResourceItemResponse(
            id,
            title,
            "COMPUTER_SCIENCE",
            "READING",
            "DOCUMENT",
            "BASIC",
            "WEB",
            "",
            List.of(),
            "https://example.com/resource",
            "example.com",
            "",
            "",
            "AUTHORIZED",
            "ACCESSIBLE",
            200,
            null,
            0.9,
            0.1,
            0L,
            0L,
            0L,
            null,
            null,
            false,
            0,
            completed,
            null,
            null,
            null,
            "GENERAL_CS",
            "",
            java.util.Map.of()
        );
    }

    private static ResourceLibraryService service(
        NamedParameterJdbcTemplate jdbcTemplate,
        ResourceSemanticSearchClient semanticClient
    ) {
        return service(jdbcTemplate, semanticClient, mock(ResourceSemanticWarmupService.class));
    }

    private static ResourceLibraryService service(
        NamedParameterJdbcTemplate jdbcTemplate,
        ResourceSemanticSearchClient semanticClient,
        ResourceSemanticWarmupService warmupService
    ) {
        return new ResourceLibraryService(
            jdbcTemplate,
            new ObjectMapper(),
            semanticClient,
            warmupService
        );
    }
}
