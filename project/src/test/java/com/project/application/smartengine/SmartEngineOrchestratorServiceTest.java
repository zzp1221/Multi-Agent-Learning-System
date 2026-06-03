package com.project.application.smartengine;

import com.project.api.smartengine.dto.SubmitTaskRequest;
import com.project.application.audit.AuditService;
import com.project.application.idempotency.IdempotencyService;
import com.project.domain.profile.UserProfileCurrentRepository;
import com.project.domain.task.ServiceType;
import com.project.domain.task.SmartEngineTask;
import com.project.domain.task.TaskStatus;
import com.project.security.JwtAuthenticatedUser;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.ObjectProvider;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class SmartEngineOrchestratorServiceTest {

    @Test
    void personalizedLearningSubmissionInjectsServerSideContext() {
        UUID userId = UUID.fromString("30000000-0000-0000-0000-000000000001");
        UUID conversationId = UUID.fromString("30000000-0000-0000-0000-000000000002");
        JwtAuthenticatedUser user = new JwtAuthenticatedUser(userId, "learner", "STUDENT");

        TaskStateMachineService taskStateMachineService = mock(TaskStateMachineService.class);
        SseEmitterService sseEmitterService = mock(SseEmitterService.class);
        SmartEngineQueueService queueService = mock(SmartEngineQueueService.class);
        @SuppressWarnings("unchecked")
        ObjectProvider<SmartEngineQueueService> queueProvider = mock(ObjectProvider.class);
        IdempotencyService idempotencyService = mock(IdempotencyService.class);
        AuditService auditService = mock(AuditService.class);
        UserProfileCurrentRepository profileRepository = mock(UserProfileCurrentRepository.class);
        PersonalizedLearningContextService contextService = mock(PersonalizedLearningContextService.class);

        SmartEngineTask task = pendingTask(userId);
        when(taskStateMachineService.createTask(any(), eq(userId), any(), eq(ServiceType.PERSONALIZED_LEARNING), any()))
            .thenReturn(task);
        when(queueProvider.getIfAvailable()).thenReturn(queueService);
        when(queueService.enqueue(any())).thenReturn("stream-record-1");
        when(idempotencyService.findExisting(any(), any(), any())).thenReturn(Optional.empty());

        Map<String, Object> automaticContext = new LinkedHashMap<>();
        automaticContext.put("profile", Map.of("knowledgeBase", "INTERMEDIATE"));
        automaticContext.put("profileSummary", "数据库基础中等");
        automaticContext.put("learningProgress", Map.of("dataAvailable", true));
        automaticContext.put("practiceSignals", Map.of("dataAvailable", true));
        automaticContext.put("resourceSignals", Map.of("dataAvailable", true));
        when(contextService.buildContext(userId)).thenReturn(automaticContext);

        SmartEngineOrchestratorService service = new SmartEngineOrchestratorService(
            taskStateMachineService,
            sseEmitterService,
            queueProvider,
            idempotencyService,
            auditService,
            profileRepository,
            contextService
        );

        service.submit(user, new SubmitTaskRequest(
            conversationId,
            ServiceType.PERSONALIZED_LEARNING,
            Map.of("topic", "事务隔离")
        ));

        verify(queueService).enqueue(org.mockito.ArgumentMatchers.argThat(invocation -> {
            assertThat(invocation.userId()).isEqualTo(userId);
            assertThat(invocation.conversationId()).isEqualTo(conversationId);
            assertThat(invocation.serviceType()).isEqualTo(ServiceType.PERSONALIZED_LEARNING);
            assertThat(invocation.params())
                .containsEntry("topic", "事务隔离")
                .containsKeys("profile", "profileSummary", "learningProgress", "practiceSignals", "resourceSignals");
            return true;
        }));
    }

    private SmartEngineTask pendingTask(UUID userId) {
        SmartEngineTask task = new SmartEngineTask();
        task.setId(UUID.fromString("30000000-0000-0000-0000-000000000003"));
        task.setTraceId("trace-1");
        task.setUserId(userId);
        task.setServiceType(ServiceType.PERSONALIZED_LEARNING);
        task.setTaskStatus(TaskStatus.PENDING);
        return task;
    }
}
