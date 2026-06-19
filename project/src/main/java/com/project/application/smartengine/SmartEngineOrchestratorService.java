package com.project.application.smartengine;

import com.project.api.smartengine.dto.SubmitTaskRequest;
import com.project.api.smartengine.dto.SubmitTaskResponse;
import com.project.api.smartengine.dto.TaskStatusResponse;
import com.project.application.audit.AuditService;
import com.project.application.common.ApplicationException;
import com.project.application.idempotency.IdempotencyService;
import com.project.application.learningpath.LearningPathProgressService;
import com.project.application.learningpath.PersonalizedLearningRefreshService;
import com.project.application.settings.UserLlmSettingsService;
import com.project.domain.conversation.QnaSessionRepository;
import com.project.domain.profile.UserProfileCurrentRepository;
import com.project.domain.task.SmartEngineTask;
import com.project.domain.task.ServiceType;
import com.project.domain.task.TaskStatus;
import com.project.security.JwtAuthenticatedUser;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * Coordinates SmartEngine task submission, status, cancellation, and SSE replay.
 */
@Service
public class SmartEngineOrchestratorService {

    private static final Logger LOGGER = LoggerFactory.getLogger(SmartEngineOrchestratorService.class);
    private static final String USER_LLM_REQUIRED_MESSAGE = "请先配置并保存模型和 API Key 后再使用智能功能。";

    private final TaskStateMachineService taskStateMachineService;
    private final SseEmitterService sseEmitterService;
    private final ObjectProvider<SmartEngineQueueService> smartEngineQueueServiceProvider;
    private final IdempotencyService idempotencyService;
    private final AuditService auditService;
    private final UserProfileCurrentRepository userProfileCurrentRepository;
    private final PersonalizedLearningContextService personalizedLearningContextService;
    private final PersonalizedLearningRefreshService personalizedLearningRefreshService;
    private final LearningPathProgressService learningPathProgressService;
    private final PracticeResultPersistenceService practiceResultPersistenceService;
    private final UserLlmSettingsService userLlmSettingsService;
    private final QnaSessionRepository qnaSessionRepository;

    public SmartEngineOrchestratorService(
        TaskStateMachineService taskStateMachineService,
        SseEmitterService sseEmitterService,
        ObjectProvider<SmartEngineQueueService> smartEngineQueueServiceProvider,
        IdempotencyService idempotencyService,
        AuditService auditService,
        UserProfileCurrentRepository userProfileCurrentRepository,
        PersonalizedLearningContextService personalizedLearningContextService,
        PersonalizedLearningRefreshService personalizedLearningRefreshService,
        LearningPathProgressService learningPathProgressService,
        PracticeResultPersistenceService practiceResultPersistenceService,
        UserLlmSettingsService userLlmSettingsService,
        QnaSessionRepository qnaSessionRepository
    ) {
        this.taskStateMachineService = taskStateMachineService;
        this.sseEmitterService = sseEmitterService;
        this.smartEngineQueueServiceProvider = smartEngineQueueServiceProvider;
        this.idempotencyService = idempotencyService;
        this.auditService = auditService;
        this.userProfileCurrentRepository = userProfileCurrentRepository;
        this.personalizedLearningContextService = personalizedLearningContextService;
        this.personalizedLearningRefreshService = personalizedLearningRefreshService;
        this.learningPathProgressService = learningPathProgressService;
        this.practiceResultPersistenceService = practiceResultPersistenceService;
        this.userLlmSettingsService = userLlmSettingsService;
        this.qnaSessionRepository = qnaSessionRepository;
    }

    public SubmitTaskAcceptance submit(JwtAuthenticatedUser currentUser, SubmitTaskRequest request) {
        return submit(currentUser, request, null);
    }

    public SubmitTaskAcceptance submit(JwtAuthenticatedUser currentUser, SubmitTaskRequest request, String idempotencyKey) {
        if (idempotencyKey != null && !idempotencyKey.isBlank()) {
            return idempotencyService.findExisting(currentUser.userId(), "SMART_ENGINE_SUBMIT", idempotencyKey)
                .map(existingTaskId -> {
                    SmartEngineTask existingTask = taskStateMachineService.getOwnedTask(existingTaskId, currentUser.userId());
                    auditService.log("TASK", "LOW", "Idempotent SmartEngine submit replay", currentUser.userId(), existingTaskId, Map.of("serviceType", request.serviceType()));
                    return new SubmitTaskAcceptance(
                        new SubmitTaskResponse(existingTask.getId(), existingTask.getTraceId(), existingTask.getTaskStatus()),
                        true
                    );
                })
                .orElseGet(() -> createAndDispatchTask(currentUser, request, idempotencyKey));
        }

        return createAndDispatchTask(currentUser, request, null);
    }

    private SubmitTaskAcceptance createAndDispatchTask(
        JwtAuthenticatedUser currentUser,
        SubmitTaskRequest request,
        String idempotencyKey
    ) {
        requireUserLlmReady(currentUser.userId());
        requireOwnedConversation(currentUser.userId(), request.conversationId());
        UUID taskId = UUID.randomUUID();
        String traceId = UUID.randomUUID().toString();
        Map<String, Object> requestPayload = new LinkedHashMap<>();
        requestPayload.put("conversationId", request.conversationId());
        requestPayload.put("params", request.safeParams());

        if (idempotencyKey != null && !idempotencyKey.isBlank()) {
            boolean reserved = idempotencyService.reserve(
                currentUser.userId(),
                "SMART_ENGINE_SUBMIT",
                idempotencyKey,
                taskId
            );
            if (!reserved) {
                return idempotencyService.findExisting(currentUser.userId(), "SMART_ENGINE_SUBMIT", idempotencyKey)
                    .map(existingTaskId -> {
                        SmartEngineTask existingTask = taskStateMachineService.getOwnedTask(existingTaskId, currentUser.userId());
                        auditService.log("TASK", "LOW", "Idempotent SmartEngine submit replay", currentUser.userId(), existingTaskId, Map.of("serviceType", request.serviceType()));
                        return new SubmitTaskAcceptance(
                            new SubmitTaskResponse(existingTask.getId(), existingTask.getTraceId(), existingTask.getTaskStatus()),
                            true
                        );
                    })
                    .orElseThrow(() -> new IllegalStateException("Idempotency reservation failed without an existing record"));
            }
        }

        SmartEngineTask task = taskStateMachineService.createTask(
            taskId,
            currentUser.userId(),
            traceId,
            request.serviceType(),
            requestPayload
        );

        auditService.log("TASK", "INFO", "Created SmartEngine task", currentUser.userId(), task.getId(), Map.of("serviceType", request.serviceType()));

        Map<String, Object> invocationParams = buildInvocationParams(currentUser, request);

        SmartEngineInvocation invocation = new SmartEngineInvocation(
            currentUser.userId(),
            task.getId(),
            traceId,
            request.conversationId(),
            request.serviceType(),
            invocationParams
        );

        try {
            SmartEngineQueueService smartEngineQueueService = smartEngineQueueServiceProvider.getIfAvailable();
            if (smartEngineQueueService == null) {
                throw new IllegalStateException("SmartEngine task queue is unavailable");
            }
            String recordId = smartEngineQueueService.enqueue(invocation);
            auditService.log("TASK", "LOW", "Enqueued SmartEngine task", currentUser.userId(), task.getId(), Map.of(
                "serviceType", request.serviceType(),
                "streamRecordId", recordId
            ));
        } catch (Exception ex) {
            LOGGER.warn("Failed to enqueue SmartEngine task taskId={} traceId={}", task.getId(), traceId, ex);
            taskStateMachineService.failTask(
                task.getId(),
                "QUEUE_UNAVAILABLE",
                "SmartEngine task queue is unavailable"
            );
            auditService.log("TASK", "HIGH", "Failed to enqueue SmartEngine task", currentUser.userId(), task.getId(), Map.of(
                "serviceType", request.serviceType(),
                "message", ex.getMessage() == null ? "" : ex.getMessage()
            ));
            throw new ApplicationException("QUEUE_UNAVAILABLE", "SmartEngine task queue is unavailable", HttpStatus.SERVICE_UNAVAILABLE);
        }

        return new SubmitTaskAcceptance(
            new SubmitTaskResponse(task.getId(), traceId, task.getTaskStatus()),
            false
        );
    }

    private void requireUserLlmReady(UUID userId) {
        if (userLlmSettingsService == null || userLlmSettingsService.isUserLlmReadyOrAllowedFallback(userId)) {
            return;
        }
        throw new ApplicationException("USER_LLM_REQUIRED", USER_LLM_REQUIRED_MESSAGE, HttpStatus.PRECONDITION_REQUIRED);
    }

    private void requireOwnedConversation(UUID userId, UUID conversationId) {
        if (qnaSessionRepository.findByIdAndUserId(conversationId, userId).isPresent()) {
            return;
        }
        throw new ApplicationException("CONVERSATION_NOT_FOUND", "会话不存在", HttpStatus.NOT_FOUND);
    }

    private Map<String, Object> buildInvocationParams(JwtAuthenticatedUser currentUser, SubmitTaskRequest request) {
        Map<String, Object> invocationParams = new LinkedHashMap<>(request.safeParams());
        invocationParams.put("userId", currentUser.userId().toString());
        if (request.serviceType() == ServiceType.PERSONALIZED_LEARNING) {
            invocationParams.putAll(personalizedLearningContextService.buildContext(currentUser.userId()));
            invocationParams.put("userId", currentUser.userId().toString());
            return invocationParams;
        }

        userProfileCurrentRepository.findById(currentUser.userId())
            .ifPresent(profile -> {
                invocationParams.put("profile", new LinkedHashMap<>(profile.getProfileJson()));
                invocationParams.put("profileSummary", profile.getSummaryText());
            });
        return invocationParams;
    }

    public TaskStatusResponse getStatus(JwtAuthenticatedUser currentUser, UUID taskId) {
        return taskStateMachineService.getOwnedTaskStatus(taskId, currentUser.userId());
    }

    public SseEmitter subscribe(JwtAuthenticatedUser currentUser, UUID taskId) {
        SmartEngineTask task = taskStateMachineService.getOwnedTask(taskId, currentUser.userId());
        return sseEmitterService.subscribe(task);
    }

    public void cancel(JwtAuthenticatedUser currentUser, UUID taskId) {
        SmartEngineTask task = taskStateMachineService.getOwnedTask(taskId, currentUser.userId());

        if (task.isTerminal()) {
            return;
        }

        auditService.log("TASK", "MEDIUM", "Cancelled SmartEngine task", currentUser.userId(), taskId, Map.of(
            "currentStatus", task.getTaskStatus().name(),
            "currentStage", task.getCurrentStage() == null ? "" : task.getCurrentStage()
        ));

        TaskStreamEventPayload cancelPayload = taskStateMachineService.markCancelled(taskId);
        sseEmitterService.cancelTask(taskId, cancelPayload);

        try {
            SmartEngineQueueService smartEngineQueueService = smartEngineQueueServiceProvider.getIfAvailable();
            if (smartEngineQueueService != null) {
                smartEngineQueueService.markCancelled(taskId);
            }
        } catch (Exception ex) {
            LOGGER.warn("Failed to write SmartEngine cancel key taskId={}: {}", taskId, ex.getMessage());
        }
    }

    public void markWorkerStarted(UUID taskId) {
        taskStateMachineService.markRunning(taskId);
    }

    public TaskEventRecordResult recordWorkerEvent(UUID taskId, PythonStreamEvent event, int seq) {
        TaskEventRecordResult result = taskStateMachineService.recordPythonEvent(taskId, event, seq);
        if (result.created() && result.payload() != null) {
            sseEmitterService.publish(result.payload(), event.resolvedEventType().isTerminal());
            triggerLearningPathRefreshAfterPracticeDone(taskId, event, result.payload());
        }
        return result;
    }

    private void triggerLearningPathRefreshAfterPracticeDone(
        UUID taskId,
        PythonStreamEvent event,
        TaskStreamEventPayload payload
    ) {
        if (!event.resolvedEventType().isTerminal() || !"done".equalsIgnoreCase(payload.event())) {
            return;
        }
        SmartEngineTask task = taskStateMachineService.getTask(taskId);
        if (task.getServiceType() != ServiceType.PRACTICE_JUDGE || task.getTaskStatus() != TaskStatus.COMPLETED) {
            return;
        }
        persistPracticeResult(task);
        if (learningPathProgressService.handleStageTestResult(task.getUserId(), task.getId())) {
            return;
        }
        personalizedLearningRefreshService.triggerPracticeRefresh(task.getUserId(), "practice_judge_completed");
    }

    private void persistPracticeResult(SmartEngineTask task) {
        try {
            int persisted = practiceResultPersistenceService.persistCompletedPracticeJudgeResult(task);
            if (persisted > 0) {
                auditService.log("TASK", "LOW", "Persisted practice judge result", task.getUserId(), task.getId(), Map.of("itemCount", persisted));
            }
        } catch (RuntimeException ex) {
            LOGGER.warn("Failed to persist practice judge result taskId={}", task.getId(), ex);
        }
    }

    public void markWorkerFailed(UUID taskId, String errorCode, String message) {
        TaskStreamEventPayload failurePayload = taskStateMachineService.failTaskIfActive(
            taskId,
            errorCode == null || errorCode.isBlank() ? "PYTHON_WORKER_ERROR" : errorCode,
            message == null || message.isBlank() ? "Python worker failed" : message
        );
        if (failurePayload != null) {
            sseEmitterService.publish(failurePayload, true);
        }
    }
}
