package com.project.application.note;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.note.dto.CreateNoteRequest;
import com.project.api.note.dto.NoteDetailResponse;
import com.project.application.resource.ResourceLibraryService;
import org.junit.jupiter.api.Test;
import org.springframework.core.task.TaskExecutor;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;

import java.util.ArrayDeque;
import java.util.List;
import java.util.Queue;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class NoteServiceTest {

    @Test
    void createNoteReturnsBeforeRagIndexTaskRuns() {
        UUID userId = UUID.fromString("60000000-0000-0000-0000-000000000101");
        UUID resourceId = UUID.fromString("62000000-0000-0000-0000-000000000101");
        UUID tagId = UUID.fromString("61000000-0000-0000-0000-000000000101");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        NotePythonClient notePythonClient = mock(NotePythonClient.class);
        ResourceLibraryService resourceLibraryService = mock(ResourceLibraryService.class);
        CapturingTaskExecutor noteIndexTaskExecutor = new CapturingTaskExecutor();

        when(jdbcTemplate.update(anyString(), any(MapSqlParameterSource.class))).thenReturn(1);
        when(jdbcTemplate.queryForObject(anyString(), any(MapSqlParameterSource.class), eq(Integer.class))).thenReturn(1);
        when(jdbcTemplate.queryForObject(anyString(), any(MapSqlParameterSource.class), eq(UUID.class))).thenReturn(tagId);
        when(notePythonClient.index(any(NoteRagIndexRequest.class))).thenReturn(new NoteRagIndexResult(true, 1, "ok"));
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class))).thenAnswer(invocation -> {
            String sql = invocation.getArgument(0, String.class);
            MapSqlParameterSource params = invocation.getArgument(1, MapSqlParameterSource.class);
            if (sql.contains("FROM app.note n")) {
                Object noteId = params.getValue("noteId");
                return List.of(new NoteDetailResponse(
                    (UUID) noteId,
                    null,
                    "异步索引验证",
                    "# 异步索引验证",
                    "异步索引验证",
                    "hash",
                    List.of(),
                    6,
                    1,
                    null,
                    null,
                    null,
                    false,
                    resourceId
                ));
            }
            if (sql.contains("FROM app.note_tag_link")) {
                return List.of();
            }
            return List.of();
        });

        NoteService service = new NoteService(
            jdbcTemplate,
            new ObjectMapper(),
            notePythonClient,
            resourceLibraryService,
            noteIndexTaskExecutor
        );

        NoteDetailResponse response = service.createNote(
            userId,
            new CreateNoteRequest("异步索引验证", "# 异步索引验证", null, List.of())
        );

        assertThat(response.title()).isEqualTo("异步索引验证");
        assertThat(noteIndexTaskExecutor.pendingTasks()).isEqualTo(1);
        verify(notePythonClient, never()).index(any(NoteRagIndexRequest.class));

        noteIndexTaskExecutor.runNext();

        verify(notePythonClient).index(any(NoteRagIndexRequest.class));
    }

    private static final class CapturingTaskExecutor implements TaskExecutor {
        private final Queue<Runnable> tasks = new ArrayDeque<>();

        @Override
        public void execute(Runnable task) {
            tasks.add(task);
        }

        int pendingTasks() {
            return tasks.size();
        }

        void runNext() {
            Runnable task = tasks.poll();
            if (task != null) {
                task.run();
            }
        }
    }
}
