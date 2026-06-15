package com.project.application.resource;

import com.project.api.resource.dto.ResourceSemanticResultResponse;
import com.project.api.resource.dto.ResourceSemanticSearchResponse;
import org.junit.jupiter.api.Test;
import org.springframework.core.task.SyncTaskExecutor;
import org.springframework.core.task.TaskExecutor;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;

import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ResourceSemanticWarmupServiceTest {

    @Test
    void stageRankedIdsReturnsCachedRagIdsAfterWarmup() {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000001001");
        UUID resourceId = UUID.fromString("70000000-0000-0000-0000-000000001001");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        ResourceSemanticSearchClient semanticClient = mock(ResourceSemanticSearchClient.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of("graph traversal current stage"));
        when(semanticClient.search(eq(userId), eq("graph traversal current stage"), eq(20)))
            .thenReturn(new ResourceSemanticSearchResponse(
                "graph traversal current stage",
                true,
                "ok",
                List.of(new ResourceSemanticResultResponse(resourceId, null, 0.9, "rag", List.of()))
            ));

        ResourceSemanticWarmupService service = new ResourceSemanticWarmupService(
            jdbcTemplate,
            semanticClient,
            new SyncTaskExecutor()
        );

        service.submitCurrentStageWarmup(userId);

        assertThat(service.stageRankedIds(userId, 12)).containsExactly(resourceId);
        verify(semanticClient).search(userId, "graph traversal current stage", 20);
    }

    @Test
    void evictUserClearsCachedWarmupResults() {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000001004");
        UUID resourceId = UUID.fromString("70000000-0000-0000-0000-000000001004");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        ResourceSemanticSearchClient semanticClient = mock(ResourceSemanticSearchClient.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of("database index current stage"));
        when(semanticClient.search(eq(userId), eq("database index current stage"), eq(20)))
            .thenReturn(new ResourceSemanticSearchResponse(
                "database index current stage",
                true,
                "ok",
                List.of(new ResourceSemanticResultResponse(resourceId, null, 0.9, "rag", List.of()))
            ));

        DeferredTaskExecutor executor = new DeferredTaskExecutor();
        ResourceSemanticWarmupService service = new ResourceSemanticWarmupService(
            jdbcTemplate,
            semanticClient,
            executor
        );

        service.submitCurrentStageWarmup(userId);
        executor.runNext();
        assertThat(service.stageRankedIds(userId, 12)).containsExactly(resourceId);

        service.evictUser(userId);

        assertThat(service.stageRankedIds(userId, 12)).isEmpty();
    }

    @Test
    void stageRankedIdsReturnsEmptyAndDoesNotCallRagWhenStageQueryMissing() {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000001002");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        ResourceSemanticSearchClient semanticClient = mock(ResourceSemanticSearchClient.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());

        ResourceSemanticWarmupService service = new ResourceSemanticWarmupService(
            jdbcTemplate,
            semanticClient,
            new SyncTaskExecutor()
        );

        assertThat(service.stageRankedIds(userId, 12)).isEmpty();
        verify(semanticClient, never()).search(any(UUID.class), anyString(), any(Integer.class));
    }

    @Test
    void stageRankedIdsReturnsEmptyWhenStageQueryFails() {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000001003");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        ResourceSemanticSearchClient semanticClient = mock(ResourceSemanticSearchClient.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenThrow(new org.springframework.jdbc.BadSqlGrammarException("query", "select 1", new java.sql.SQLException("missing table")));

        ResourceSemanticWarmupService service = new ResourceSemanticWarmupService(
            jdbcTemplate,
            semanticClient,
            new SyncTaskExecutor()
        );

        assertThat(service.stageRankedIds(userId, 12)).isEmpty();
        verify(semanticClient, never()).search(any(UUID.class), anyString(), any(Integer.class));
    }

    private static final class DeferredTaskExecutor implements TaskExecutor {
        private final java.util.Queue<Runnable> tasks = new java.util.ArrayDeque<>();

        @Override
        public void execute(Runnable task) {
            tasks.add(task);
        }

        void runNext() {
            Runnable task = tasks.poll();
            if (task != null) {
                task.run();
            }
        }
    }
}
