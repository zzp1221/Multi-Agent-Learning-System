package com.project.application.learningpath;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.learningpath.dto.LearningPathCurrentResponse;
import com.project.application.smartengine.TaskStateMachineService;
import com.project.domain.task.ServiceType;
import com.project.domain.task.SmartEngineTask;
import com.project.domain.task.SmartEngineTaskRepository;
import com.project.domain.task.TaskStatus;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.test.util.ReflectionTestUtils;

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
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class LearningPathQueryServiceTest {

    @Test
    void returnsLearningPathFromCompletedTaskWhenPlanTableIsEmpty() {
        UUID userId = UUID.fromString("40000000-0000-0000-0000-000000000005");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());

        SmartEngineTask personalizedTask = task(userId, ServiceType.PERSONALIZED_LEARNING);
        personalizedTask.setResponseSummary(Map.of(
            "summary", "已生成首版路径",
            "learningPath", Map.of(
                "summaryText", "按数据库基础到项目实践推进",
                "steps", List.of(Map.of("stepId", "step-1", "title", "SQL 基础"))
            ),
            "resourcePushPlan", Map.of("stepResources", List.of())
        ));

        SmartEngineTaskRepository taskRepository = mock(SmartEngineTaskRepository.class);
        when(taskRepository.findFirstByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.PERSONALIZED_LEARNING))
            .thenReturn(Optional.empty());
        when(taskRepository.findFirstByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.RESOURCE_PUSH))
            .thenReturn(Optional.empty());
        when(taskRepository.findTop5ByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.PERSONALIZED_LEARNING))
            .thenReturn(List.of(personalizedTask));
        when(taskRepository.findTop5ByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.RESOURCE_PUSH))
            .thenReturn(List.of());

        LearningPathQueryService service = new LearningPathQueryService(
            jdbcTemplate,
            taskRepository,
            mock(TaskStateMachineService.class),
            new ObjectMapper()
        );

        LearningPathCurrentResponse response = service.getCurrent(userId);

        assertThat(response.status()).isEqualTo("ACTIVE");
        assertThat(response.triggerSource()).isEqualTo("TASK_RESPONSE_FALLBACK");
        assertThat(response.learningPath()).containsKey("steps");
        assertThat((List<?>) response.learningPath().get("steps")).hasSize(1);
        assertThat(response.activeStep()).containsEntry("stepId", "step-1");
        assertThat(response.activeStep()).containsEntry("title", "SQL 基础");
    }

    @Test
    void returnsActiveStepOnlyWhenStepStatusIsActive() {
        UUID userId = UUID.fromString("40000000-0000-0000-0000-000000000006");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());

        SmartEngineTask personalizedTask = task(userId, ServiceType.PERSONALIZED_LEARNING);
        personalizedTask.setResponseSummary(Map.of(
            "learningPath", Map.of(
                "steps", List.of(
                    Map.of("stepId", "step-1", "title", "SQL 基础", "status", "NOT_STARTED"),
                    Map.of("stepId", "step-2", "title", "索引优化", "status", "IN_PROGRESS")
                )
            )
        ));

        SmartEngineTaskRepository taskRepository = mock(SmartEngineTaskRepository.class);
        when(taskRepository.findFirstByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.PERSONALIZED_LEARNING))
            .thenReturn(Optional.empty());
        when(taskRepository.findFirstByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.RESOURCE_PUSH))
            .thenReturn(Optional.empty());
        when(taskRepository.findTop5ByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.PERSONALIZED_LEARNING))
            .thenReturn(List.of(personalizedTask));
        when(taskRepository.findTop5ByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.RESOURCE_PUSH))
            .thenReturn(List.of());

        LearningPathQueryService service = new LearningPathQueryService(
            jdbcTemplate,
            taskRepository,
            mock(TaskStateMachineService.class),
            new ObjectMapper()
        );

        LearningPathCurrentResponse response = service.getCurrent(userId);

        assertThat(response.activeStep()).containsEntry("stepId", "step-2");
        assertThat(response.activeStep()).containsEntry("title", "索引优化");
    }

    @Test
    void doesNotTreatInactiveStepAsActive() {
        UUID userId = UUID.fromString("40000000-0000-0000-0000-000000000007");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());

        SmartEngineTask personalizedTask = task(userId, ServiceType.PERSONALIZED_LEARNING);
        personalizedTask.setResponseSummary(Map.of(
            "learningPath", Map.of(
                "steps", List.of(Map.of("stepId", "step-1", "title", "SQL 基础", "status", "INACTIVE"))
            )
        ));

        SmartEngineTaskRepository taskRepository = mock(SmartEngineTaskRepository.class);
        when(taskRepository.findFirstByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.PERSONALIZED_LEARNING))
            .thenReturn(Optional.empty());
        when(taskRepository.findFirstByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.RESOURCE_PUSH))
            .thenReturn(Optional.empty());
        when(taskRepository.findTop5ByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.PERSONALIZED_LEARNING))
            .thenReturn(List.of(personalizedTask));
        when(taskRepository.findTop5ByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.RESOURCE_PUSH))
            .thenReturn(List.of());

        LearningPathQueryService service = new LearningPathQueryService(
            jdbcTemplate,
            taskRepository,
            mock(TaskStateMachineService.class),
            new ObjectMapper()
        );

        assertThat(service.getCurrent(userId).activeStep()).isNull();
    }

    @Test
    void returnsTimeoutForStaleLiveRefreshTask() {
        UUID userId = UUID.fromString("40000000-0000-0000-0000-000000000009");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());

        SmartEngineTask staleTask = task(userId, ServiceType.PERSONALIZED_LEARNING);
        staleTask.setTaskStatus(TaskStatus.RUNNING);
        ReflectionTestUtils.setField(staleTask, "createdAt", OffsetDateTime.now().minusMinutes(31));
        SmartEngineTaskRepository taskRepository = mock(SmartEngineTaskRepository.class);
        when(taskRepository.findFirstByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.PERSONALIZED_LEARNING))
            .thenReturn(Optional.of(staleTask));
        when(taskRepository.findFirstByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.RESOURCE_PUSH))
            .thenReturn(Optional.empty());
        when(taskRepository.findTop5ByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.PERSONALIZED_LEARNING))
            .thenReturn(List.of(staleTask));
        when(taskRepository.findTop5ByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.RESOURCE_PUSH))
            .thenReturn(List.of());

        LearningPathQueryService service = new LearningPathQueryService(
            jdbcTemplate,
            taskRepository,
            mock(TaskStateMachineService.class),
            new ObjectMapper()
        );

        LearningPathCurrentResponse response = service.getCurrent(userId);

        assertThat(response.refreshTask()).isNotNull();
        assertThat(response.refreshTask().status()).isEqualTo(TaskStatus.TIMEOUT);
        assertThat(response.refreshTask().errorCode()).isEqualTo("TASK_STALE");
    }

    @Test
    void returnsNoActiveStepWhenAllStepsAreCompleted() {
        UUID userId = UUID.fromString("40000000-0000-0000-0000-000000000008");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());

        SmartEngineTask personalizedTask = task(userId, ServiceType.PERSONALIZED_LEARNING);
        personalizedTask.setResponseSummary(Map.of(
            "learningPath", Map.of(
                "steps", List.of(
                    Map.of("stepId", "step-1", "title", "SQL 基础", "status", "COMPLETED"),
                    Map.of("stepId", "step-2", "title", "联合索引", "status", "COMPLETED")
                )
            )
        ));

        SmartEngineTaskRepository taskRepository = mock(SmartEngineTaskRepository.class);
        when(taskRepository.findFirstByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.PERSONALIZED_LEARNING))
            .thenReturn(Optional.empty());
        when(taskRepository.findFirstByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.RESOURCE_PUSH))
            .thenReturn(Optional.empty());
        when(taskRepository.findTop5ByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.PERSONALIZED_LEARNING))
            .thenReturn(List.of(personalizedTask));
        when(taskRepository.findTop5ByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.RESOURCE_PUSH))
            .thenReturn(List.of());

        LearningPathQueryService service = new LearningPathQueryService(
            jdbcTemplate,
            taskRepository,
            mock(TaskStateMachineService.class),
            new ObjectMapper()
        );

        assertThat(service.getCurrent(userId).activeStep()).isNull();
    }

    @Test
    void alignsResourcePlanToLearningPathAndFiltersOldFrontendQuizResources() {
        UUID userId = UUID.fromString("40000000-0000-0000-0000-000000000010");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());
        when(jdbcTemplate.queryForList(anyString(), any(MapSqlParameterSource.class)))
            .thenReturn(List.of(Map.of(
                "title", "Redirecting…",
                "summary_text", "torch.nn.Linear deep learning neural network layer",
                "resource_type", "READING",
                "display_type", "DOCUMENT",
                "source_url", "https://docs.pytorch.org/docs/stable/generated/torch.nn.Linear.html",
                "source_name", "PyTorch Documentation",
                "cs_category", "AI_ML",
                "tags_text", "[\"pytorch\",\"deep-learning\"]",
                "quality_score", 0.9
            )));

        SmartEngineTask personalizedTask = task(userId, ServiceType.PERSONALIZED_LEARNING);
        personalizedTask.setResponseSummary(Map.of(
            "learningPath", Map.of(
                "goal", "深度学习核心理论基础",
                "steps", List.of(Map.of(
                    "stepId", "step-1",
                    "title", "深度学习核心概念学习",
                    "status", "IN_PROGRESS",
                    "targetKnowledgePoints", List.of("神经网络", "损失函数", "优化器")
                ))
            ),
            "resourcePushPlan", Map.of(
                "stepResources", List.of(Map.of(
                    "stepId", "step-1",
                    "resources", List.of(
                        Map.of(
                            "title", "JavaScript 前端课程",
                            "resourceType", "DOCUMENT",
                            "downloadUrl", "https://developer.mozilla.org/zh-CN/docs/Web/JavaScript"
                        ),
                        Map.of(
                            "title", "深度学习练习题",
                            "resourceType", "QUIZ",
                            "downloadUrl", "https://example.com/quiz"
                        )
                    )
                ))
            )
        ));

        SmartEngineTaskRepository taskRepository = mock(SmartEngineTaskRepository.class);
        when(taskRepository.findFirstByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.PERSONALIZED_LEARNING))
            .thenReturn(Optional.empty());
        when(taskRepository.findFirstByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.RESOURCE_PUSH))
            .thenReturn(Optional.empty());
        when(taskRepository.findTop5ByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.PERSONALIZED_LEARNING))
            .thenReturn(List.of(personalizedTask));
        when(taskRepository.findTop5ByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.RESOURCE_PUSH))
            .thenReturn(List.of());

        LearningPathQueryService service = new LearningPathQueryService(
            jdbcTemplate,
            taskRepository,
            mock(TaskStateMachineService.class),
            new ObjectMapper()
        );

        LearningPathCurrentResponse response = service.getCurrent(userId);
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> stepResources = (List<Map<String, Object>>) response.resourcePushPlan().get("stepResources");
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> resources = (List<Map<String, Object>>) stepResources.getFirst().get("resources");

        assertThat(resources).hasSize(1);
        assertThat(resources.getFirst()).containsEntry("sourceName", "PyTorch Documentation");
        assertThat(resources.getFirst()).containsEntry("resourceType", "DOCUMENT");
        assertThat(resources.getFirst().get("title")).asString().contains("PyTorch");
        assertThat(response.pushedResources()).hasSize(1);
        verify(jdbcTemplate).queryForList(
            org.mockito.ArgumentMatchers.argThat((String sql) ->
                sql.contains("metadata_json ->> 'csCategory'")
                    && sql.contains("resource_type::text NOT IN ('QUIZ', 'PRACTICE')")
                    && sql.contains("accessibilityStatus")
            ),
            org.mockito.ArgumentMatchers.<MapSqlParameterSource>argThat(params ->
                "AI_ML".equals(params.getValue("category"))
            )
        );
    }

    @Test
    void alignsResourcePlanWhenLearningPathUsesStagesInsteadOfSteps() {
        UUID userId = UUID.fromString("40000000-0000-0000-0000-000000000011");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());
        when(jdbcTemplate.queryForList(anyString(), any(MapSqlParameterSource.class)))
            .thenReturn(List.of(Map.of(
                "title", "PyTorch autograd",
                "summary_text", "deep learning automatic differentiation",
                "resource_type", "READING",
                "display_type", "DOCUMENT",
                "source_url", "https://docs.pytorch.org/docs/stable/autograd.html",
                "source_name", "PyTorch Documentation",
                "cs_category", "AI_ML",
                "tags_text", "[\"pytorch\",\"deep-learning\"]",
                "quality_score", 0.9
            )));

        SmartEngineTask personalizedTask = task(userId, ServiceType.PERSONALIZED_LEARNING);
        personalizedTask.setResponseSummary(Map.of(
            "learningPath", Map.of(
                "goal", "deep learning neural network PyTorch",
                "stages", List.of(Map.of(
                    "title", "Deep learning foundations",
                    "description", "neural networks and backpropagation",
                    "steps", List.of(Map.of(
                        "title", "PyTorch tensors and autograd",
                        "description", "automatic differentiation"
                    ))
                ))
            )
        ));

        SmartEngineTaskRepository taskRepository = mock(SmartEngineTaskRepository.class);
        when(taskRepository.findFirstByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.PERSONALIZED_LEARNING))
            .thenReturn(Optional.empty());
        when(taskRepository.findFirstByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.RESOURCE_PUSH))
            .thenReturn(Optional.empty());
        when(taskRepository.findTop5ByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.PERSONALIZED_LEARNING))
            .thenReturn(List.of(personalizedTask));
        when(taskRepository.findTop5ByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.RESOURCE_PUSH))
            .thenReturn(List.of());

        LearningPathQueryService service = new LearningPathQueryService(
            jdbcTemplate,
            taskRepository,
            mock(TaskStateMachineService.class),
            new ObjectMapper()
        );

        LearningPathCurrentResponse response = service.getCurrent(userId);

        assertThat(response.learningPath()).containsKey("steps");
        assertThat(response.activeStep()).containsEntry("stepId", "step-1");
        assertThat(response.pushedResources()).hasSize(1);
        assertThat(response.pushedResources().getFirst()).containsEntry("csCategory", "AI_ML");
    }

    private SmartEngineTask task(UUID userId, ServiceType serviceType) {
        SmartEngineTask task = new SmartEngineTask();
        task.setId(UUID.randomUUID());
        task.setTraceId(UUID.randomUUID().toString());
        task.setUserId(userId);
        task.setServiceType(serviceType);
        task.setTaskStatus(TaskStatus.COMPLETED);
        return task;
    }
}
