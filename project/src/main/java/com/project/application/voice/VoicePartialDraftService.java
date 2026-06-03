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

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;
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
        DraftState state = new DraftState(UUID.randomUUID(), request.voiceSessionId(), request.turnId(), request.partialText());
        if (drafts.putIfAbsent(draftKey, state) == null) {
            conversationTaskExecutor.execute(() -> runDraft(request, state));
        }
    }

    public boolean keepOrCancel(UUID voiceSessionId, String turnId, String finalText) {
        return cancelIfFinalDiffers(voiceSessionId, turnId, finalText);
    }

    public boolean cancelIfFinalDiffers(UUID voiceSessionId, String turnId, String finalText) {
        DraftState draft = drafts.get(key(voiceSessionId, turnId));
        if (draft == null) {
            return false;
        }
        if (isSimilar(draft.partialText(), finalText)) {
            return true;
        }
        if (drafts.remove(key(voiceSessionId, turnId), draft)) {
            cancelDraft(draft, "FINAL_DIFF", draft.partialText().length(), finalText == null ? 0 : finalText.length());
        }
        return false;
    }

    public ReusableDraft takeReusableDraft(UUID voiceSessionId, String turnId, String finalText) {
        String draftKey = key(voiceSessionId, turnId);
        DraftState draft = drafts.get(draftKey);
        if (draft == null) {
            return null;
        }
        if (!isSimilar(draft.partialText(), finalText)) {
            cancelIfFinalDiffers(voiceSessionId, turnId, finalText);
            return null;
        }
        if (!draft.hasVisibleChunks()) {
            if (drafts.remove(draftKey, draft)) {
                cancelDraft(draft, "NO_VISIBLE_DRAFT", draft.partialText().length(), finalText == null ? 0 : finalText.length());
            }
            return null;
        }
        if (!drafts.remove(draftKey, draft) || !draft.adopt()) {
            return null;
        }
        record("llm_draft_keep_ms", voiceSessionId, turnId, "success", draft.partialText().length(), finalText == null ? 0 : finalText.length(), "");
        return new ReusableDraft(draft);
    }

    public void streamDraft(ReusableDraft draft, java.util.function.Consumer<PythonStreamEvent> eventConsumer) {
        if (draft != null && eventConsumer != null) {
            draft.stream(eventConsumer);
        }
    }

    public void cancel(UUID voiceSessionId, String turnId, String errorCode) {
        DraftState draft = drafts.remove(key(voiceSessionId, turnId));
        if (draft != null) {
            cancelDraft(draft, errorCode, draft.partialText().length(), null);
        }
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
                    state.appendEvent(event);
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
            if (!state.cancelled()) {
                record("llm_draft_done_ms", request.voiceSessionId(), request.turnId(), "success", request.partialText().length(), null, "");
            }
        } catch (Exception ex) {
            if (!state.cancelled()) {
                LOGGER.debug("Voice partial draft failed sessionId={} turnId={}: {}", request.voiceSessionId(), request.turnId(), ex.getMessage());
                record("llm_draft_error_ms", request.voiceSessionId(), request.turnId(), "error", request.partialText().length(), null, ex.getClass().getSimpleName());
            }
        } finally {
            state.finish();
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

    private void cancelDraft(DraftState draft, String errorCode, Integer inputLength, Integer outputLength) {
        draft.cancel();
        pythonAgentClient.cancel(draft.taskId().toString());
        record("llm_draft_cancel_ms", draft.voiceSessionId(), draft.turnId(), "cancelled", inputLength, outputLength, errorCode);
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
        return TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAtNanos);
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

    public final class ReusableDraft {
        private final DraftState state;
        private int cursor;

        private ReusableDraft(DraftState state) {
            this.state = state;
        }

        public boolean hasChunks() {
            return state.hasVisibleChunks();
        }

        public void cancel(String errorCode) {
            cancelDraft(state, errorCode, state.partialText().length(), null);
        }

        private void stream(java.util.function.Consumer<PythonStreamEvent> eventConsumer) {
            while (true) {
                List<PythonStreamEvent> events = state.eventsAfter(cursor);
                cursor += events.size();
                events.forEach(eventConsumer);
                if (state.done()) {
                    return;
                }
                state.awaitNextEvent();
            }
        }
    }

    private final class DraftState {
        private final UUID taskId;
        private final UUID voiceSessionId;
        private final String turnId;
        private final String partialText;
        private final List<PythonStreamEvent> events = new ArrayList<>();
        private boolean cancelled;
        private boolean adopted;
        private boolean done;

        private DraftState(UUID taskId, UUID voiceSessionId, String turnId, String partialText) {
            this.taskId = taskId;
            this.voiceSessionId = voiceSessionId;
            this.turnId = turnId;
            this.partialText = partialText;
        }

        private UUID taskId() {
            return taskId;
        }

        private UUID voiceSessionId() {
            return voiceSessionId;
        }

        private String turnId() {
            return turnId;
        }

        private String partialText() {
            return partialText;
        }

        private synchronized boolean cancelled() {
            return cancelled;
        }

        private synchronized boolean adopt() {
            if (adopted || cancelled) {
                return false;
            }
            adopted = true;
            notifyAll();
            return true;
        }

        private synchronized void appendEvent(PythonStreamEvent event) {
            events.add(event);
            notifyAll();
        }

        private synchronized List<PythonStreamEvent> eventsAfter(int cursor) {
            if (cursor >= events.size()) {
                return List.of();
            }
            return List.copyOf(events.subList(cursor, events.size()));
        }

        private synchronized boolean hasVisibleChunks() {
            return events.stream().anyMatch(event -> !visibleChunk(event).isBlank());
        }

        private synchronized void awaitNextEvent() {
            try {
                wait(250L);
            } catch (InterruptedException ex) {
                Thread.currentThread().interrupt();
                done = true;
            }
        }

        private synchronized boolean done() {
            return done;
        }

        private synchronized void finish() {
            done = true;
            notifyAll();
        }

        private synchronized void cancel() {
            cancelled = true;
            done = true;
            notifyAll();
        }
    }
}
