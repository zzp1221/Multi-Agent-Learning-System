package com.project.application.smartengine;

import com.project.api.smartengine.dto.SubmitTaskRequest;
import com.project.application.audit.AuditService;
import com.project.application.idempotency.IdempotencyService;
import com.project.application.learningpath.LearningPathProgressService;
import com.project.application.learningpath.PersonalizedLearningRefreshService;
import com.project.application.settings.UserLlmSettingsService;
import com.project.domain.conversation.QnaSession;
import com.project.domain.conversation.QnaSessionRepository;
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
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class SmartEngineOrchestratorServiceTest {

    @Test
    void formalUserWithoutLlmConfigIsRejectedBeforeTaskCreation() {
        UUID userId = UUID.fromString("30000000-0000-0000-0000-000000000031");
        JwtAuthenticatedUser user = new JwtAuthenticatedUser(userId, "learner", "STUDENT");
        TaskStateMachineService taskStateMachineService = mock(TaskStateMachineService.class);
        @SuppressWarnings("unchecked")
        ObjectProvider<SmartEngineQueueService> queueProvider = mock(ObjectProvider.class);
        UserLlmSettingsService userLlmSettingsService = mock(UserLlmSettingsService.class);
        when(userLlmSettingsService.isUserLlmReadyOrAllowedFallback(userId)).thenReturn(false);

        SmartEngineOrchestratorService service = new SmartEngineOrchestratorService(
            taskStateMachineService,
            mock(SseEmitterService.class),
            queueProvider,
            mock(IdempotencyService.class),
            mock(AuditService.class),
            mock(UserProfileCurrentRepository.class),
            mock(PersonalizedLearningContextService.class),
            mock(PersonalizedLearningRefreshService.class),
            mock(LearningPathProgressService.class),
            mock(PracticeResultPersistenceService.class),
            userLlmSettingsService,
            mock(QnaSessionRepository.class)
        );

        assertThatThrownBy(() -> service.submit(user, new SubmitTaskRequest(
            UUID.fromString("30000000-0000-0000-0000-000000000032"),
            ServiceType.PERSONALIZED_LEARNING,
            Map.of("topic", "database index")
        )))
            .isInstanceOf(com.project.application.common.ApplicationException.class)
            .hasMessage("请先配置并保存模型和 API Key 后再使用智能功能。")
            .satisfies(error -> assertThat(((com.project.application.common.ApplicationException) error).getCode())
                .isEqualTo("USER_LLM_REQUIRED"));

        verify(taskStateMachineService, never()).createTask(any(), any(), any(), any(), any());
        verify(queueProvider, never()).getIfAvailable();
    }

    @Test
    void missingConversationIsRejectedBeforeTaskCreation() {
        UUID userId = UUID.fromString("30000000-0000-0000-0000-000000000041");
        UUID conversationId = UUID.fromString("30000000-0000-0000-0000-000000000042");
        JwtAuthenticatedUser user = new JwtAuthenticatedUser(userId, "learner", "STUDENT");
        TaskStateMachineService taskStateMachineService = mock(TaskStateMachineService.class);
        @SuppressWarnings("unchecked")
        ObjectProvider<SmartEngineQueueService> queueProvider = mock(ObjectProvider.class);
        IdempotencyService idempotencyService = mock(IdempotencyService.class);
        UserLlmSettingsService userLlmSettingsService = mock(UserLlmSettingsService.class);
        QnaSessionRepository qnaSessionRepository = mock(QnaSessionRepository.class);
        when(userLlmSettingsService.isUserLlmReadyOrAllowedFallback(userId)).thenReturn(true);
        when(qnaSessionRepository.findByIdAndUserId(conversationId, userId)).thenReturn(Optional.empty());

        SmartEngineOrchestratorService service = new SmartEngineOrchestratorService(
            taskStateMachineService,
            mock(SseEmitterService.class),
            queueProvider,
            idempotencyService,
            mock(AuditService.class),
            mock(UserProfileCurrentRepository.class),
            mock(PersonalizedLearningContextService.class),
            mock(PersonalizedLearningRefreshService.class),
            mock(LearningPathProgressService.class),
            mock(PracticeResultPersistenceService.class),
            userLlmSettingsService,
            qnaSessionRepository
        );

        assertThatThrownBy(() -> service.submit(user, new SubmitTaskRequest(
            conversationId,
            ServiceType.PERSONALIZED_LEARNING,
            Map.of("topic", "database index")
        )))
            .isInstanceOf(com.project.application.common.ApplicationException.class)
            .satisfies(error -> assertThat(((com.project.application.common.ApplicationException) error).getCode())
                .isEqualTo("CONVERSATION_NOT_FOUND"));

        verify(taskStateMachineService, never()).createTask(any(), any(), any(), any(), any());
        verify(idempotencyService, never()).reserve(any(), any(), any(), any());
        verify(queueProvider, never()).getIfAvailable();
    }

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
        PersonalizedLearningRefreshService refreshService = mock(PersonalizedLearningRefreshService.class);
        LearningPathProgressService progressService = mock(LearningPathProgressService.class);
        PracticeResultPersistenceService practiceResultPersistenceService = mock(PracticeResultPersistenceService.class);
        UserLlmSettingsService userLlmSettingsService = mock(UserLlmSettingsService.class);
        QnaSessionRepository qnaSessionRepository = mock(QnaSessionRepository.class);

        SmartEngineTask task = pendingTask(userId);
        when(taskStateMachineService.createTask(any(), eq(userId), any(), eq(ServiceType.PERSONALIZED_LEARNING), any()))
            .thenReturn(task);
        when(queueProvider.getIfAvailable()).thenReturn(queueService);
        when(queueService.enqueue(any())).thenReturn("stream-record-1");
        when(idempotencyService.findExisting(any(), any(), any())).thenReturn(Optional.empty());
        when(userLlmSettingsService.isUserLlmReadyOrAllowedFallback(userId)).thenReturn(true);
        when(qnaSessionRepository.findByIdAndUserId(conversationId, userId)).thenReturn(Optional.of(mock(QnaSession.class)));

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
            contextService,
            refreshService,
            progressService,
            practiceResultPersistenceService,
            userLlmSettingsService,
            qnaSessionRepository
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

    @Test
    void completedStageTestTriggersLearningPathProgressWithoutPracticeRefresh() {
        UUID userId = UUID.fromString("30000000-0000-0000-0000-000000000011");
        UUID taskId = UUID.fromString("30000000-0000-0000-0000-000000000012");
        TaskStateMachineService taskStateMachineService = mock(TaskStateMachineService.class);
        SseEmitterService sseEmitterService = mock(SseEmitterService.class);
        @SuppressWarnings("unchecked")
        ObjectProvider<SmartEngineQueueService> queueProvider = mock(ObjectProvider.class);
        PersonalizedLearningRefreshService refreshService = mock(PersonalizedLearningRefreshService.class);
        LearningPathProgressService progressService = mock(LearningPathProgressService.class);
        PracticeResultPersistenceService practiceResultPersistenceService = mock(PracticeResultPersistenceService.class);
        AuditService auditService = mock(AuditService.class);

        SmartEngineTask task = completedPracticeTask(userId, taskId);
        when(taskStateMachineService.recordPythonEvent(eq(taskId), any(), eq(9)))
            .thenReturn(new TaskEventRecordResult(
                new TaskStreamEventPayload(
                    "done",
                    taskId,
                    "trace-stage-test",
                    9,
                    java.time.OffsetDateTime.now(),
                    Map.of("status", "SUCCESS", "summary", "判题完成")
                ),
                true
        ));
        when(taskStateMachineService.getTask(taskId)).thenReturn(task);
        when(progressService.handleStageTestResult(userId, taskId)).thenReturn(true);
        when(practiceResultPersistenceService.persistCompletedPracticeJudgeResult(task)).thenReturn(3);

        SmartEngineOrchestratorService service = new SmartEngineOrchestratorService(
            taskStateMachineService,
            sseEmitterService,
            queueProvider,
            mock(IdempotencyService.class),
            auditService,
            mock(UserProfileCurrentRepository.class),
            mock(PersonalizedLearningContextService.class),
            refreshService,
            progressService,
            practiceResultPersistenceService,
            null,
            mock(QnaSessionRepository.class)
        );

        service.recordWorkerEvent(taskId, new PythonStreamEvent("done", "judge", Map.of("status", "SUCCESS")), 9);

        verify(practiceResultPersistenceService).persistCompletedPracticeJudgeResult(task);
        verify(auditService).log(eq("TASK"), eq("LOW"), eq("Persisted practice judge result"), eq(userId), eq(taskId), eq(Map.of("itemCount", 3)));
        verify(progressService).handleStageTestResult(userId, taskId);
        verify(refreshService, never()).triggerPracticeRefresh(any(), any());
    }

    @Test
    void completedOrdinaryPracticeStillTriggersPracticeRefresh() {
        UUID userId = UUID.fromString("30000000-0000-0000-0000-000000000021");
        UUID taskId = UUID.fromString("30000000-0000-0000-0000-000000000022");
        TaskStateMachineService taskStateMachineService = mock(TaskStateMachineService.class);
        SseEmitterService sseEmitterService = mock(SseEmitterService.class);
        @SuppressWarnings("unchecked")
        ObjectProvider<SmartEngineQueueService> queueProvider = mock(ObjectProvider.class);
        PersonalizedLearningRefreshService refreshService = mock(PersonalizedLearningRefreshService.class);
        LearningPathProgressService progressService = mock(LearningPathProgressService.class);
        PracticeResultPersistenceService practiceResultPersistenceService = mock(PracticeResultPersistenceService.class);

        SmartEngineTask task = completedPracticeTask(userId, taskId);
        when(taskStateMachineService.recordPythonEvent(eq(taskId), any(), eq(11)))
            .thenReturn(new TaskEventRecordResult(
                new TaskStreamEventPayload(
                    "done",
                    taskId,
                    "trace-practice",
                    11,
                    java.time.OffsetDateTime.now(),
                    Map.of("status", "SUCCESS", "summary", "判题完成")
                ),
                true
            ));
        when(taskStateMachineService.getTask(taskId)).thenReturn(task);
        when(progressService.handleStageTestResult(userId, taskId)).thenReturn(false);

        SmartEngineOrchestratorService service = new SmartEngineOrchestratorService(
            taskStateMachineService,
            sseEmitterService,
            queueProvider,
            mock(IdempotencyService.class),
            mock(AuditService.class),
            mock(UserProfileCurrentRepository.class),
            mock(PersonalizedLearningContextService.class),
            refreshService,
            progressService,
            practiceResultPersistenceService,
            null,
            mock(QnaSessionRepository.class)
        );

        service.recordWorkerEvent(taskId, new PythonStreamEvent("done", "judge", Map.of("status", "SUCCESS")), 11);

        verify(practiceResultPersistenceService).persistCompletedPracticeJudgeResult(task);
        verify(progressService).handleStageTestResult(userId, taskId);
        verify(refreshService).triggerPracticeRefresh(userId, "practice_judge_completed");
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

    private SmartEngineTask completedPracticeTask(UUID userId, UUID taskId) {
        SmartEngineTask task = new SmartEngineTask();
        task.setId(taskId);
        task.setTraceId("trace-stage-test");
        task.setUserId(userId);
        task.setServiceType(ServiceType.PRACTICE_JUDGE);
        task.setTaskStatus(TaskStatus.COMPLETED);
        return task;
    }
}
