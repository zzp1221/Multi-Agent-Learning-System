package com.project.application.voice;

import com.project.application.smartengine.PythonAgentClient;
import com.project.application.smartengine.PythonStreamEvent;
import com.project.application.smartengine.SmartEngineInvocation;
import com.project.domain.task.ServiceType;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.core.task.TaskExecutor;
import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;

@Service
public class VoicePartialDraftService {

    private static final Logger LOGGER = LoggerFactory.getLogger(VoicePartialDraftService.class);

    private final PythonAgentClient pythonAgentClient;
    private final TaskExecutor conversationTaskExecutor;
    private final VoiceMetricLogger voiceMetricLogger;
    private final VoiceTurnMetricsService voiceTurnMetricsService;
    private final Map<String, DraftState> drafts = new ConcurrentHashMap<>();

    public VoicePartialDraftService(
        PythonAgentClient pythonAgentClient,
        @Qualifier("conversationTaskExecutor") TaskExecutor conversationTaskExecutor,
        VoiceMetricLogger voiceMetricLogger,
        VoiceTurnMetricsService voiceTurnMetricsService
    ) {
        this.pythonAgentClient = pythonAgentClient;
        this.conversationTaskExecutor = conversationTaskExecutor;
        this.voiceMetricLogger = voiceMetricLogger;
        this.voiceTurnMetricsService = voiceTurnMetricsService;
    }

    public void startDraft(VoiceDraftRequest request) {
        if (request == null || !request.isUsable()) {
            return;
        }
        String draftKey = key(request.voiceSessionId(), request.turnId());
        DraftState state = new DraftState(UUID.randomUUID(), request.partialText());
        if (drafts.putIfAbsent(draftKey, state) == null) {
            conversationTaskExecutor.execute(() -> runDraft(request, state));
        }
    }

    public boolean keepOrCancel(UUID voiceSessionId, String turnId, String finalText) {
        DraftState draft = drafts.remove(key(voiceSessionId, turnId));
        if (draft == null) {
            return false;
        }
        boolean similar = isSimilar(draft.partialText(), finalText);
        if (!similar) {
            draft.cancel();
            pythonAgentClient.cancel(draft.taskId().toString());
            record("llm_draft_cancel_ms", voiceSessionId, turnId, "cancelled", draft.partialText().length(), finalText == null ? 0 : finalText.length(), "FINAL_DIFF");
            return false;
        }
        record("llm_draft_keep_ms", voiceSessionId, turnId, "success", draft.partialText().length(), finalText == null ? 0 : finalText.length(), "");
        return true;
    }

    public void cancel(UUID voiceSessionId, String turnId, String errorCode) {
        DraftState draft = drafts.remove(key(voiceSessionId, turnId));
        if (draft == null) {
            return;
        }
        draft.cancel();
        pythonAgentClient.cancel(draft.taskId().toString());
        record("llm_draft_cancel_ms", voiceSessionId, turnId, "cancelled", draft.partialText().length(), null, errorCode);
    }

    private void runDraft(VoiceDraftRequest request, DraftState state) {
        long startedAtNanos = System.nanoTime();
        AtomicBoolean firstTokenLogged = new AtomicBoolean(false);
        record("llm_draft_request_start_ms", request.voiceSessionId(), request.turnId(), "success", request.partialText().length(), null, "");
        try {
            pythonAgentClient.stream(
                new SmartEngineInvocation(
                    request.userId(),
                    state.taskId(),
                    "voice-draft-" + state.taskId(),
                    request.conversationId(),
                    ServiceType.TUTORING,
                    buildParams(request)
                ),
                event -> {
                    if (state.cancelled()) {
                        throw new IllegalStateException("voice draft cancelled");
                    }
                    String chunk = visibleChunk(event);
                    if (!chunk.isBlank() && firstTokenLogged.compareAndSet(false, true)) {
                        voiceMetricLogger.record(
                            "llm_draft_first_token_ms",
                            voiceTurnMetricsService.context(request.voiceSessionId(), request.turnId()),
                            elapsedMs(startedAtNanos),
                            "python-agent",
                            "conversation-stream",
                            "success",
                            request.partialText().length(),
                            chunk.length(),
                            ""
                        );
                    }
                }
            );
            record("llm_draft_done_ms", request.voiceSessionId(), request.turnId(), "success", request.partialText().length(), null, "");
        } catch (Exception ex) {
            if (!state.cancelled()) {
                LOGGER.debug("Voice partial draft failed sessionId={} turnId={}: {}", request.voiceSessionId(), request.turnId(), ex.getMessage());
                record("llm_draft_error_ms", request.voiceSessionId(), request.turnId(), "error", request.partialText().length(), null, ex.getClass().getSimpleName());
            }
        } finally {
            drafts.remove(key(request.voiceSessionId(), request.turnId()), state);
        }
    }

    private Map<String, Object> buildParams(VoiceDraftRequest request) {
        Map<String, Object> params = new LinkedHashMap<>();
        params.put("message", request.partialText());
        params.put("query", request.partialText());
        params.put("userInput", request.partialText());
        params.put("voiceDraft", true);
        params.put("webSearchEnabled", false);
        params.put("reasoningMode", "NORMAL");
        params.put("conversationId", request.conversationId().toString());
        params.put("userId", request.userId().toString());
        Map<String, Object> learningContext = new LinkedHashMap<>();
        learningContext.put("source", "voice_partial_draft");
        learningContext.put("pageType", request.pageType());
        learningContext.put("commandIntent", request.commandIntent());
        learningContext.put("voiceSessionId", request.voiceSessionId().toString());
        learningContext.put("voiceTurnId", request.turnId());
        params.put("learningContext", learningContext);
        return params;
    }

    private String visibleChunk(PythonStreamEvent event) {
        if (!"result_chunk".equals(event.eventType())) {
            return "";
        }
        Object text = event.safePayload().get("text");
        return text instanceof String value ? value.trim() : "";
    }

    private void record(String metric, UUID voiceSessionId, String turnId, String outcome, Integer inputLength, Integer outputLength, String errorCode) {
        voiceMetricLogger.record(
            metric,
            voiceTurnMetricsService.context(voiceSessionId, turnId),
            voiceTurnMetricsService.elapsedMs(voiceSessionId, turnId),
            "python-agent",
            "conversation-stream",
            outcome,
            inputLength,
            outputLength,
            errorCode
        );
    }

    private long elapsedMs(long startedAtNanos) {
        return java.util.concurrent.TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAtNanos);
    }

    private boolean isSimilar(String partialText, String finalText) {
        String partial = normalize(partialText);
        String full = normalize(finalText);
        if (partial.isBlank() || full.isBlank()) {
            return false;
        }
        if (full.contains(partial) || partial.contains(full)) {
            return true;
        }
        int commonPrefix = 0;
        int max = Math.min(partial.length(), full.length());
        while (commonPrefix < max && partial.charAt(commonPrefix) == full.charAt(commonPrefix)) {
            commonPrefix += 1;
        }
        return commonPrefix >= Math.min(partial.length(), full.length()) * 0.8D;
    }

    private String normalize(String text) {
        return text == null ? "" : text.replaceAll("\\s+", "").trim();
    }

    private String key(UUID voiceSessionId, String turnId) {
        return voiceSessionId + ":" + turnId;
    }

    public record VoiceDraftRequest(
        UUID userId,
        UUID voiceSessionId,
        String turnId,
        UUID conversationId,
        String pageType,
        String commandIntent,
        String partialText
    ) {
        private boolean isUsable() {
            return userId != null
                && voiceSessionId != null
                && turnId != null
                && !turnId.isBlank()
                && conversationId != null
                && partialText != null
                && partialText.trim().length() >= 8;
        }
    }

    private static final class DraftState {
        private final UUID taskId;
        private final String partialText;
        private volatile boolean cancelled;

        private DraftState(UUID taskId, String partialText) {
            this.taskId = taskId;
            this.partialText = partialText;
        }

        private UUID taskId() {
            return taskId;
        }

        private String partialText() {
            return partialText;
        }

        private boolean cancelled() {
            return cancelled;
        }

        private void cancel() {
            cancelled = true;
        }
    }
}
