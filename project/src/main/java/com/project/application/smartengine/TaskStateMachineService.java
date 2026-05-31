package com.project.application.smartengine;

import com.project.api.smartengine.dto.TaskStatusResponse;
import com.project.application.artifact.ArtifactDownloadDescriptor;
import com.project.application.artifact.ArtifactDownloadService;
import com.project.application.common.ApplicationException;
import com.project.domain.artifact.ResourceType;
import com.project.domain.task.SmartEngineTask;
import com.project.domain.task.SmartEngineTaskEvent;
import com.project.domain.task.SmartEngineTaskEventRepository;
import com.project.domain.task.SmartEngineTaskRepository;
import com.project.domain.task.ServiceType;
import com.project.domain.task.TaskStatus;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.nio.file.Path;
import java.time.OffsetDateTime;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * 管理任务状态流转与事件持久化。
 */
@Service
public class TaskStateMachineService {

    private final SmartEngineTaskRepository taskRepository;
    private final SmartEngineTaskEventRepository taskEventRepository;
    private final ArtifactDownloadService artifactDownloadService;
    private final VideoGenerationTaskService videoGenerationTaskService;

    public TaskStateMachineService(
        SmartEngineTaskRepository taskRepository,
        SmartEngineTaskEventRepository taskEventRepository,
        ArtifactDownloadService artifactDownloadService,
        VideoGenerationTaskService videoGenerationTaskService
    ) {
        this.taskRepository = taskRepository;
        this.taskEventRepository = taskEventRepository;
        this.artifactDownloadService = artifactDownloadService;
        this.videoGenerationTaskService = videoGenerationTaskService;
    }

    @Transactional
    public SmartEngineTask createTask(UUID taskId, UUID userId, String traceId, ServiceType serviceType, Map<String, Object> requestPayload) {
        SmartEngineTask task = new SmartEngineTask();
        task.setId(taskId);
        task.setUserId(userId);
        task.setTraceId(traceId);
        task.setServiceType(serviceType);
        task.setTaskStatus(TaskStatus.PENDING);
        task.setRequestPayload(requestPayload);
        task.setResponseSummary(new LinkedHashMap<>());
        return taskRepository.save(task);
    }

    @Transactional
    public SmartEngineTask markRunning(UUID taskId) {
        SmartEngineTask task = getTaskInternal(taskId);
        if (task.isTerminal()) {
            return task;
        }
        task.setTaskStatus(TaskStatus.RUNNING);
        task.setStartedAt(task.getStartedAt() == null ? OffsetDateTime.now() : task.getStartedAt());
        if (task.getCurrentStage() == null) {
            task.setCurrentStage("dispatching");
        }
        return task;
    }

    @Transactional
    public TaskStreamEventPayload recordPythonEvent(UUID taskId, PythonStreamEvent pythonEvent) {
        SmartEngineTask task = getTaskInternalForUpdate(taskId);
        int nextSequence = taskEventRepository.countByTaskId(taskId) + 1;
        return applyAndPersistPythonEvent(task, pythonEvent, nextSequence);
    }

    @Transactional
    public TaskEventRecordResult recordPythonEvent(UUID taskId, PythonStreamEvent pythonEvent, int eventSeq) {
        if (eventSeq <= 0) {
            throw new ApplicationException("INVALID_EVENT_SEQ", "event seq must be positive", HttpStatus.BAD_REQUEST);
        }

        SmartEngineTask task = getTaskInternalForUpdate(taskId);
        return taskEventRepository.findByTaskIdAndEventSeq(taskId, eventSeq)
            .map(existingEvent -> {
                if (!task.isTerminal() && pythonEvent.resolvedEventType().isTerminal()) {
                    int nextSequence = taskEventRepository.countByTaskId(taskId) + 1;
                    return new TaskEventRecordResult(applyAndPersistPythonEvent(task, pythonEvent, nextSequence), true);
                }
                return new TaskEventRecordResult(toPayload(task, existingEvent), false);
            })
            .orElseGet(() -> {
                if (task.isTerminal()) {
                    return TaskEventRecordResult.ignored();
                }
                return new TaskEventRecordResult(applyAndPersistPythonEvent(task, pythonEvent, eventSeq), true);
            });
    }

    private TaskStreamEventPayload applyAndPersistPythonEvent(
        SmartEngineTask task,
        PythonStreamEvent pythonEvent,
        int sequence
    ) {
        Map<String, Object> payload = pythonEvent.safePayload();

        if (task.getStartedAt() == null) {
            task.setStartedAt(OffsetDateTime.now());
        }

        StreamEventType eventType = pythonEvent.resolvedEventType();
        String persistedEventType = pythonEvent.eventType();
        boolean skipVideoSync = false;
        if (isInvalidGeneratedArtifactEvent(eventType, payload)) {
            payload = provenanceFailurePayload(eventType, payload);
            applyErrorEvent(task, payload);
            persistedEventType = StreamEventType.ERROR.wireValue();
            skipVideoSync = true;
        } else {
            switch (eventType) {
                case PROGRESS -> applyProgressEvent(task, pythonEvent, payload);
                case RESOURCE_FILE -> payload = applyResourceFileEvent(task, payload);
                case QUESTION_BATCH, JUDGE_RESULT, VIDEO_GEN_SPEECH, VIDEO_GEN_COMPLETE -> applyStructuredResultEvent(task, pythonEvent, payload);
                case DONE -> applyDoneEvent(task, payload);
                case ERROR -> applyErrorEvent(task, payload);
                default -> applyIntermediateEvent(task, pythonEvent);
            }
        }
        if (!skipVideoSync && eventType != StreamEventType.RESOURCE_FILE) {
            videoGenerationTaskService.syncFromPythonEvent(task, pythonEvent, payload);
        }

        SmartEngineTaskEvent event = new SmartEngineTaskEvent();
        event.setTask(task);
        event.setEventSeq(sequence);
        event.setEventType(persistedEventType);
        event.setStageName(pythonEvent.stage());
        event.setEventPayload(payload);
        SmartEngineTaskEvent savedEvent = taskEventRepository.save(event);

        return new TaskStreamEventPayload(
            persistedEventType,
            task.getId(),
            task.getTraceId(),
            sequence,
            savedEvent.getCreatedAt(),
            payload
        );
    }

    @Transactional
    public TaskStreamEventPayload failTask(UUID taskId, String errorCode, String message) {
        SmartEngineTask task = getTaskInternalForUpdate(taskId);
        return failTask(task, errorCode, message);
    }

    @Transactional
    public TaskStreamEventPayload failTaskIfActive(UUID taskId, String errorCode, String message) {
        SmartEngineTask task = getTaskInternalForUpdate(taskId);
        if (task.isTerminal()) {
            return null;
        }
        return failTask(task, errorCode, message);
    }

    private TaskStreamEventPayload failTask(SmartEngineTask task, String errorCode, String message) {
        task.setTaskStatus(TaskStatus.FAILED);
        task.setErrorCode(errorCode);
        task.setErrorMessage(message);
        task.setCompletedAt(OffsetDateTime.now());
        videoGenerationTaskService.markFailed(task, message);

        int nextSequence = taskEventRepository.countByTaskId(task.getId()) + 1;
        Map<String, Object> payload = Map.of(
            "code", errorCode,
            "message", message
        );

        SmartEngineTaskEvent event = new SmartEngineTaskEvent();
        event.setTask(task);
        event.setEventSeq(nextSequence);
        event.setEventType(StreamEventType.ERROR.wireValue());
        event.setStageName(task.getCurrentStage());
        event.setEventPayload(payload);
        SmartEngineTaskEvent savedEvent = taskEventRepository.save(event);

        return new TaskStreamEventPayload(StreamEventType.ERROR.wireValue(), task.getId(), task.getTraceId(), nextSequence, savedEvent.getCreatedAt(), payload);
    }

    @Transactional(readOnly = true)
    public SmartEngineTask getOwnedTask(UUID taskId, UUID userId) {
        return taskRepository.findByIdAndUserId(taskId, userId)
            .orElseThrow(() -> new ApplicationException("TASK_NOT_FOUND", "任务不存在", HttpStatus.NOT_FOUND));
    }

    @Transactional(readOnly = true)
    public TaskStatusResponse getOwnedTaskStatus(UUID taskId, UUID userId) {
        SmartEngineTask task = getOwnedTask(taskId, userId);
        return new TaskStatusResponse(
            task.getId(),
            task.getTraceId(),
            task.getServiceType(),
            task.getTaskStatus(),
            task.getCurrentStage(),
            task.getProgressPercent(),
            task.getErrorCode(),
            task.getErrorMessage(),
            task.getResponseSummary()
        );
    }

    @Transactional
    public TaskStreamEventPayload markCancelled(UUID taskId) {
        SmartEngineTask task = getTaskInternalForUpdate(taskId);
        task.setTaskStatus(TaskStatus.CANCELLED);
        task.setCompletedAt(OffsetDateTime.now());

        int nextSequence = taskEventRepository.countByTaskId(taskId) + 1;
        Map<String, Object> payload = Map.of(
            "code", "TASK_CANCELLED",
            "message", "任务已被取消"
        );

        SmartEngineTaskEvent event = new SmartEngineTaskEvent();
        event.setTask(task);
        event.setEventSeq(nextSequence);
        event.setEventType(StreamEventType.DONE.wireValue());
        event.setStageName(task.getCurrentStage());
        event.setEventPayload(payload);
        SmartEngineTaskEvent savedEvent = taskEventRepository.save(event);

        return new TaskStreamEventPayload(
            StreamEventType.DONE.wireValue(),
            task.getId(),
            task.getTraceId(),
            nextSequence,
            savedEvent.getCreatedAt(),
            payload
        );
    }

    @Transactional(readOnly = true)
    public boolean isTerminal(UUID taskId) {
        return getTaskInternal(taskId).isTerminal();
    }

    @Transactional(readOnly = true)
    public boolean isCancelled(UUID taskId) {
        return getTaskInternal(taskId).getTaskStatus() == TaskStatus.CANCELLED;
    }

    private SmartEngineTask getTaskInternal(UUID taskId) {
        return taskRepository.findById(taskId)
            .orElseThrow(() -> new ApplicationException("TASK_NOT_FOUND", "任务不存在", HttpStatus.NOT_FOUND));
    }

    private SmartEngineTask getTaskInternalForUpdate(UUID taskId) {
        return taskRepository.findWithLockById(taskId)
            .orElseThrow(() -> new ApplicationException("TASK_NOT_FOUND", "任务不存在", HttpStatus.NOT_FOUND));
    }

    private TaskStreamEventPayload toPayload(SmartEngineTask task, SmartEngineTaskEvent event) {
        return new TaskStreamEventPayload(
            event.getEventType(),
            task.getId(),
            task.getTraceId(),
            event.getEventSeq(),
            event.getCreatedAt(),
            event.getEventPayload()
        );
    }

    private void applyProgressEvent(SmartEngineTask task, PythonStreamEvent pythonEvent, Map<String, Object> payload) {
        task.setTaskStatus(TaskStatus.RUNNING);
        task.setCurrentStage(pythonEvent.stage());
        Object percentValue = payload.get("percent");
        if (percentValue instanceof Number number) {
            task.setProgressPercent(BigDecimal.valueOf(number.doubleValue()));
        }
    }

    private void applyIntermediateEvent(SmartEngineTask task, PythonStreamEvent pythonEvent) {
        task.setTaskStatus(TaskStatus.RUNNING);
        if (pythonEvent.stage() != null && !pythonEvent.stage().isBlank()) {
            task.setCurrentStage(pythonEvent.stage());
        }
    }

    private void applyStructuredResultEvent(SmartEngineTask task, PythonStreamEvent pythonEvent, Map<String, Object> payload) {
        applyIntermediateEvent(task, pythonEvent);
        task.setResponseSummary(new LinkedHashMap<>(payload));
    }

    private void applyDoneEvent(SmartEngineTask task, Map<String, Object> payload) {
        Object statusValue = payload.get("status");
        String normalizedStatus = statusValue == null ? "" : String.valueOf(statusValue);
        boolean failed = "FAILED".equalsIgnoreCase(normalizedStatus);
        boolean partialFailed = "PARTIAL_FAILED".equalsIgnoreCase(normalizedStatus);
        task.setTaskStatus(failed ? TaskStatus.FAILED : TaskStatus.COMPLETED);
        task.setCurrentStage(failed ? "failed" : partialFailed ? "partial_failed" : "completed");
        task.setProgressPercent(BigDecimal.valueOf(100));
        task.setCompletedAt(OffsetDateTime.now());
        if (failed) {
            task.setErrorCode("PYTHON_AGENT_DONE_FAILED");
            Object summaryValue = payload.getOrDefault("summary", "Python Agent execution failed");
            task.setErrorMessage(summaryValue == null ? "Python Agent execution failed" : String.valueOf(summaryValue));
        }
        Map<String, Object> mergedSummary = new LinkedHashMap<>(task.getResponseSummary());
        mergedSummary.putAll(payload);
        task.setResponseSummary(mergedSummary);
    }

    private void applyErrorEvent(SmartEngineTask task, Map<String, Object> payload) {
        task.setTaskStatus(TaskStatus.FAILED);
        task.setCompletedAt(OffsetDateTime.now());
        Object codeValue = payload.getOrDefault("code", "PYTHON_AGENT_ERROR");
        Object messageValue = payload.getOrDefault("message", "Python Agent 执行失败");
        task.setErrorCode(codeValue == null ? "PYTHON_AGENT_ERROR" : String.valueOf(codeValue));
        task.setErrorMessage(messageValue == null ? "Python Agent 执行失败" : String.valueOf(messageValue));
    }

    private boolean isInvalidGeneratedArtifactEvent(StreamEventType eventType, Map<String, Object> payload) {
        if (eventType == StreamEventType.RESOURCE_FILE) {
            return requiresGeneratedResourceProvenance(payload) && !hasRealLlmProvenance(payload);
        }
        if (eventType == StreamEventType.QUESTION_BATCH) {
            return !hasRealLlmProvenance(payload);
        }
        if (isVideoArtifactEvent(eventType) && containsVideoArtifactPayload(payload)) {
            return !hasRealLlmProvenance(payload);
        }
        return false;
    }

    private boolean requiresGeneratedResourceProvenance(Map<String, Object> payload) {
        String displayMode = stringValue(payload.get("displayMode"));
        if ("external_link".equalsIgnoreCase(displayMode)) {
            return false;
        }
        String sourceName = stringValue(payload.get("sourceName"));
        return sourceName == null || sourceName.isBlank() || "generated".equalsIgnoreCase(sourceName);
    }

    private boolean hasRealLlmProvenance(Map<String, Object> payload) {
        return "LLM".equalsIgnoreCase(stringValue(payload.get("generatedBy")))
            && "LLM".equalsIgnoreCase(stringValue(payload.get("contentOrigin")))
            && hasText(payload.get("provider"))
            && hasText(payload.get("model"))
            && hasText(payload.get("agentName"))
            && payload.get("evidenceIds") instanceof Collection<?>
            && Boolean.FALSE.equals(payload.get("fallback"))
            && payload.get("fromCache") instanceof Boolean;
    }

    private boolean isVideoArtifactEvent(StreamEventType eventType) {
        return eventType == StreamEventType.VIDEO_GEN_SCRIPT
            || eventType == StreamEventType.VIDEO_GEN_SPEECH
            || eventType == StreamEventType.VIDEO_GEN_AVATAR
            || eventType == StreamEventType.VIDEO_GEN_COMPLETE;
    }

    private boolean containsVideoArtifactPayload(Map<String, Object> payload) {
        return hasText(payload.get("scriptText"))
            || hasText(payload.get("audioBase64"))
            || hasText(payload.get("audioUrl"))
            || hasText(payload.get("videoUrl"))
            || hasText(payload.get("finalVideoUrl"))
            || hasText(payload.get("downloadUrl"))
            || payload.get("scriptJson") instanceof Map<?, ?>
            || payload.get("videoGenerationTask") instanceof Map<?, ?>
            || payload.get("videoSandboxArtifact") instanceof Map<?, ?>;
    }

    private Map<String, Object> provenanceFailurePayload(StreamEventType eventType, Map<String, Object> originalPayload) {
        Map<String, Object> failure = new LinkedHashMap<>();
        failure.put("code", "PROVENANCE_INVALID");
        failure.put("message", "Generated artifact is missing required LLM provenance metadata");
        failure.put("sourceEvent", eventType.wireValue());
        Object title = originalPayload.get("title");
        if (title != null) {
            failure.put("title", title);
        }
        Object assetType = originalPayload.get("assetType");
        if (assetType != null) {
            failure.put("assetType", assetType);
        }
        return failure;
    }

    private boolean hasText(Object value) {
        String text = stringValue(value);
        return text != null && !text.isBlank();
    }

    private String stringValue(Object value) {
        return value == null ? null : String.valueOf(value);
    }

    private Map<String, Object> applyResourceFileEvent(SmartEngineTask task, Map<String, Object> payload) {
        String sandboxPath = (String) payload.getOrDefault("sandboxPath", payload.get("localPath"));
        String fileName = (String) payload.get("fileName");
        if (sandboxPath == null || fileName == null) {
            task.setResponseSummary(new LinkedHashMap<>(payload));
            return payload;
        }
        videoGenerationTaskService.syncFromResourceFile(task, payload);

        ResourceType resourceType = resolveResourceType(payload.get("assetType"));

        ArtifactDownloadDescriptor descriptor = artifactDownloadService.issueDownload(
            task,
            resourceType,
            (String) payload.getOrDefault("title", fileName),
            fileName,
            sandboxPath,
            (String) payload.get("mimeType")
        );

        Map<String, Object> signedPayload = new LinkedHashMap<>(payload);
        signedPayload.remove("sandboxPath");
        signedPayload.remove("localPath");
        signedPayload.put("downloadUrl", descriptor.downloadUrl());
        signedPayload.put("expiresInSec", descriptor.expiresInSec());
        signedPayload.put("expiresAt", descriptor.expiresAt());
        String thumbnailPath = (String) payload.get("thumbnailPath");
        if (thumbnailPath != null && !thumbnailPath.isBlank()) {
            String thumbnailFileName = (String) payload.getOrDefault("thumbnailFileName", Path.of(thumbnailPath).getFileName().toString());
            ArtifactDownloadDescriptor thumbnailDescriptor = artifactDownloadService.issueDownload(
                task,
                resourceType,
                (String) payload.getOrDefault("title", thumbnailFileName),
                thumbnailFileName,
                thumbnailPath,
                (String) payload.get("thumbnailMimeType")
            );
            signedPayload.remove("thumbnailPath");
            signedPayload.put("thumbnailUrl", thumbnailDescriptor.downloadUrl());
        }
        task.setResponseSummary(new LinkedHashMap<>(signedPayload));
        return signedPayload;
    }

    private ResourceType resolveResourceType(Object rawValue) {
        if (rawValue instanceof ResourceType resourceType) {
            return resourceType;
        }
        if (rawValue instanceof String text && !text.isBlank()) {
            return ResourceType.fromValue(text);
        }
        return ResourceType.DOCUMENT;
    }
}
