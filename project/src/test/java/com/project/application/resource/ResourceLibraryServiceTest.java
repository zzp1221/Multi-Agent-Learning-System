package com.project.application.resource;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.resource.dto.ResourceListResponse;
import com.project.api.resource.dto.ResourceProgressRequest;
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

        ResourceLibraryService service = new ResourceLibraryService(jdbcTemplate, new ObjectMapper(), semanticClient);

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

        ResourceLibraryService service = new ResourceLibraryService(jdbcTemplate, new ObjectMapper(), semanticClient);
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

        ResourceLibraryService service = new ResourceLibraryService(jdbcTemplate, new ObjectMapper(), semanticClient);

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
    void recommendationsUseProfileHistoryAndGuardedNumericMetadataScore() {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000000004");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        ResourceSemanticSearchClient semanticClient = mock(ResourceSemanticSearchClient.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());

        ResourceLibraryService service = new ResourceLibraryService(jdbcTemplate, new ObjectMapper(), semanticClient);
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
                    && sql.contains("WITH latest_plan AS")
                    && sql.contains("app.learning_plan p")
                    && sql.contains("latest_task_path AS")
                    && sql.contains("t.service_type = 'PERSONALIZED_LEARNING'")
                    && sql.contains("jsonb_exists(t.response_summary, 'learningPath')")
                    && sql.contains("active_step AS")
                    && sql.contains("context_signals AS")
                    && sql.contains("preferred_category")
                    && sql.contains("'AI_ML'")
                    && sql.contains("deep[- ]?learning")
                    && sql.contains("pytorch")
                    && sql.contains("ROW_NUMBER() OVER")
                    && sql.contains("category_rank")
                    && sql.contains("lower(lr.title) LIKE 'redirecting%'")
            ),
            org.mockito.ArgumentMatchers.<SqlParameterSource>argThat(params ->
                params instanceof MapSqlParameterSource source
                    && Integer.valueOf(5).equals(source.getValue("limit"))
            ),
            any(RowMapper.class)
        );
    }

    @Test
    void tagsGuardAgainstNonArrayTagMetadata() {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000000006");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        ResourceSemanticSearchClient semanticClient = mock(ResourceSemanticSearchClient.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());

        ResourceLibraryService service = new ResourceLibraryService(jdbcTemplate, new ObjectMapper(), semanticClient);
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

        ResourceLibraryService service = new ResourceLibraryService(jdbcTemplate, new ObjectMapper(), semanticClient);
        service.stats(userId);

        verify(jdbcTemplate).queryForList(
            org.mockito.ArgumentMatchers.argThat((String sql) ->
                sql.contains("metadata_json ->> 'displayType'")
                    && sql.contains("AS display_type")
                    && sql.contains("GROUP BY display_type")
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

        ResourceLibraryService service = new ResourceLibraryService(jdbcTemplate, new ObjectMapper(), semanticClient);

        ResourceSemanticSearchResponse response = service.semanticSearch(userId, "动态规划", 8);

        assertThat(response.available()).isFalse();
        assertThat(response.results()).isEmpty();
        assertThat(response.message()).contains("missing api key");
    }
}
