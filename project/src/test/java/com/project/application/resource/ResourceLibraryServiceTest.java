package com.project.application.resource;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.resource.dto.ResourceItemResponse;
import com.project.api.resource.dto.ResourceListResponse;
import com.project.api.resource.dto.ResourceProgressRequest;
import com.project.api.resource.dto.ResourceSemanticHitResponse;
import com.project.api.resource.dto.ResourceSemanticResultResponse;
import com.project.api.resource.dto.ResourceSemanticSearchResponse;
import com.project.application.common.ApplicationException;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.jdbc.core.namedparam.SqlParameterSource;

import java.sql.ResultSet;
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
                    && sql.contains("wiki_resource_importer")
                    && sql.contains("generic lexical score")
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
    void listResourcesEscapesLikeWildcardsInKeyword() {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000000101");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        ResourceSemanticSearchClient semanticClient = mock(ResourceSemanticSearchClient.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());
        when(jdbcTemplate.queryForObject(anyString(), any(MapSqlParameterSource.class), eq(Long.class)))
            .thenReturn(0L);

        ResourceLibraryService service = service(jdbcTemplate, semanticClient);
        service.listResources(userId, "100%_path\\test", null, null, null, null, null, null, false, "latest", 0, 12);

        verify(jdbcTemplate).query(
            org.mockito.ArgumentMatchers.argThat((String sql) -> sql.contains("ESCAPE '\\'")),
            org.mockito.ArgumentMatchers.<SqlParameterSource>argThat(params ->
                params instanceof MapSqlParameterSource source
                    && "%100\\%\\_path\\\\test%".equals(source.getValue("keyword"))
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
                    && sql.contains("wiki_resource_importer")
                    && sql.contains("generic lexical score")
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
        ResourceSemanticWarmupService warmupService = mock(ResourceSemanticWarmupService.class);
        when(jdbcTemplate.queryForObject(anyString(), any(MapSqlParameterSource.class), eq(Boolean.class)))
            .thenReturn(true);
        when(jdbcTemplate.update(anyString(), any(MapSqlParameterSource.class))).thenReturn(1);
        when(jdbcTemplate.queryForObject(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(null);

        ResourceLibraryService service = service(jdbcTemplate, semanticClient, warmupService);
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
        verify(warmupService).evictUser(userId);
    }

    @Test
    void setFavoriteEvictsWarmRecommendationCacheAfterStateChange() {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000000018");
        UUID resourceId = UUID.fromString("70000000-0000-0000-0000-000000000018");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        ResourceSemanticSearchClient semanticClient = mock(ResourceSemanticSearchClient.class);
        ResourceSemanticWarmupService warmupService = mock(ResourceSemanticWarmupService.class);
        when(jdbcTemplate.queryForObject(anyString(), any(MapSqlParameterSource.class), eq(Boolean.class)))
            .thenReturn(true);
        when(jdbcTemplate.update(anyString(), any(MapSqlParameterSource.class))).thenReturn(1);
        when(jdbcTemplate.queryForObject(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(null);

        ResourceLibraryService service = service(jdbcTemplate, semanticClient, warmupService);
        service.setFavorite(userId, resourceId, true);

        verify(warmupService).evictUser(userId);
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
                sql.contains("WITH stage_ranked AS")
                    && sql.contains("FROM (VALUES")
                    && sql.contains("CAST(:stageRankedId0 AS uuid)")
                    && sql.contains("GREATEST(0.0, 0.18 - (sr.stage_rank * 0.01))")
                    && sql.contains("recommendation_score DESC")
                    && sql.contains("stage_rank ASC NULLS LAST")
                    && sql.contains("app.user_resource_state history_state")
                    && sql.contains("COALESCE(urs.is_favorite, false)")
                    && sql.contains("COALESCE(urs.progress_percent, 0) * 0.0012")
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
                    List.of(new ResourceSemanticHitResponse(
                        11L,
                        0,
                        0.91,
                        """
                        Wiki title: "Graph Traversal"
                        Wiki slug: algorithms/graph-traversal
                        Aliases: BFS, DFS
                        Wiki summary: Graph traversal overview.
                        Resource title: External graph guide
                        Tags: graph, wiki-bound-resource, "metadata-search-fallback", existing-web-match, traversal
                        URL: https://example.com/graph
                        """,
                        "https://example.com/graph"
                    )),
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
        String hitContent = response.results().get(0).hits().get(0).content();
        assertThat(hitContent)
            .contains("Topic: Graph Traversal")
            .contains("Topic summary: Graph traversal overview.")
            .contains("Tags: graph, traversal")
            .doesNotContain("Wiki slug:", "Aliases:", "wiki-bound-resource", "metadata-search-fallback", "existing-web-match");
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
                    && sql.contains("lower(trim")
                    && sql.contains("NOT IN (:internalTags)")
            ),
            org.mockito.ArgumentMatchers.<SqlParameterSource>argThat(params ->
                params instanceof MapSqlParameterSource source
                    && Integer.valueOf(12).equals(source.getValue("limit"))
                    && source.getValue("internalTags") instanceof java.util.Set<?> internalTags
                    && internalTags.contains("wiki-bound-resource")
                    && internalTags.contains("existing-web-match")
                    && internalTags.contains("metadata-search-fallback")
            ),
            any(RowMapper.class)
        );
    }

    @Test
    void resourceMapperCleansPipelineSummaryAndWrappedTitle() throws Exception {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000000017");
        UUID resourceId = UUID.fromString("70000000-0000-0000-0000-000000000017");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        ResourceSemanticSearchClient semanticClient = mock(ResourceSemanticSearchClient.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenAnswer(invocation -> {
                RowMapper<ResourceItemResponse> mapper = invocation.getArgument(2);
                return List.of(mapper.mapRow(resourceResultSet(resourceId), 0));
            });
        when(jdbcTemplate.queryForObject(anyString(), any(MapSqlParameterSource.class), eq(Long.class)))
            .thenReturn(1L);

        ResourceLibraryService service = service(jdbcTemplate, semanticClient);
        ResourceListResponse response = service.listResources(userId, null, null, null, null, null, null, null, false, "latest", 0, 12);

        assertThat(response.items()).hasSize(1);
        ResourceItemResponse item = response.items().get(0);
        assertThat(item.title()).isEqualTo("路由协议对比（RIP与OSPF）");
        assertThat(item.summaryText()).isEmpty();
        assertThat(item.tags()).contains("routing");
        assertThat(item.tags()).doesNotContain(
            "wiki-bound-resource",
            "Existing-Web-Match",
            "\"metadata-search-fallback\"",
            "metadata-search-fallback",
            "\"路由协议对比（RIP与OSPF）\"",
            "RIP",
            "OSPF"
        );
        assertThat(item.metadata())
            .doesNotContainKeys(
                "wikiSlug",
                "wikiTitle",
                "wikiSourceRef",
                "wikiAliases",
                "wikiBindingStatus",
                "ingestedBy",
                "displayType",
                "accessibilityStatus",
                "qualityScore",
                "popularityScore"
            )
            .containsEntry("noteId", "note-123");
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
                    && sql.contains("wiki_resource_importer")
                    && sql.contains("generic lexical score")
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
                    && sql.contains("wiki_resource_importer")
                    && sql.contains("generic lexical score")
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

    private static ResultSet resourceResultSet(UUID resourceId) throws Exception {
        ResultSet rs = mock(ResultSet.class);
        when(rs.getObject("id")).thenReturn(resourceId);
        when(rs.getString("title")).thenReturn("\"路由协议对比（RIP与OSPF）\"");
        when(rs.getString("domain")).thenReturn("COMPUTER_SCIENCE");
        when(rs.getString("resource_type")).thenReturn("READING");
        when(rs.getString("difficulty_level")).thenReturn("BASIC");
        when(rs.getString("source_kind")).thenReturn("WEB");
        when(rs.getString("summary_text")).thenReturn("Metadata-only URL candidate matched to wiki topic with generic lexical score 0.4210");
        when(rs.getString("tags_json")).thenReturn("[\"routing\",\"wiki-bound-resource\",\"Existing-Web-Match\",\"\\\"metadata-search-fallback\\\"\",\"\\\"路由协议对比（RIP与OSPF）\\\"\",\"RIP\",\"OSPF\"]");
        when(rs.getString("metadata_json")).thenReturn("""
            {
              "sourceUrl": "https://example.com/routing",
              "sourceName": "example.com",
              "ingestedBy": "wiki_resource_importer",
              "wikiSlug": "networking/routing-protocols",
              "wikiTitle": "\\"路由协议对比（RIP与OSPF）\\"",
              "wikiSourceRef": "wiki://networking/routing-protocols",
              "wikiAliases": ["RIP", "OSPF"],
              "noteId": "note-123",
              "displayType": "DOCUMENT",
              "accessibilityStatus": "ACCESSIBLE",
              "copyrightStatus": "AUTHORIZED",
              "qualityScore": 0.9,
              "popularityScore": 0.1,
              "csCategory": "GENERAL_CS",
              "csSubcategory": "Networking"
            }
            """);
        when(rs.getLong("favorite_count")).thenReturn(0L);
        when(rs.getBoolean("is_favorite")).thenReturn(false);
        when(rs.getInt("progress_percent")).thenReturn(0);
        when(rs.getBoolean("completed")).thenReturn(false);
        return rs;
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
