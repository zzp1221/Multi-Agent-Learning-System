package com.project.application.learningpath;

import com.project.application.audit.AuditService;
import com.project.application.common.ApplicationException;
import com.project.application.settings.UserLlmSettingsService;
import com.project.application.smartengine.PersonalizedLearningContextService;
import com.project.application.smartengine.SmartEngineInvocation;
import com.project.application.smartengine.SmartEngineQueueService;
import com.project.application.smartengine.TaskStateMachineService;
import com.project.domain.task.ServiceType;
import com.project.domain.task.SmartEngineTask;
import com.project.domain.task.SmartEngineTaskRepository;
import com.project.domain.task.TaskStatus;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.http.HttpStatus;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

/**
 * 后台创建个性化学习路径刷新任务，复用 SmartEngine 队列和 Python 多智能体链路。
 */
@Service
public class PersonalizedLearningRefreshService {

    private static final Logger LOGGER = LoggerFactory.getLogger(PersonalizedLearningRefreshService.class);
    private static final Duration ACTIVE_TASK_REUSE_WINDOW = Duration.ofMinutes(30);
    private static final String USER_LLM_REQUIRED_MESSAGE = "请先配置并保存模型和 API Key 后再使用智能功能。";

    private final TaskStateMachineService taskStateMachineService;
    private final SmartEngineTaskRepository taskRepository;
    private final ObjectProvider<SmartEngineQueueService> queueServiceProvider;
    private final PersonalizedLearningContextService contextService;
    private final AuditService auditService;
    private final UserLlmSettingsService userLlmSettingsService;

    public PersonalizedLearningRefreshService(
        TaskStateMachineService taskStateMachineService,
        SmartEngineTaskRepository taskRepository,
        ObjectProvider<SmartEngineQueueService> queueServiceProvider,
        PersonalizedLearningContextService contextService,
        AuditService auditService,
        UserLlmSettingsService userLlmSettingsService
    ) {
        this.taskStateMachineService = taskStateMachineService;
        this.taskRepository = taskRepository;
        this.queueServiceProvider = queueServiceProvider;
        this.contextService = contextService;
        this.auditService = auditService;
        this.userLlmSettingsService = userLlmSettingsService;
    }

    @Async("conversationTaskExecutor")
    public void triggerInitialPlan(UUID userId, String majorCode) {
        try {
            triggerRefresh(userId, "INITIAL_PROFILE", Map.of(
                "goal", "生成我的个性化学习路径规划和资源推送方案",
                "topic", majorCode == null || majorCode.isBlank() ? "个性化学习方案" : majorCode.trim(),
                "profileUpdate", true
            ));
        } catch (Exception ex) {
            LOGGER.warn("Failed to trigger initial personalized learning plan userId={}: {}", userId, ex.getMessage());
        }
    }

    @Async("conversationTaskExecutor")
    public void triggerPracticeRefresh(UUID userId, String reason) {
        try {
            triggerRefresh(userId, "PRACTICE_PROGRESS", Map.of(
                "goal", "根据最新练习结果调整我的学习路径和资源推送",
                "topic", "学习进度变化",
                "progressUpdateReason", reason == null ? "practice_progress_changed" : reason
            ));
        } catch (Exception ex) {
            LOGGER.warn("Failed to trigger practice personalized learning refresh userId={}: {}", userId, ex.getMessage());
        }
    }

    public SmartEngineTask triggerManualAdjustment(UUID userId, String adjustmentIntent) {
        Map<String, Object> params = new LinkedHashMap<>();
        params.put("goal", "根据用户调整意图重新规划个性化学习路径");
        params.put("topic", "个性化学习路径调整");
        params.put("manualRefresh", true);
        params.put("adjustmentIntent", adjustmentIntent == null ? "" : adjustmentIntent.trim());
        return triggerRefresh(userId, "MANUAL_ADJUSTMENT", params, true, ServiceType.PERSONALIZED_LEARNING);
    }

    public SmartEngineTask triggerResourceRecommendationRefresh(
        UUID userId,
        String adjustmentIntent,
        Map<String, Object> learningPath
    ) {
        return triggerResourceRecommendationRefresh(userId, adjustmentIntent, learningPath, List.of());
    }

    public SmartEngineTask triggerResourceRecommendationRefresh(
        UUID userId,
        String adjustmentIntent,
        Map<String, Object> learningPath,
        List<Map<String, Object>> existingResources
    ) {
        Map<String, Object> params = new LinkedHashMap<>();
        params.put("goal", "只刷新当前学习路径各阶段的推荐资源");
        params.put("topic", "个性化学习路径资源推荐");
        params.put("manualRefresh", true);
        params.put("resourceRefresh", true);
        params.put("adjustmentIntent", adjustmentIntent == null ? "" : adjustmentIntent.trim());
        if (learningPath != null && !learningPath.isEmpty()) {
            params.put("learningPath", new LinkedHashMap<>(learningPath));
        }
        appendExistingResourceExclusions(params, existingResources);
        return triggerRefresh(userId, "RESOURCE_RECOMMENDATION_REFRESH", params, true, ServiceType.RESOURCE_PUSH);
    }

    private void appendExistingResourceExclusions(
        Map<String, Object> params,
        List<Map<String, Object>> existingResources
    ) {
        if (existingResources == null || existingResources.isEmpty()) {
            return;
        }
        List<Map<String, Object>> normalizedResources = existingResources.stream()
            .filter(resource -> resource != null && !resource.isEmpty())
            .<Map<String, Object>>map(LinkedHashMap::new)
            .toList();
        List<String> urls = normalizedResources.stream()
            .map(resource -> readString(resource.get("downloadUrl")))
            .filter(value -> !value.isBlank())
            .distinct()
            .toList();
        List<String> titles = normalizedResources.stream()
            .map(resource -> readString(resource.get("title")))
            .filter(value -> !value.isBlank())
            .distinct()
            .toList();
        if (!normalizedResources.isEmpty()) {
            params.put("existingResources", normalizedResources);
        }
        if (!urls.isEmpty()) {
            params.put("previousResourceUrls", urls);
        }
        if (!titles.isEmpty()) {
            params.put("previousResourceTitles", titles);
        }
    }

    private static String readString(Object value) {
        return value instanceof String text ? text.trim() : "";
    }

    public SmartEngineTask triggerRefresh(UUID userId, String triggerSource, Map<String, Object> seedParams) {
        return triggerRefresh(userId, triggerSource, seedParams, true, ServiceType.PERSONALIZED_LEARNING);
    }

    private SmartEngineTask triggerRefresh(
        UUID userId,
        String triggerSource,
        Map<String, Object> seedParams,
        boolean reuseActiveTask,
        ServiceType serviceType
    ) {
        requireUserLlmReady(userId);
        if (reuseActiveTask) {
            Optional<SmartEngineTask> activeTask = taskRepository.findFirstByUserIdAndServiceTypeAndTaskStatusInOrderByCreatedAtDesc(
                userId,
                serviceType,
                java.util.List.of(TaskStatus.PENDING, TaskStatus.RUNNING)
            );
            if (activeTask.filter(this::isReusableActiveTask).isPresent()) {
                return activeTask.get();
            }
        }

        UUID taskId = UUID.randomUUID();
        String traceId = UUID.randomUUID().toString();
        Map<String, Object> params = new LinkedHashMap<>(seedParams == null ? Map.of() : seedParams);
        params.put("autoTriggered", true);
        params.put("triggerSource", triggerSource);
        params.putAll(contextService.buildContext(userId));

        Map<String, Object> requestPayload = new LinkedHashMap<>();
        requestPayload.put("conversationId", null);
        requestPayload.put("params", params);

        SmartEngineTask task = taskStateMachineService.createTask(
            taskId,
            userId,
            traceId,
            serviceType,
            requestPayload
        );

        SmartEngineQueueService queueService = queueServiceProvider.getIfAvailable();
        if (queueService == null) {
            taskStateMachineService.failTask(taskId, "QUEUE_UNAVAILABLE", "SmartEngine task queue is unavailable");
            throw new IllegalStateException("SmartEngine task queue is unavailable");
        }

        String recordId = queueService.enqueue(new SmartEngineInvocation(
            userId,
            taskId,
            traceId,
            null,
            serviceType,
            params
        ));
        auditService.log("TASK", "LOW", "Auto enqueued personalized learning refresh", userId, taskId, Map.of(
            "triggerSource", triggerSource,
            "serviceType", serviceType,
            "streamRecordId", recordId
        ));
        return task;
    }

    private void requireUserLlmReady(UUID userId) {
        if (userLlmSettingsService == null || userLlmSettingsService.isUserLlmReadyOrAllowedFallback(userId)) {
            return;
        }
        throw new ApplicationException("USER_LLM_REQUIRED", USER_LLM_REQUIRED_MESSAGE, HttpStatus.PRECONDITION_REQUIRED);
    }

    private boolean isReusableActiveTask(SmartEngineTask task) {
        OffsetDateTime createdAt = task.getCreatedAt();
        return createdAt == null || createdAt.isAfter(OffsetDateTime.now().minus(ACTIVE_TASK_REUSE_WINDOW));
    }
}
