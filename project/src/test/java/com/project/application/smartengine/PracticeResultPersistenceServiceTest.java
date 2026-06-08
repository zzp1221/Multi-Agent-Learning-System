package com.project.application.smartengine;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.domain.task.ServiceType;
import com.project.domain.task.SmartEngineTask;
import com.project.domain.task.TaskStatus;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;

import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class PracticeResultPersistenceServiceTest {

    @Test
    void persistsWrongJudgeItemAsPracticeSubmission() throws Exception {
        UUID practiceSetId = UUID.fromString("43000000-0000-0000-0000-000000000010");
        UUID practiceItemId = UUID.fromString("43000000-0000-0000-0000-000000000011");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class))).thenReturn(List.of());
        when(jdbcTemplate.queryForObject(anyString(), any(MapSqlParameterSource.class), eq(UUID.class)))
            .thenReturn(practiceSetId)
            .thenReturn(practiceItemId);
        when(jdbcTemplate.update(anyString(), any(MapSqlParameterSource.class))).thenReturn(1);

        PracticeResultPersistenceService service = new PracticeResultPersistenceService(
            jdbcTemplate,
            new ObjectMapper()
        );

        int persisted = service.persistCompletedPracticeJudgeResult(practiceTask());

        assertThat(persisted).isEqualTo(1);
        ArgumentCaptor<MapSqlParameterSource> captor = ArgumentCaptor.forClass(MapSqlParameterSource.class);
        verify(jdbcTemplate, org.mockito.Mockito.times(2)).update(anyString(), captor.capture());
        MapSqlParameterSource submissionParams = captor.getAllValues().get(0);
        assertThat(submissionParams.getValue("practiceSetId")).isEqualTo(practiceSetId);
        assertThat(submissionParams.getValue("practiceItemId")).isEqualTo(practiceItemId);
        assertThat(submissionParams.getValue("isCorrect")).isEqualTo(false);
        assertThat(String.valueOf(submissionParams.getValue("answerJson"))).contains("__验收故意错答__");
        assertThat(String.valueOf(submissionParams.getValue("judgeResultJson"))).contains("\"questionId\":\"q1\"");
        assertThat(String.valueOf(submissionParams.getValue("profileDeltaJson"))).contains("红黑树插入");
    }

    private SmartEngineTask practiceTask() {
        UUID taskId = UUID.fromString("43000000-0000-0000-0000-000000000001");
        UUID userId = UUID.fromString("43000000-0000-0000-0000-000000000002");
        SmartEngineTask task = new SmartEngineTask();
        task.setId(taskId);
        task.setTraceId("trace-practice-result");
        task.setUserId(userId);
        task.setServiceType(ServiceType.PRACTICE_JUDGE);
        task.setTaskStatus(TaskStatus.COMPLETED);
        task.setRequestPayload(Map.of(
            "conversationId", UUID.fromString("43000000-0000-0000-0000-000000000003"),
            "params", Map.of(
                "topic", "红黑树旋转与染色",
                "difficulty", "basic",
                "practiceQuestions", List.of(Map.of(
                    "questionId", "q1",
                    "questionType", "SINGLE_CHOICE",
                    "stem", "红黑树新插入节点默认是什么颜色？",
                    "options", List.of("红色", "黑色"),
                    "answer", "红色",
                    "knowledgeTags", List.of("红黑树插入", "节点染色")
                )),
                "answers", Map.of("q1", "__验收故意错答__")
            )
        ));
        task.setResponseSummary(Map.of(
            "judgeResult", Map.of(
                "items", List.of(Map.of(
                    "questionId", "q1",
                    "questionType", "SINGLE_CHOICE",
                    "learnerAnswer", "__验收故意错答__",
                    "correctAnswer", "红色",
                    "isCorrect", false,
                    "score", 0,
                    "feedback", "答案与标准答案不一致",
                    "profileDelta", Map.of("weakPoints", List.of("红黑树插入"))
                ))
            )
        ));
        return task;
    }
}
