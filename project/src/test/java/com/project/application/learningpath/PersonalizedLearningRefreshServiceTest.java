package com.project.application.learningpath;

import com.project.application.audit.AuditService;
import com.project.application.smartengine.PersonalizedLearningContextService;
import com.project.application.smartengine.SmartEngineQueueService;
import com.project.application.smartengine.TaskStateMachineService;
import com.project.domain.task.ServiceType;
import com.project.domain.task.SmartEngineTask;
import com.project.domain.task.SmartEngineTaskRepository;
import com.project.domain.task.TaskStatus;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.ObjectProvider;

import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class PersonalizedLearningRefreshServiceTest {

    @Test
    void reusesActivePersonalizedLearningTask() {
        UUID userId = UUID.fromString("40000000-0000-0000-0000-000000000001");
        SmartEngineTask activeTask = task(userId, TaskStatus.RUNNING);
        SmartEngineTaskRepository taskRepository = mock(SmartEngineTaskRepository.class);
        when(taskRepository.findFirstByUserIdAndServiceTypeAndTaskStatusInOrderByCreatedAtDesc(
            eq(userId),
            eq(ServiceType.PERSONALIZED_LEARNING),
            any()
        )).thenReturn(Optional.of(activeTask));
        TaskStateMachineService stateMachineService = mock(TaskStateMachineService.class);
        SmartEngineQueueService queueService = mock(SmartEngineQueueService.class);
        @SuppressWarnings("unchecked")
        ObjectProvider<SmartEngineQueueService> queueProvider = mock(ObjectProvider.class);

        PersonalizedLearningRefreshService service = new PersonalizedLearningRefreshService(
            stateMachineService,
            taskRepository,
            queueProvider,
            mock(PersonalizedLearningContextService.class),
            mock(AuditService.class)
        );

        SmartEngineTask result = service.triggerRefresh(userId, "PRACTICE_PROGRESS", Map.of("topic", "练习变化"));

        assertThat(result).isSameAs(activeTask);
        verify(stateMachineService, never()).createTask(any(), any(), any(), any(), any());
        verify(queueService, never()).enqueue(any());
    }

    @Test
    void manualAdjustmentAlwaysCreatesNewTaskWithUserIntent() {
        UUID userId = UUID.fromString("40000000-0000-0000-0000-000000000003");
        SmartEngineTask createdTask = task(userId, TaskStatus.PENDING);
        SmartEngineTaskRepository taskRepository = mock(SmartEngineTaskRepository.class);
        TaskStateMachineService stateMachineService = mock(TaskStateMachineService.class);
        when(stateMachineService.createTask(any(), eq(userId), any(), eq(ServiceType.PERSONALIZED_LEARNING), any()))
            .thenReturn(createdTask);
        SmartEngineQueueService queueService = mock(SmartEngineQueueService.class);
        when(queueService.enqueue(any())).thenReturn("record-2");
        @SuppressWarnings("unchecked")
        ObjectProvider<SmartEngineQueueService> queueProvider = mock(ObjectProvider.class);
        when(queueProvider.getIfAvailable()).thenReturn(queueService);
        PersonalizedLearningContextService contextService = mock(PersonalizedLearningContextService.class);
        when(contextService.buildContext(userId)).thenReturn(Map.of());

        PersonalizedLearningRefreshService service = new PersonalizedLearningRefreshService(
            stateMachineService,
            taskRepository,
            queueProvider,
            contextService,
            mock(AuditService.class)
        );

        SmartEngineTask result = service.triggerManualAdjustment(userId, "先学索引");

        assertThat(result).isSameAs(createdTask);
        verify(queueService).enqueue(org.mockito.ArgumentMatchers.argThat(invocation ->
            "MANUAL_ADJUSTMENT".equals(invocation.params().get("triggerSource"))
                && "先学索引".equals(invocation.params().get("adjustmentIntent"))
        ));
    }

    @Test
    void resourceRecommendationRefreshCreatesResourcePushTaskWithLearningPath() {
        UUID userId = UUID.fromString("40000000-0000-0000-0000-000000000004");
        SmartEngineTask createdTask = task(userId, TaskStatus.PENDING);
        createdTask.setServiceType(ServiceType.RESOURCE_PUSH);
        SmartEngineTaskRepository taskRepository = mock(SmartEngineTaskRepository.class);
        when(taskRepository.findFirstByUserIdAndServiceTypeAndTaskStatusInOrderByCreatedAtDesc(
            eq(userId),
            eq(ServiceType.RESOURCE_PUSH),
            any()
        )).thenReturn(Optional.empty());
        TaskStateMachineService stateMachineService = mock(TaskStateMachineService.class);
        when(stateMachineService.createTask(any(), eq(userId), any(), eq(ServiceType.RESOURCE_PUSH), any()))
            .thenReturn(createdTask);
        SmartEngineQueueService queueService = mock(SmartEngineQueueService.class);
        when(queueService.enqueue(any())).thenReturn("record-resource");
        @SuppressWarnings("unchecked")
        ObjectProvider<SmartEngineQueueService> queueProvider = mock(ObjectProvider.class);
        when(queueProvider.getIfAvailable()).thenReturn(queueService);
        PersonalizedLearningContextService contextService = mock(PersonalizedLearningContextService.class);
        when(contextService.buildContext(userId)).thenReturn(Map.of("profileSummary", "计算机专业"));
        Map<String, Object> learningPath = Map.of("steps", java.util.List.of(Map.of("stepId", "step-1")));

        PersonalizedLearningRefreshService service = new PersonalizedLearningRefreshService(
            stateMachineService,
            taskRepository,
            queueProvider,
            contextService,
            mock(AuditService.class)
        );

        SmartEngineTask result = service.triggerResourceRecommendationRefresh(
            userId,
            "补充视频资源",
            learningPath,
            java.util.List.of(Map.of(
                "title",
                "旧视频资源",
                "downloadUrl",
                "https://example.com/old-video"
            ))
        );

        assertThat(result).isSameAs(createdTask);
        verify(queueService).enqueue(org.mockito.ArgumentMatchers.argThat(invocation ->
            invocation.serviceType() == ServiceType.RESOURCE_PUSH
                && "RESOURCE_RECOMMENDATION_REFRESH".equals(invocation.params().get("triggerSource"))
                && Boolean.TRUE.equals(invocation.params().get("resourceRefresh"))
                && "补充视频资源".equals(invocation.params().get("adjustmentIntent"))
                && invocation.params().get("learningPath") instanceof Map<?, ?>
                && invocation.params().get("previousResourceUrls") instanceof java.util.List<?> urls
                && urls.contains("https://example.com/old-video")
                && invocation.params().get("previousResourceTitles") instanceof java.util.List<?> titles
                && titles.contains("旧视频资源")
                && invocation.params().get("existingResources") instanceof java.util.List<?> existing
                && existing.size() == 1
        ));
    }

    @Test
    void enqueuesNewBackgroundPersonalizedLearningTask() {
        UUID userId = UUID.fromString("40000000-0000-0000-0000-000000000002");
        SmartEngineTask createdTask = task(userId, TaskStatus.PENDING);
        SmartEngineTaskRepository taskRepository = mock(SmartEngineTaskRepository.class);
        when(taskRepository.findFirstByUserIdAndServiceTypeAndTaskStatusInOrderByCreatedAtDesc(
            eq(userId),
            eq(ServiceType.PERSONALIZED_LEARNING),
            any()
        )).thenReturn(Optional.empty());
        TaskStateMachineService stateMachineService = mock(TaskStateMachineService.class);
        when(stateMachineService.createTask(any(), eq(userId), any(), eq(ServiceType.PERSONALIZED_LEARNING), any()))
            .thenReturn(createdTask);
        SmartEngineQueueService queueService = mock(SmartEngineQueueService.class);
        when(queueService.enqueue(any())).thenReturn("record-1");
        @SuppressWarnings("unchecked")
        ObjectProvider<SmartEngineQueueService> queueProvider = mock(ObjectProvider.class);
        when(queueProvider.getIfAvailable()).thenReturn(queueService);
        PersonalizedLearningContextService contextService = mock(PersonalizedLearningContextService.class);
        when(contextService.buildContext(userId)).thenReturn(Map.of("profileSummary", "计算机专业新生"));

        PersonalizedLearningRefreshService service = new PersonalizedLearningRefreshService(
            stateMachineService,
            taskRepository,
            queueProvider,
            contextService,
            mock(AuditService.class)
        );

        SmartEngineTask result = service.triggerRefresh(userId, "INITIAL_PROFILE", Map.of("topic", "CS"));

        assertThat(result).isSameAs(createdTask);
        verify(queueService).enqueue(any());
    }

    private SmartEngineTask task(UUID userId, TaskStatus status) {
        SmartEngineTask task = new SmartEngineTask();
        task.setId(UUID.randomUUID());
        task.setTraceId(UUID.randomUUID().toString());
        task.setUserId(userId);
        task.setServiceType(ServiceType.PERSONALIZED_LEARNING);
        task.setTaskStatus(status);
        return task;
    }
}
