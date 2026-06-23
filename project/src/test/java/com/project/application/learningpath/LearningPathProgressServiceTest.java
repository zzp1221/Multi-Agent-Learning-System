package com.project.application.learningpath;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.domain.task.ServiceType;
import com.project.domain.task.SmartEngineTask;
import com.project.domain.task.SmartEngineTaskRepository;
import com.project.domain.task.TaskStatus;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;

import java.sql.ResultSet;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class LearningPathProgressServiceTest {

    private static final UUID USER_ID = UUID.fromString("41000000-0000-0000-0000-000000000001");
    private static final UUID PLAN_ID = UUID.fromString("41000000-0000-0000-0000-000000000002");

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void stageTestPassAdvancesCurrentStepAndOpensNextStep() throws Exception {
        NamedParameterJdbcTemplate jdbcTemplate = mockJdbcWithPlan(Map.of(
            "steps", List.of(
                Map.of("stepId", "step-1", "title", "SQL 基础"),
                Map.of("stepId", "step-2", "title", "联合索引")
            )
        ));
        SmartEngineTaskRepository taskRepository = mockTaskRepository(stageTestTask("step-1", 0.8));
        LearningPathProgressService service = new LearningPathProgressService(jdbcTemplate, taskRepository, objectMapper);

        boolean handled = service.handleStageTestResult(USER_ID, UUID.randomUUID());

        assertThat(handled).isTrue();
        Map<String, Object> updatedPlan = capturedUpdatedPlan(jdbcTemplate);
        List<Map<String, Object>> steps = steps(updatedPlan);
        assertThat(steps.get(0))
            .containsEntry("status", "COMPLETED")
            .containsEntry("progress", 100)
            .containsEntry("lastTestScore", 0.8);
        assertThat(steps.get(1))
            .containsEntry("status", "IN_PROGRESS")
            .containsEntry("progress", 0);
        assertThat(updatedPlan).containsKey("lastStageTest");
    }

    @Test
    void stageTestPassAcceptsPercentTotalScore() throws Exception {
        NamedParameterJdbcTemplate jdbcTemplate = mockJdbcWithPlan(Map.of(
            "steps", List.of(
                Map.of("stepId", "step-1", "title", "SQL 基础"),
                Map.of("stepId", "step-2", "title", "联合索引")
            )
        ));
        SmartEngineTask task = stageTestTask("step-1", 0.0);
        task.setResponseSummary(Map.of("judgeResult", Map.of("totalScore", 80)));
        SmartEngineTaskRepository taskRepository = mockTaskRepository(task);
        LearningPathProgressService service = new LearningPathProgressService(jdbcTemplate, taskRepository, objectMapper);

        boolean handled = service.handleStageTestResult(USER_ID, UUID.randomUUID());

        assertThat(handled).isTrue();
        List<Map<String, Object>> steps = steps(capturedUpdatedPlan(jdbcTemplate));
        assertThat(steps.get(0))
            .containsEntry("status", "COMPLETED")
            .containsEntry("lastTestScore", 0.8);
        assertThat(steps.get(1)).containsEntry("status", "IN_PROGRESS");
    }

    @Test
    void stageTestJudgeTaskWithAnswersAndScoreAdvancesCurrentStep() throws Exception {
        NamedParameterJdbcTemplate jdbcTemplate = mockJdbcWithPlan(Map.of(
            "steps", List.of(
                Map.of("stepId", "step-1", "title", "SQL 基础"),
                Map.of("stepId", "step-2", "title", "联合索引")
            )
        ));
        SmartEngineTask task = stageTestTask("step-1", 0.0);
        task.setRequestPayload(Map.of(
            "params", Map.of(
                "purpose", "STAGE_TEST",
                "answers", Map.of("q1", "A"),
                "learningContext", Map.of("activeLearningStepId", "step-1")
            )
        ));
        task.setResponseSummary(Map.of("judgeResult", Map.of(
            "accuracy", 0.9,
            "totalScore", 95,
            "items", List.of(Map.of("questionId", "q1"))
        )));
        SmartEngineTaskRepository taskRepository = mockTaskRepository(task);
        LearningPathProgressService service = new LearningPathProgressService(jdbcTemplate, taskRepository, objectMapper);

        boolean handled = service.handleStageTestResult(USER_ID, UUID.randomUUID());

        assertThat(handled).isTrue();
        List<Map<String, Object>> steps = steps(capturedUpdatedPlan(jdbcTemplate));
        assertThat(steps.get(0))
            .containsEntry("status", "COMPLETED")
            .containsEntry("lastTestScore", 0.9);
        assertThat(steps.get(1)).containsEntry("status", "IN_PROGRESS");
    }

    @Test
    void stageTestQuestionGenerationTaskDoesNotTouchLearningPlan() {
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        SmartEngineTask task = stageTestTask("step-1", 0.9);
        task.setResponseSummary(Map.of("questionBatch", Map.of("questions", List.of(Map.of("questionId", "q1")))));
        SmartEngineTaskRepository taskRepository = mockTaskRepository(task);
        LearningPathProgressService service = new LearningPathProgressService(jdbcTemplate, taskRepository, objectMapper);

        boolean handled = service.handleStageTestResult(USER_ID, UUID.randomUUID());

        assertThat(handled).isTrue();
        verify(jdbcTemplate, never()).query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class));
        verify(jdbcTemplate, never()).update(anyString(), any(MapSqlParameterSource.class));
    }

    @Test
    void stageTestFailRecordsScoreWithoutAdvancing() throws Exception {
        NamedParameterJdbcTemplate jdbcTemplate = mockJdbcWithPlan(Map.of(
            "steps", List.of(
                Map.of("stepId", "step-1", "title", "SQL 基础", "status", "IN_PROGRESS", "testAttempts", 1),
                Map.of("stepId", "step-2", "title", "联合索引", "status", "NOT_STARTED")
            )
        ));
        SmartEngineTaskRepository taskRepository = mockTaskRepository(stageTestTask("step-1", 0.7));
        LearningPathProgressService service = new LearningPathProgressService(jdbcTemplate, taskRepository, objectMapper);

        boolean handled = service.handleStageTestResult(USER_ID, UUID.randomUUID());

        assertThat(handled).isTrue();
        List<Map<String, Object>> steps = steps(capturedUpdatedPlan(jdbcTemplate));
        assertThat(steps.get(0))
            .containsEntry("status", "IN_PROGRESS")
            .containsEntry("lastTestScore", 0.7)
            .containsEntry("testAttempts", 2);
        assertThat(steps.get(1)).containsEntry("status", "NOT_STARTED");
    }

    @Test
    void ordinaryPracticeDoesNotTouchLearningPlan() {
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        SmartEngineTask task = stageTestTask("step-1", 0.9);
        task.setRequestPayload(Map.of("params", Map.of("topic", "SQL 基础")));
        SmartEngineTaskRepository taskRepository = mockTaskRepository(task);
        LearningPathProgressService service = new LearningPathProgressService(jdbcTemplate, taskRepository, objectMapper);

        boolean handled = service.handleStageTestResult(USER_ID, UUID.randomUUID());

        assertThat(handled).isFalse();
        verify(jdbcTemplate, never()).query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class));
        verify(jdbcTemplate, never()).update(anyString(), any(MapSqlParameterSource.class));
    }

    @Test
    void activeStepMismatchDoesNotAdvance() {
        NamedParameterJdbcTemplate jdbcTemplate = mockJdbcWithPlan(Map.of(
            "steps", List.of(
                Map.of("stepId", "step-1", "title", "SQL 基础", "status", "COMPLETED"),
                Map.of("stepId", "step-2", "title", "联合索引", "status", "IN_PROGRESS")
            )
        ));
        SmartEngineTaskRepository taskRepository = mockTaskRepository(stageTestTask("step-1", 0.95));
        LearningPathProgressService service = new LearningPathProgressService(jdbcTemplate, taskRepository, objectMapper);

        boolean handled = service.handleStageTestResult(USER_ID, UUID.randomUUID());

        assertThat(handled).isTrue();
        verify(jdbcTemplate, never()).update(anyString(), any(MapSqlParameterSource.class));
    }

    @Test
    void lastStagePassLeavesNoNextActiveStep() throws Exception {
        NamedParameterJdbcTemplate jdbcTemplate = mockJdbcWithPlan(Map.of(
            "steps", List.of(Map.of("stepId", "step-1", "title", "SQL 基础", "status", "IN_PROGRESS"))
        ));
        SmartEngineTaskRepository taskRepository = mockTaskRepository(stageTestTask("step-1", 1.0));
        LearningPathProgressService service = new LearningPathProgressService(jdbcTemplate, taskRepository, objectMapper);

        boolean handled = service.handleStageTestResult(USER_ID, UUID.randomUUID());

        assertThat(handled).isTrue();
        List<Map<String, Object>> steps = steps(capturedUpdatedPlan(jdbcTemplate));
        assertThat(steps).hasSize(1);
        assertThat(steps.getFirst()).containsEntry("status", "COMPLETED");
    }

    private NamedParameterJdbcTemplate mockJdbcWithPlan(Map<String, Object> planJson) {
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenAnswer(invocation -> {
                @SuppressWarnings("unchecked")
                RowMapper<Object> mapper = invocation.getArgument(2);
                ResultSet rs = mock(ResultSet.class);
                when(rs.getObject("id", UUID.class)).thenReturn(PLAN_ID);
                when(rs.getObject("user_id", UUID.class)).thenReturn(USER_ID);
                when(rs.getObject("course_id", UUID.class)).thenReturn(null);
                when(rs.getObject("plan_json")).thenReturn(objectMapper.writeValueAsString(planJson));
                return List.of(mapper.mapRow(rs, 0));
            });
        when(jdbcTemplate.queryForObject(anyString(), any(MapSqlParameterSource.class), eq(Integer.class))).thenReturn(2);
        when(jdbcTemplate.update(anyString(), any(MapSqlParameterSource.class))).thenReturn(1);
        return jdbcTemplate;
    }

    private SmartEngineTaskRepository mockTaskRepository(SmartEngineTask task) {
        SmartEngineTaskRepository taskRepository = mock(SmartEngineTaskRepository.class);
        when(taskRepository.findById(any())).thenReturn(Optional.of(task));
        return taskRepository;
    }

    private SmartEngineTask stageTestTask(String stepId, double accuracy) {
        SmartEngineTask task = new SmartEngineTask();
        task.setId(UUID.randomUUID());
        task.setTraceId(UUID.randomUUID().toString());
        task.setUserId(USER_ID);
        task.setServiceType(ServiceType.PRACTICE_JUDGE);
        task.setTaskStatus(TaskStatus.COMPLETED);
        task.setCompletedAt(OffsetDateTime.now());
        task.setRequestPayload(Map.of(
            "params", Map.of(
                "purpose", "STAGE_TEST",
                "learningContext", Map.of("activeLearningStepId", stepId)
            )
        ));
        task.setResponseSummary(Map.of("judgeResult", Map.of("accuracy", accuracy)));
        return task;
    }

    private Map<String, Object> capturedUpdatedPlan(NamedParameterJdbcTemplate jdbcTemplate) throws Exception {
        ArgumentCaptor<MapSqlParameterSource> captor = ArgumentCaptor.forClass(MapSqlParameterSource.class);
        verify(jdbcTemplate).update(eq("UPDATE app.learning_plan\nSET plan_json = CAST(:planJson AS jsonb),\n    updated_at = now()\nWHERE id = :planId\n"), captor.capture());
        String rawJson = String.valueOf(captor.getValue().getValue("planJson"));
        return objectMapper.readValue(rawJson, new com.fasterxml.jackson.core.type.TypeReference<>() {
        });
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> steps(Map<String, Object> planJson) {
        return (List<Map<String, Object>>) planJson.get("steps");
    }
}
