package com.project.application.learningpath;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.learningpath.dto.LearningPathCurrentResponse;
import com.project.application.resource.ResourceSemanticWarmupService;
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
            new ObjectMapper(),
            mock(ResourceSemanticWarmupService.class)
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
            new ObjectMapper(),
            mock(ResourceSemanticWarmupService.class)
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
            new ObjectMapper(),
            mock(ResourceSemanticWarmupService.class)
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
            new ObjectMapper(),
            mock(ResourceSemanticWarmupService.class)
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
            new ObjectMapper(),
            mock(ResourceSemanticWarmupService.class)
        );

        assertThat(service.getCurrent(userId).activeStep()).isNull();
    }

    @Test
    void alignsResourcePlanToLearningPathAndFiltersInvalidTavilyResources() {
        UUID userId = UUID.fromString("40000000-0000-0000-0000-000000000010");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());

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
                            "title", "深度学习核心概念讲解",
                            "resourceType", "DOCUMENT",
                            "downloadUrl", "https://example.com/deep-learning-intro",
                            "source", "tavily"
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
            new ObjectMapper(),
            mock(ResourceSemanticWarmupService.class)
        );

        LearningPathCurrentResponse response = service.getCurrent(userId);
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> stepResources = (List<Map<String, Object>>) response.resourcePushPlan().get("stepResources");
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> resources = (List<Map<String, Object>>) stepResources.getFirst().get("resources");

        assertThat(resources).hasSize(1);
        assertThat(resources.getFirst()).containsEntry("title", "深度学习核心概念讲解");
        assertThat(resources.getFirst()).containsEntry("resourceType", "DOCUMENT");
        assertThat(response.pushedResources()).hasSize(1);
    }

    @Test
    void returnsCoverageGapWhenTavilyPlanHasNoUsableResources() {
        UUID userId = UUID.fromString("40000000-0000-0000-0000-000000000011");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());

        SmartEngineTask personalizedTask = task(userId, ServiceType.PERSONALIZED_LEARNING);
        personalizedTask.setResponseSummary(Map.of(
            "learningPath", Map.of(
                "goal", "图着色算法学习",
                "stages", List.of(Map.of(
                    "title", "图着色基础",
                    "description", "回溯搜索",
                    "steps", List.of(Map.of(
                        "title", "图着色建模",
                        "description", "理解约束建模"
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
            new ObjectMapper(),
            mock(ResourceSemanticWarmupService.class)
        );

        LearningPathCurrentResponse response = service.getCurrent(userId);

        assertThat(response.learningPath()).containsKey("steps");
        assertThat(response.activeStep()).containsEntry("stepId", "step-1");
        assertThat(response.pushedResources()).isEmpty();
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> gaps = (List<Map<String, Object>>) response.resourcePushPlan().get("coverageGaps");
        assertThat(gaps).hasSize(1);
        assertThat(gaps.getFirst().get("reason")).asString().contains("Tavily");
    }

    @Test
    void keepsTavilyResourcesWithoutTopicSpecificLibraryReplacement() {
        UUID userId = UUID.fromString("40000000-0000-0000-0000-000000000012");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());

        SmartEngineTask personalizedTask = task(userId, ServiceType.PERSONALIZED_LEARNING);
        personalizedTask.setResponseSummary(Map.of(
            "learningPath", Map.of(
                "goal", "Java 并发编程基础",
                "steps", List.of(Map.of(
                    "stepId", "step-1",
                    "title", "Java线程创建基础概念学习",
                    "status", "IN_PROGRESS",
                    "targetKnowledgePoints", List.of("Thread类", "Runnable接口", "synchronized", "volatile")
                ))
            ),
            "resourcePushPlan", Map.of(
                "stepResources", List.of(Map.of(
                    "stepId", "step-1",
                    "resources", List.of(Map.of(
                        "title", "Java Thread and Runnable Tutorial",
                        "resourceType", "DOCUMENT",
                        "downloadUrl", "https://docs.oracle.com/javase/tutorial/essential/concurrency/runthread.html",
                        "source", "tavily",
                        "summaryText", "Thread Runnable synchronized volatile"
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
            new ObjectMapper(),
            mock(ResourceSemanticWarmupService.class)
        );

        LearningPathCurrentResponse response = service.getCurrent(userId);

        assertThat(response.pushedResources()).hasSize(1);
        assertThat(response.pushedResources().getFirst().get("title")).asString().contains("Java");
    }

    @Test
    void attachesLatestTopLevelPushedResourcesToActiveStepWhenPlanResourcesAreEmpty() {
        UUID userId = UUID.fromString("40000000-0000-0000-0000-000000000013");
        NamedParameterJdbcTemplate jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        when(jdbcTemplate.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenReturn(List.of());

        SmartEngineTask personalizedTask = task(userId, ServiceType.PERSONALIZED_LEARNING);
        personalizedTask.setResponseSummary(Map.of(
            "learningPath", Map.of(
                "goal", "Java concurrency",
                "steps", List.of(
                    Map.of("stepId", "step-1", "title", "Thread basics", "status", "IN_PROGRESS"),
                    Map.of("stepId", "step-2", "title", "Synchronization", "status", "NOT_STARTED")
                )
            )
        ));
        SmartEngineTask resourceTask = task(userId, ServiceType.RESOURCE_PUSH);
        resourceTask.setResponseSummary(Map.of(
            "resourcePushPlan", Map.of(
                "stepResources", List.of(Map.of("stepId", "step-1", "resources", List.of()))
            ),
            "pushedResources", List.of(Map.of(
                "title", "Java Thread Tutorial",
                "resourceType", "DOCUMENT",
                "downloadUrl", "https://docs.oracle.com/javase/tutorial/essential/concurrency/runthread.html",
                "summaryText", "Thread and Runnable"
            ))
        ));

        SmartEngineTaskRepository taskRepository = mock(SmartEngineTaskRepository.class);
        when(taskRepository.findFirstByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.PERSONALIZED_LEARNING))
            .thenReturn(Optional.empty());
        when(taskRepository.findFirstByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.RESOURCE_PUSH))
            .thenReturn(Optional.empty());
        when(taskRepository.findTop5ByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.PERSONALIZED_LEARNING))
            .thenReturn(List.of(personalizedTask));
        when(taskRepository.findTop5ByUserIdAndServiceTypeOrderByCreatedAtDesc(userId, ServiceType.RESOURCE_PUSH))
            .thenReturn(List.of(resourceTask));

        LearningPathQueryService service = new LearningPathQueryService(
            jdbcTemplate,
            taskRepository,
            mock(TaskStateMachineService.class),
            new ObjectMapper(),
            mock(ResourceSemanticWarmupService.class)
        );

        LearningPathCurrentResponse response = service.getCurrent(userId);
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> stepResources = (List<Map<String, Object>>) response.resourcePushPlan().get("stepResources");
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> firstStepResources = (List<Map<String, Object>>) stepResources.getFirst().get("resources");

        assertThat(firstStepResources).hasSize(1);
        assertThat(firstStepResources.getFirst()).containsEntry("title", "Java Thread Tutorial");
        assertThat(response.pushedResources()).hasSize(1);
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
