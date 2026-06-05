package com.project.application.conversation;

import com.project.api.conversation.dto.ConversationMessageStreamRequest;
import com.project.api.conversation.dto.ConversationHistoryItemResponse;
import com.project.api.conversation.dto.ConversationMessageItemResponse;
import com.project.api.conversation.dto.CreateConversationResponse;
import com.project.application.common.ApplicationException;
import com.project.application.common.ClientDisconnectDetector;
import com.project.application.artifact.ArtifactDownloadDescriptor;
import com.project.application.artifact.ArtifactDownloadService;
import com.project.application.smartengine.PythonAgentClient;
import com.project.application.smartengine.PythonStreamEvent;
import com.project.application.smartengine.SmartEngineInvocation;
import com.project.application.voice.VoiceMetricContext;
import com.project.application.voice.VoiceMetricLogger;
import com.project.application.voice.VoicePartialDraftService;
import com.project.application.voice.VoiceTurnMetricsService;
import com.project.domain.conversation.ConversationMode;
import com.project.domain.conversation.QnaSession;
import com.project.domain.conversation.QnaSessionRepository;
import com.project.domain.artifact.ResourceType;
import com.project.domain.task.ServiceType;
import com.project.domain.task.SmartEngineTask;
import com.project.domain.task.SmartEngineTaskRepository;
import com.project.domain.task.TaskStatus;
import com.project.domain.profile.UserProfileCurrentRepository;
import com.project.security.JwtAuthenticatedUser;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.data.domain.PageRequest;
import org.springframework.core.task.TaskExecutor;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.nio.file.Path;
import java.time.OffsetDateTime;
import java.math.BigDecimal;
import java.util.Collection;
import java.util.List;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 管理会话元数据与流式消息转发。
 */
@Service
public class ConversationService {

    private static final long DEFAULT_TIMEOUT_MS = 0L;
    private static final Logger LOGGER = LoggerFactory.getLogger(ConversationService.class);
    private static final int DEFAULT_CONVERSATION_PAGE = 0;
    private static final int DEFAULT_CONVERSATION_SIZE = 12;
    private static final int DEFAULT_MESSAGE_PAGE = 0;
    private static final int DEFAULT_MESSAGE_SIZE = 50;

    private final QnaSessionRepository qnaSessionRepository;
    private final PythonAgentClient pythonAgentClient;
    private final PythonConversationMessageClient pythonConversationMessageClient;
    private final TaskExecutor conversationTaskExecutor;
    private final UserProfileCurrentRepository userProfileCurrentRepository;
    private final VoiceMetricLogger voiceMetricLogger;
    private final VoiceTurnMetricsService voiceTurnMetricsService;
    private final VoicePartialDraftService voicePartialDraftService;
    private final SmartEngineTaskRepository smartEngineTaskRepository;
    private final ArtifactDownloadService artifactDownloadService;

    public ConversationService(
        QnaSessionRepository qnaSessionRepository,
        PythonAgentClient pythonAgentClient,
        PythonConversationMessageClient pythonConversationMessageClient,
        @Qualifier("conversationTaskExecutor") TaskExecutor conversationTaskExecutor,
        UserProfileCurrentRepository userProfileCurrentRepository,
        VoiceMetricLogger voiceMetricLogger,
        VoiceTurnMetricsService voiceTurnMetricsService,
        VoicePartialDraftService voicePartialDraftService,
        SmartEngineTaskRepository smartEngineTaskRepository,
        ArtifactDownloadService artifactDownloadService
    ) {
        this.qnaSessionRepository = qnaSessionRepository;
        this.pythonAgentClient = pythonAgentClient;
        this.pythonConversationMessageClient = pythonConversationMessageClient;
        this.conversationTaskExecutor = conversationTaskExecutor;
        this.userProfileCurrentRepository = userProfileCurrentRepository;
        this.voiceMetricLogger = voiceMetricLogger;
        this.voiceTurnMetricsService = voiceTurnMetricsService;
        this.voicePartialDraftService = voicePartialDraftService;
        this.smartEngineTaskRepository = smartEngineTaskRepository;
        this.artifactDownloadService = artifactDownloadService;
    }

    @Transactional
    public CreateConversationResponse createConversation(JwtAuthenticatedUser currentUser) {
        QnaSession session = new QnaSession();
        session.setUserId(currentUser.userId());
        session.setTitle("新对话");
        session.setMongoThreadId("mongo-thread-" + UUID.randomUUID());
        session.setEntrySource("NEW_CONVERSATION");
        session.setCurrentMode(ConversationMode.QNA);

        QnaSession saved = qnaSessionRepository.save(session);
        return new CreateConversationResponse(saved.getId(), saved.getTitle());
    }

    @Transactional(readOnly = true)
    public List<ConversationHistoryItemResponse> listRecentConversations(
        JwtAuthenticatedUser currentUser,
        Integer page,
        Integer size
    ) {
        return qnaSessionRepository.findRecentByUserId(
                currentUser.userId(),
                PageRequest.of(normalizePage(page), normalizeSize(size, DEFAULT_CONVERSATION_SIZE))
            ).stream()
            .map(session -> new ConversationHistoryItemResponse(
                session.getId(),
                resolveConversationTitle(session),
                session.getLastMessagePreview(),
                session.getMessageCount(),
                session.getLastMessageAt(),
                session.getUpdatedAt()
            ))
            .toList();
    }

    public List<ConversationMessageItemResponse> listConversationMessages(
        JwtAuthenticatedUser currentUser,
        UUID conversationId,
        Integer page,
        Integer size
    ) {
        qnaSessionRepository.findByIdAndUserId(conversationId, currentUser.userId())
            .orElseThrow(() -> new ApplicationException("CONVERSATION_NOT_FOUND", "会话不存在", HttpStatus.NOT_FOUND));
        return pythonConversationMessageClient.listMessages(
            conversationId,
            currentUser.userId(),
            normalizePage(page),
            normalizeSize(size, DEFAULT_MESSAGE_SIZE)
        );
    }

    private static final int MAX_HISTORY_MESSAGES = 20;

    public SseEmitter streamMessage(
        JwtAuthenticatedUser currentUser,
        UUID conversationId,
        ConversationMessageStreamRequest request
    ) {
        if (!request.hasUsableInput()) {
            throw new ApplicationException("INVALID_ARGUMENT", "请输入问题内容或上传至少一张图片", HttpStatus.BAD_REQUEST);
        }
        QnaSession session = qnaSessionRepository.findByIdAndUserId(conversationId, currentUser.userId())
            .orElseThrow(() -> new ApplicationException("CONVERSATION_NOT_FOUND", "会话不存在", HttpStatus.NOT_FOUND));
        String normalizedMessage = request.normalizedMessage();
        List<String> imageUrls = request.normalizedImageUrls();

        session.setLastMessageAt(OffsetDateTime.now());
        session.setLastMessagePreview(buildPreview(normalizedMessage, imageUrls));
        session.setMessageCount(session.getMessageCount() + 1);
        if (session.getMessageCount() <= 1 || session.getTitle() == null || session.getTitle().isBlank() || "新对话".equals(session.getTitle())) {
            session.setTitle(buildConversationTitle(normalizedMessage, imageUrls));
        }
        appendConversationMessage(conversationId, currentUser.userId(), "user", normalizedMessage, imageUrls, true);
        qnaSessionRepository.save(session);

        // 获取会话历史用于多轮记忆
        List<ConversationMessageItemResponse> history = fetchRecentHistory(conversationId, currentUser.userId());

        SseEmitter emitter = new SseEmitter(DEFAULT_TIMEOUT_MS);
        emitter.onCompletion(() -> LOGGER.debug("Conversation SSE emitter completed conversationId={}", conversationId));
        emitter.onTimeout(() -> LOGGER.debug("Conversation SSE emitter timed out conversationId={}", conversationId));
        emitter.onError(ex -> LOGGER.debug("Conversation SSE emitter error conversationId={}", conversationId, ex));
        AtomicInteger sequence = new AtomicInteger(0);
        StringBuilder assistantReply = new StringBuilder();
        VoiceTurnRef voiceTurn = resolveVoiceTurn(request.voiceContextMap());
        if (voiceTurn.isPresent()) {
            voiceTurnMetricsService.attachConversation(
                voiceTurn.voiceSessionId(),
                voiceTurn.turnId(),
                conversationId,
                request.voiceContextMap().get("pageType"),
                request.voiceContextMap().get("commandIntent")
            );
        }

        conversationTaskExecutor.execute(() -> {
            try {
                recordVoiceMetric(
                    "llm_request_start_ms",
                    voiceTurn,
                    voiceTurn.isPresent() ? voiceTurnMetricsService.elapsedMs(voiceTurn.voiceSessionId(), voiceTurn.turnId()) : -1L,
                    "success",
                    request.normalizedMessage().length(),
                    null,
                    ""
                );
                long llmStartedAtNanos = System.nanoTime();
                java.util.concurrent.atomic.AtomicBoolean firstTokenLogged = new java.util.concurrent.atomic.AtomicBoolean(false);
                streamConversationAnswer(
                    currentUser,
                    conversationId,
                    request,
                    history,
                    voiceTurn,
                    assistantReply,
                    emitter,
                    sequence,
                    llmStartedAtNanos,
                    firstTokenLogged
                );
                recordVoiceMetric(
                    "llm_done_ms",
                    voiceTurn,
                    elapsedMs(llmStartedAtNanos),
                    "success",
                    request.normalizedMessage().length(),
                    assistantReply.length(),
                    ""
                );
                if (!assistantReply.isEmpty()) {
                    appendConversationMessage(conversationId, currentUser.userId(), "assistant", assistantReply.toString(), List.of(), false);
                }
                emitter.complete();
            } catch (Exception ex) {
                if (ClientDisconnectDetector.isClientDisconnect(ex)) {
                    LOGGER.info("Conversation stream closed by client conversationId={}", conversationId);
                    safeComplete(emitter);
                    return;
                }
                recordVoiceMetric(
                    "llm_error_ms",
                    voiceTurn,
                    voiceTurn.isPresent() ? voiceTurnMetricsService.elapsedMs(voiceTurn.voiceSessionId(), voiceTurn.turnId()) : -1L,
                    "error",
                    request.normalizedMessage().length(),
                    assistantReply.length(),
                    ex.getClass().getSimpleName()
                );
                if (assistantReply.isEmpty()) {
                    appendConversationMessage(conversationId, currentUser.userId(), "assistant", "抱歉，处理过程中遇到了问题，请稍后重试。", List.of(), false);
                }
                LOGGER.warn("Conversation stream failed conversationId={}", conversationId, ex);
                if (sendErrorEvent(emitter, conversationId, sequence, "会话流式调用失败，请稍后重试")) {
                    safeComplete(emitter);
                }
            }
        });

        return emitter;
    }

    private VoiceTurnRef resolveVoiceTurn(Map<String, String> voiceContext) {
        if (voiceContext == null || voiceContext.isEmpty()) {
            return VoiceTurnRef.empty();
        }
        try {
            String voiceSessionId = voiceContext.get("voiceSessionId");
            String turnId = voiceContext.get("voiceTurnId");
            if (voiceSessionId == null || voiceSessionId.isBlank() || turnId == null || turnId.isBlank()) {
                return VoiceTurnRef.empty();
            }
            return new VoiceTurnRef(UUID.fromString(voiceSessionId.trim()), turnId.trim());
        } catch (IllegalArgumentException ex) {
            return VoiceTurnRef.empty();
        }
    }

    private void streamConversationAnswer(
        JwtAuthenticatedUser currentUser,
        UUID conversationId,
        ConversationMessageStreamRequest request,
        List<ConversationMessageItemResponse> history,
        VoiceTurnRef voiceTurn,
        StringBuilder assistantReply,
        SseEmitter emitter,
        AtomicInteger sequence,
        long llmStartedAtNanos,
        java.util.concurrent.atomic.AtomicBoolean firstTokenLogged
    ) {
        java.util.function.Consumer<PythonStreamEvent> eventConsumer = event -> {
            collectAssistantReply(assistantReply, event);
            String chunk = extractVisibleAssistantChunk(event);
            if (!chunk.isEmpty() && firstTokenLogged.compareAndSet(false, true)) {
                recordVoiceMetric(
                    "llm_first_token_ms",
                    voiceTurn,
                    elapsedMs(llmStartedAtNanos),
                    "success",
                    request.normalizedMessage().length(),
                    chunk.length(),
                    ""
                );
            }
            sendConversationEvent(emitter, conversationId, sequence, event);
        };
        VoicePartialDraftService.ReusableDraft reusableDraft = takeReusableDraft(voiceTurn, request.normalizedMessage());
        if (reusableDraft != null) {
            voicePartialDraftService.streamDraft(reusableDraft, eventConsumer);
            return;
        }
        UUID taskId = UUID.randomUUID();
        String traceId = UUID.randomUUID().toString();
        ServiceType serviceType = request.resolvedServiceType();
        Map<String, Object> params = buildConversationParams(currentUser, conversationId, request, history);
        java.util.concurrent.atomic.AtomicReference<SmartEngineTask> conversationTaskRef = new java.util.concurrent.atomic.AtomicReference<>();
        java.util.function.Consumer<PythonStreamEvent> signedEventConsumer = event -> {
            PythonStreamEvent outboundEvent = event;
            if ("resource_file".equals(event.eventType())) {
                SmartEngineTask conversationTask = conversationTaskRef.updateAndGet(existing -> existing == null
                    ? createConversationStreamTask(currentUser.userId(), taskId, traceId, serviceType, params)
                    : existing);
                outboundEvent = signConversationResourceFileEvent(conversationTask, event);
            }
            eventConsumer.accept(outboundEvent);
        };
        pythonAgentClient.stream(
            new SmartEngineInvocation(
                currentUser.userId(),
                taskId,
                traceId,
                conversationId,
                serviceType,
                params
            ),
            signedEventConsumer
        );
    }

    private VoicePartialDraftService.ReusableDraft takeReusableDraft(VoiceTurnRef voiceTurn, String finalText) {
        if (!voiceTurn.isPresent() || voicePartialDraftService == null) {
            return null;
        }
        return voicePartialDraftService.takeReusableDraft(voiceTurn.voiceSessionId(), voiceTurn.turnId(), finalText);
    }

    private void recordVoiceMetric(
        String metric,
        VoiceTurnRef voiceTurn,
        long durationMs,
        String outcome,
        Integer inputLength,
        Integer outputLength,
        String errorCode
    ) {
        if (!voiceTurn.isPresent() || voiceMetricLogger == null || voiceTurnMetricsService == null) {
            return;
        }
        VoiceMetricContext context = voiceTurnMetricsService.context(voiceTurn.voiceSessionId(), voiceTurn.turnId());
        voiceMetricLogger.record(metric, context, durationMs, "python-agent", "conversation-stream", outcome, inputLength, outputLength, errorCode);
    }

    private SmartEngineTask createConversationStreamTask(
        UUID userId,
        UUID taskId,
        String traceId,
        ServiceType serviceType,
        Map<String, Object> requestPayload
    ) {
        SmartEngineTask task = new SmartEngineTask();
        task.setId(taskId);
        task.setUserId(userId);
        task.setTraceId(traceId);
        task.setServiceType(serviceType);
        task.setTaskStatus(TaskStatus.RUNNING);
        task.setCurrentStage("conversation_stream");
        task.setProgressPercent(BigDecimal.ZERO);
        task.setRequestPayload(new LinkedHashMap<>(requestPayload));
        task.setResponseSummary(new LinkedHashMap<>());
        task.setStartedAt(OffsetDateTime.now());
        return smartEngineTaskRepository == null ? task : smartEngineTaskRepository.save(task);
    }

    private PythonStreamEvent signConversationResourceFileEvent(SmartEngineTask task, PythonStreamEvent event) {
        if (!"resource_file".equals(event.eventType())) {
            return event;
        }
        Map<String, Object> payload = event.safePayload();
        if (isPendingSlideOutline(payload)) {
            return new PythonStreamEvent(event.eventType(), event.stage(), stripSandboxPaths(payload));
        }
        if (requiresGeneratedResourceProvenance(payload) && !hasRealLlmProvenance(payload)) {
            return new PythonStreamEvent(
                "error",
                event.stage(),
                provenanceFailurePayload(payload)
            );
        }
        if (artifactDownloadService == null) {
            return new PythonStreamEvent(event.eventType(), event.stage(), stripSandboxPaths(payload));
        }
        String sandboxPath = firstText(payload.get("sandboxPath"), payload.get("localPath"));
        String fileName = stringValue(payload.get("fileName"));
        if (sandboxPath == null || sandboxPath.isBlank() || fileName == null || fileName.isBlank()) {
            return new PythonStreamEvent(event.eventType(), event.stage(), stripSandboxPaths(payload));
        }
        try {
            Map<String, Object> signedPayload = signResourceFilePayload(task, payload, sandboxPath, fileName);
            return new PythonStreamEvent(event.eventType(), event.stage(), signedPayload);
        } catch (Exception ex) {
            LOGGER.warn("Conversation resource_file signing failed taskId={} title={}", task.getId(), payload.get("title"), ex);
            return new PythonStreamEvent(
                "error",
                event.stage(),
                Map.of(
                    "code", "ARTIFACT_SIGNING_FAILED",
                    "message", "生成资源下载链接签发失败",
                    "sourceEvent", "resource_file"
                )
            );
        }
    }

    private Map<String, Object> signResourceFilePayload(
        SmartEngineTask task,
        Map<String, Object> payload,
        String sandboxPath,
        String fileName
    ) {
        ResourceType resourceType = resolveResourceType(payload.get("assetType"));
        ArtifactDownloadDescriptor descriptor = artifactDownloadService.issueDownload(
            task,
            resourceType,
            firstText(payload.get("title"), fileName),
            fileName,
            sandboxPath,
            stringValue(payload.get("mimeType"))
        );
        Map<String, Object> signedPayload = stripSandboxPaths(payload);
        signedPayload.put("downloadUrl", descriptor.downloadUrl());
        signedPayload.put("expiresInSec", descriptor.expiresInSec());
        signedPayload.put("expiresAt", descriptor.expiresAt());
        String thumbnailPath = stringValue(payload.get("thumbnailPath"));
        if (thumbnailPath != null && !thumbnailPath.isBlank()) {
            String thumbnailFileName = firstText(
                payload.get("thumbnailFileName"),
                Path.of(thumbnailPath).getFileName().toString()
            );
            ArtifactDownloadDescriptor thumbnailDescriptor = artifactDownloadService.issueDownload(
                task,
                resourceType,
                firstText(payload.get("title"), thumbnailFileName),
                thumbnailFileName,
                thumbnailPath,
                stringValue(payload.get("thumbnailMimeType"))
            );
            signedPayload.remove("thumbnailPath");
            signedPayload.put("thumbnailUrl", thumbnailDescriptor.downloadUrl());
        }
        return signedPayload;
    }

    private Map<String, Object> stripSandboxPaths(Map<String, Object> payload) {
        Map<String, Object> sanitized = new LinkedHashMap<>(payload);
        sanitized.remove("sandboxPath");
        sanitized.remove("localPath");
        return sanitized;
    }

    private boolean isPendingSlideOutline(Map<String, Object> payload) {
        return "SLIDES".equalsIgnoreCase(stringValue(payload.get("assetType")))
            && "SLIDE_OUTLINE_CONFIRMATION".equalsIgnoreCase(stringValue(payload.get("displayMode")));
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

    private Map<String, Object> provenanceFailurePayload(Map<String, Object> originalPayload) {
        Map<String, Object> failure = new LinkedHashMap<>();
        failure.put("code", "PROVENANCE_INVALID");
        failure.put("message", "Generated artifact is missing required LLM provenance metadata");
        failure.put("sourceEvent", "resource_file");
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

    private ResourceType resolveResourceType(Object rawValue) {
        if (rawValue instanceof ResourceType resourceType) {
            return resourceType;
        }
        if (rawValue instanceof String text && !text.isBlank()) {
            return ResourceType.fromValue(text);
        }
        return ResourceType.DOCUMENT;
    }

    private boolean hasText(Object value) {
        String text = stringValue(value);
        return text != null && !text.isBlank();
    }

    private String firstText(Object first, Object second) {
        String firstValue = stringValue(first);
        if (firstValue != null && !firstValue.isBlank()) {
            return firstValue;
        }
        return stringValue(second);
    }

    private String stringValue(Object value) {
        return value == null ? null : String.valueOf(value);
    }

    private long elapsedMs(long startedAtNanos) {
        return java.util.concurrent.TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAtNanos);
    }

    private void sendConversationEvent(
        SseEmitter emitter,
        UUID conversationId,
        AtomicInteger sequence,
        PythonStreamEvent event
    ) {
        int nextSeq = sequence.incrementAndGet();
        Map<String, Object> payload = new LinkedHashMap<>(event.safePayload());
        String eventStage = event.stage();
        if (eventStage != null && !eventStage.isBlank() && !payload.containsKey("stage")) {
            payload.put("stage", eventStage);
        }
        try {
            emitter.send(SseEmitter.event()
                .name(event.eventType())
                .id(String.valueOf(nextSeq))
                .data(new ConversationStreamEventPayload(
                    event.eventType(),
                    nextSeq,
                    OffsetDateTime.now(),
                    new ConversationDialogState(
                        conversationId,
                        "turn_" + nextSeq,
                        String.valueOf(payload.getOrDefault("pedagogyStrategy", "EXPLAIN")),
                        String.valueOf(payload.getOrDefault("nextAction", "WAIT_USER"))
                    ),
                    payload
                )));
        } catch (IOException ex) {
            throw new ConversationClientDisconnectedException(ex);
        } catch (IllegalStateException ex) {
            throw new ConversationClientDisconnectedException(ex);
        }
    }

    private boolean sendErrorEvent(
        SseEmitter emitter,
        UUID conversationId,
        AtomicInteger sequence,
        String message
    ) {
        int nextSeq = sequence.incrementAndGet();
        try {
            emitter.send(SseEmitter.event()
                .name("error")
                .id(String.valueOf(nextSeq))
                .data(new ConversationStreamEventPayload(
                    "error",
                    nextSeq,
                    OffsetDateTime.now(),
                    new ConversationDialogState(conversationId, "turn_" + nextSeq, "CORRECT", "END_SESSION"),
                    Map.of("message", message == null ? "会话流式调用失败" : message)
                )));
            return true;
        } catch (IOException | IllegalStateException ex) {
            LOGGER.debug("Skip sending conversation error because client is unavailable conversationId={}", conversationId, ex);
            return false;
        }
    }

    private void safeComplete(SseEmitter emitter) {
        try {
            emitter.complete();
        } catch (IllegalStateException ex) {
            LOGGER.debug("Conversation SSE emitter already completed", ex);
        }
    }

    private static final class ConversationClientDisconnectedException extends RuntimeException {
        private ConversationClientDisconnectedException(Throwable cause) {
            super(cause);
        }
    }

    private void collectAssistantReply(StringBuilder assistantReply, PythonStreamEvent event) {
        String chunk = extractVisibleAssistantChunk(event);
        if (chunk == null || chunk.isEmpty()) {
            return;
        }
        assistantReply.append(chunk);
    }

    private String extractVisibleAssistantChunk(PythonStreamEvent event) {
        if (!"result_chunk".equals(event.eventType())) {
            return "";
        }
        String stage = event.stage();
        if (stage != null && !stage.isBlank() && !"tutoring".equals(stage)) {
            return "";
        }
        Object text = event.safePayload().get("text");
        if (!(text instanceof String value)) {
            return "";
        }
        return sanitizeVisibleAssistantChunk(value);
    }

    private boolean looksLikeInternalChain(String text) {
        String normalized = text == null ? "" : text.replace('\n', ' ').trim();
        if (normalized.isBlank()) {
            return false;
        }
        return normalized.contains("历史摘要")
            || normalized.contains("检索查询")
            || normalized.contains("来源摘要")
            || normalized.contains("查询处理")
            || normalized.contains("协作计划")
            || normalized.contains("质量复核")
            || normalized.contains("优先参考的来源")
            || normalized.contains("建议你这样学")
            || normalized.contains("接下来请你先回答")
            || normalized.contains("未解决的问题");
    }

    private String sanitizeVisibleAssistantChunk(String text) {
        String normalized = text == null ? "" : text.replace("\r\n", "\n");
        if (normalized.isEmpty() || looksLikeInternalChain(normalized)) {
            return "";
        }
        return normalized;
    }

    private void appendConversationMessage(
        UUID conversationId,
        UUID userId,
        String role,
        String content,
        List<String> imageUrls,
        boolean failOnError
    ) {
        if ((content == null || content.isBlank()) && (imageUrls == null || imageUrls.isEmpty())) {
            return;
        }
        try {
            pythonConversationMessageClient.appendMessage(
                conversationId,
                userId,
                role,
                content == null ? "" : content.trim(),
                imageUrls == null ? List.of() : imageUrls
            );
        } catch (Exception ex) {
            if (failOnError) {
                throw new ApplicationException("CONVERSATION_MESSAGE_PERSIST_FAILED", "会话消息保存失败，请稍后重试", HttpStatus.BAD_GATEWAY);
            }
            LOGGER.warn(
                "Failed to persist conversation message conversationId={} userId={} role={}",
                conversationId,
                userId,
                role,
                ex
            );
        }
    }

    private List<ConversationMessageItemResponse> fetchRecentHistory(UUID conversationId, UUID userId) {
        try {
            List<ConversationMessageItemResponse> all = pythonConversationMessageClient.listMessages(conversationId, userId);
            int fromIndex = Math.max(0, all.size() - MAX_HISTORY_MESSAGES);
            return all.subList(fromIndex, all.size());
        } catch (Exception ex) {
            LOGGER.warn("Failed to load conversation history conversationId={}: {}", conversationId, ex.getMessage());
            return List.of();
        }
    }

    private Map<String, Object> buildConversationParams(
        JwtAuthenticatedUser currentUser,
        UUID conversationId,
        ConversationMessageStreamRequest request,
        List<ConversationMessageItemResponse> history
    ) {
        Map<String, Object> params = new LinkedHashMap<>();
        params.put("message", request.normalizedMessage());
        params.put("query", request.normalizedMessage());
        params.put("userInput", request.normalizedMessage());
        params.put("imageUrls", request.normalizedImageUrls());
        params.put("webSearchEnabled", request.isWebSearchEnabled());
        params.put("reasoningMode", request.resolvedReasoningMode().value());
        params.put("deepReasoning", "DEEP".equals(request.resolvedReasoningMode().value()));
        params.put("conversationId", conversationId.toString());
        params.put("userId", currentUser.userId().toString());
        params.put("conversationLength", history.size());
        Map<String, String> voiceContext = request.voiceContextMap();
        if (!voiceContext.isEmpty()) {
            params.put("voiceContext", voiceContext);
            params.put("learningContext", buildLearningContext(voiceContext));
            putLearningContext(params, "confirmedSlideOutline", voiceContext.get("confirmedSlideOutline"));
            putLearningContext(params, "confirmedSlideOutlineText", voiceContext.get("confirmedSlideOutlineText"));
        }

        List<Map<String, Object>> messages = new java.util.ArrayList<>();
        for (ConversationMessageItemResponse msg : history) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("role", msg.role());
            item.put("content", msg.content());
            item.put("imageUrls", msg.imageUrls() == null ? List.of() : msg.imageUrls());
            messages.add(item);
        }
        params.put("messages", messages);
        userProfileCurrentRepository.findById(currentUser.userId())
            .ifPresent(profile -> {
                params.put("profile", new LinkedHashMap<>(profile.getProfileJson()));
                params.put("profileSummary", profile.getSummaryText());
            });
        return params;
    }

    private Map<String, Object> buildLearningContext(Map<String, String> voiceContext) {
        Map<String, Object> learningContext = new LinkedHashMap<>();
        putLearningContext(learningContext, "pageType", voiceContext.get("pageType"));
        putLearningContext(learningContext, "questionId", voiceContext.get("questionId"));
        putLearningContext(learningContext, "course", voiceContext.get("courseId"));
        putLearningContext(learningContext, "knowledgePoint", voiceContext.get("knowledgePointId"));
        putLearningContext(learningContext, "pageTitle", voiceContext.get("pageTitle"));
        putLearningContext(learningContext, "currentPath", voiceContext.get("currentPath"));
        putLearningContext(learningContext, "source", voiceContext.get("source"));
        putLearningContext(learningContext, "conversationId", voiceContext.get("conversationId"));
        putLearningContext(learningContext, "recentMessagesSummary", voiceContext.get("recentMessagesSummary"));
        putLearningContext(learningContext, "commandIntent", voiceContext.get("commandIntent"));
        putLearningContext(learningContext, "voiceSessionId", voiceContext.get("voiceSessionId"));
        putLearningContext(learningContext, "voiceTurnId", voiceContext.get("voiceTurnId"));
        putLearningContext(learningContext, "selectedService", voiceContext.get("selectedService"));
        putLearningContext(learningContext, "formParametersSummary", voiceContext.get("formParametersSummary"));
        putLearningContext(learningContext, "taskStatus", voiceContext.get("taskStatus"));
        putLearningContext(learningContext, "currentMistakeSummary", voiceContext.get("currentMistakeSummary"));
        putLearningContext(learningContext, "reviewStatus", voiceContext.get("reviewStatus"));
        putLearningContext(learningContext, "weakPointsSummary", voiceContext.get("weakPointsSummary"));
        putLearningContext(learningContext, "currentGoal", voiceContext.get("currentGoal"));
        putLearningContext(learningContext, "lowestMasteryKnowledge", voiceContext.get("lowestMasteryKnowledge"));
        putLearningContext(learningContext, "resourceResultSummary", voiceContext.get("resourceResultSummary"));
        putLearningContext(learningContext, "downloadResourceSummary", voiceContext.get("downloadResourceSummary"));
        putLearningContext(learningContext, "recommendedAction", voiceContext.get("recommendedAction"));
        putLearningContext(learningContext, "activeLearningStepId", voiceContext.get("activeLearningStepId"));
        putLearningContext(learningContext, "activeLearningStepTitle", voiceContext.get("activeLearningStepTitle"));
        putLearningContext(learningContext, "activeLearningStepProgress", voiceContext.get("activeLearningStepProgress"));
        putLearningContext(learningContext, "activeLearningStepSummary", voiceContext.get("activeLearningStepSummary"));
        putLearningContext(learningContext, "explicitUserTopic", voiceContext.get("explicitUserTopic"));
        putLearningContext(learningContext, "questionCount", voiceContext.get("questionCount"));
        putLearningContext(learningContext, "questionTypePreference", voiceContext.get("questionTypePreference"));
        putLearningContext(learningContext, "difficultyPreference", voiceContext.get("difficultyPreference"));
        putLearningContext(learningContext, "requiresSlideOutlineConfirmation", voiceContext.get("requiresSlideOutlineConfirmation"));
        putLearningContext(learningContext, "confirmedSlideOutline", voiceContext.get("confirmedSlideOutline"));
        putLearningContext(learningContext, "confirmedSlideOutlineText", voiceContext.get("confirmedSlideOutlineText"));
        return learningContext;
    }

    private void putLearningContext(Map<String, Object> target, String key, String value) {
        if (value != null && !value.isBlank()) {
            target.put(key, value.trim());
        }
    }

    private String truncate(String message, int maxLength) {
        return message.length() <= maxLength ? message : message.substring(0, maxLength);
    }

    private int normalizePage(Integer page) {
        return page == null || page < 0 ? DEFAULT_CONVERSATION_PAGE : page;
    }

    private int normalizeSize(Integer size, int defaultSize) {
        return size == null || size <= 0 ? defaultSize : size;
    }

    private String buildConversationTitle(String message, List<String> imageUrls) {
        String normalized = message == null ? "" : message.replaceAll("\\s+", " ").trim();
        if (normalized.isEmpty() && imageUrls != null && !imageUrls.isEmpty()) {
            return "图片提问";
        }
        if (normalized.isEmpty()) {
            return "新对话";
        }
        return truncate(normalized, 20);
    }

    private String resolveConversationTitle(QnaSession session) {
        if (session.getTitle() != null && !session.getTitle().isBlank() && !"新对话".equals(session.getTitle())) {
            return session.getTitle();
        }
        if (session.getLastMessagePreview() != null && !session.getLastMessagePreview().isBlank()) {
            return buildConversationTitle(session.getLastMessagePreview(), List.of());
        }
        return "新对话";
    }

    private String buildPreview(String message, List<String> imageUrls) {
        String normalized = message == null ? "" : message.trim();
        if (!normalized.isBlank()) {
            return truncate(normalized, 120);
        }
        int imageCount = imageUrls == null ? 0 : imageUrls.size();
        return imageCount <= 1 ? "[图片]" : "[图片] 共 " + imageCount + " 张";
    }

    private record VoiceTurnRef(UUID voiceSessionId, String turnId) {
        private static VoiceTurnRef empty() {
            return new VoiceTurnRef(null, "");
        }

        private boolean isPresent() {
            return voiceSessionId != null && turnId != null && !turnId.isBlank();
        }
    }
}
