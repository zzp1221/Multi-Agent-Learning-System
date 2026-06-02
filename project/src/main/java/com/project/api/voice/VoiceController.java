package com.project.api.voice;

import com.project.api.voice.dto.VoiceCommandRequest;
import com.project.api.voice.dto.VoiceCommandResponse;
import com.project.api.voice.dto.VoiceSessionResponse;
import com.project.api.voice.dto.VoiceTranscribeResponse;
import com.project.api.voice.dto.VoiceTtsRequest;
import com.project.application.common.ApplicationException;
import com.project.application.common.ClientDisconnectDetector;
import com.project.application.voice.VoiceAsrPrewarmService;
import com.project.application.voice.VoiceGatewayService;
import com.project.application.voice.VoiceMetricContext;
import com.project.application.voice.VoiceMetricLogger;
import com.project.application.voice.VoiceTurnMetricsService;
import com.project.application.voice.VoiceTtsClient;
import com.project.application.voice.VoiceTtsChunk;
import com.project.security.JwtAuthenticatedUser;
import com.project.config.AppProperties;
import com.project.security.AuthenticatedUserResolver;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.core.task.TaskExecutor;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicBoolean;

@RestController
@RequestMapping("/api/voice")
@Tag(name = "Voice Assistant")
public class VoiceController {

    private static final Logger LOGGER = LoggerFactory.getLogger(VoiceController.class);
    private static final long STREAM_TIMEOUT_MS = 0L;

    private final VoiceGatewayService voiceGatewayService;
    private final VoiceTtsClient voiceTtsClient;
    private final AppProperties appProperties;
    private final TaskExecutor voiceTaskExecutor;
    private final VoiceMetricLogger voiceMetricLogger;
    private final VoiceTurnMetricsService voiceTurnMetricsService;
    private final VoiceAsrPrewarmService voiceAsrPrewarmService;

    public VoiceController(
        VoiceGatewayService voiceGatewayService,
        VoiceTtsClient voiceTtsClient,
        AppProperties appProperties,
        @Qualifier("voiceTaskExecutor") TaskExecutor voiceTaskExecutor,
        VoiceMetricLogger voiceMetricLogger,
        VoiceTurnMetricsService voiceTurnMetricsService,
        VoiceAsrPrewarmService voiceAsrPrewarmService
    ) {
        this.voiceGatewayService = voiceGatewayService;
        this.voiceTtsClient = voiceTtsClient;
        this.appProperties = appProperties;
        this.voiceTaskExecutor = voiceTaskExecutor;
        this.voiceMetricLogger = voiceMetricLogger;
        this.voiceTurnMetricsService = voiceTurnMetricsService;
        this.voiceAsrPrewarmService = voiceAsrPrewarmService;
    }

    @PostMapping(path = "/transcribe", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    @Operation(summary = "Transcribe 16k mono PCM voice audio")
    public ResponseEntity<VoiceTranscribeResponse> transcribe(
        Authentication authentication,
        @RequestPart("file") MultipartFile file
    ) {
        return ResponseEntity.ok(
            voiceGatewayService.transcribe(AuthenticatedUserResolver.require(authentication), file)
        );
    }

    @PostMapping("/sessions")
    @Operation(summary = "Create a short lived realtime voice session")
    public ResponseEntity<VoiceSessionResponse> createSession(Authentication authentication) {
        return ResponseEntity.ok(
            voiceGatewayService.createSession(AuthenticatedUserResolver.require(authentication))
        );
    }

    @PostMapping("/commands/parse")
    @Operation(summary = "Parse voice shortcut command")
    public ResponseEntity<VoiceCommandResponse> parseCommand(
        Authentication authentication,
        @Valid @RequestBody VoiceCommandRequest request
    ) {
        return ResponseEntity.ok(
            voiceGatewayService.parseCommand(AuthenticatedUserResolver.require(authentication), request)
        );
    }

    @PostMapping(path = "/tts/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    @Operation(summary = "Stream synthesized speech audio chunks")
    public SseEmitter streamTts(
        Authentication authentication,
        @Valid @RequestBody VoiceTtsRequest request
    ) {
        JwtAuthenticatedUser currentUser = AuthenticatedUserResolver.require(authentication);
        String text = request.normalizedText();
        if (text.isBlank() && !Boolean.TRUE.equals(request.turnComplete())) {
            throw new ApplicationException("VOICE_TTS_TEXT_EMPTY", "朗读内容不能为空", HttpStatus.BAD_REQUEST);
        }
        VoiceMetricContext metricContext = resolveMetricContext(request, currentUser);
        if (text.isBlank()) {
            return completeTtsTurn(metricContext);
        }
        SseEmitter emitter = new SseEmitter(STREAM_TIMEOUT_MS);
        AtomicInteger sequence = new AtomicInteger(0);
        AtomicBoolean firstAudioSent = new AtomicBoolean(false);
        String streamId = UUID.randomUUID().toString();
        long startedAtNanos = System.nanoTime();
        emitter.onCompletion(() -> LOGGER.debug("Voice TTS SSE completed"));
        emitter.onTimeout(() -> LOGGER.debug("Voice TTS SSE timed out"));
        emitter.onError(ex -> LOGGER.debug("Voice TTS SSE error", ex));

        voiceTaskExecutor.execute(() -> {
            try {
                recordTtsMetric("tts_request_start_ms", metricContext, streamId, 0L, "success", text.length(), null, "");
                voiceTtsClient.synthesize(text, request.normalizedVoice(), chunk -> {
                    if (!chunk.finished() && firstAudioSent.compareAndSet(false, true)) {
                        recordTtsMetric("tts_first_audio_ms", metricContext, streamId, startedAtNanos, "success", text.length(), chunk.audioBase64().length(), "");
                    }
                    sendTtsChunk(emitter, sequence, chunk);
                });
                recordTtsMetric("tts_done_ms", metricContext, streamId, startedAtNanos, "success", text.length(), null, "");
                if (request.isTurnComplete()) {
                    recordVoiceTurnTotal(metricContext, "success", text.length(), null, "");
                }
                sendEvent(emitter, "done", sequence, Map.of("finished", true));
                emitter.complete();
            } catch (Exception ex) {
                if (ClientDisconnectDetector.isClientDisconnect(ex)) {
                    safeComplete(emitter);
                    return;
                }
                LOGGER.warn("Voice TTS stream failed", ex);
                String outcome = firstAudioSent.get() ? "error" : "fallback_text_only";
                recordTtsMetric("tts_error_ms", metricContext, streamId, startedAtNanos, outcome, text.length(), null, ex.getClass().getSimpleName());
                if (request.isTurnComplete()) {
                    recordVoiceTurnTotal(metricContext, outcome, text.length(), null, ex.getClass().getSimpleName());
                }
                sendEvent(emitter, "error", sequence, Map.of(
                    "message", "语音合成暂不可用，已保留文字回答",
                    "fallback", "TEXT_ONLY"
                ));
                safeComplete(emitter);
            }
        });
        return emitter;
    }

    private SseEmitter completeTtsTurn(VoiceMetricContext metricContext) {
        SseEmitter emitter = new SseEmitter(STREAM_TIMEOUT_MS);
        AtomicInteger sequence = new AtomicInteger(0);
        emitter.onCompletion(() -> LOGGER.debug("Voice TTS completion marker SSE completed"));
        emitter.onTimeout(() -> LOGGER.debug("Voice TTS completion marker SSE timed out"));
        emitter.onError(ex -> LOGGER.debug("Voice TTS completion marker SSE error", ex));
        voiceTaskExecutor.execute(() -> {
            try {
                recordVoiceTurnTotal(metricContext, "success", 0, null, "");
                sendEvent(emitter, "done", sequence, Map.of("finished", true, "audioSkipped", true));
                emitter.complete();
            } catch (Exception ex) {
                if (ClientDisconnectDetector.isClientDisconnect(ex)) {
                    safeComplete(emitter);
                    return;
                }
                LOGGER.warn("Voice TTS completion marker failed", ex);
                safeComplete(emitter);
            }
        });
        return emitter;
    }

    @PostMapping("/sessions/{sessionId}/prewarm")
    @Operation(summary = "Prewarm ASR provider connection for a voice session")
    public ResponseEntity<Void> prewarmAsr(
        Authentication authentication,
        @PathVariable UUID sessionId
    ) {
        JwtAuthenticatedUser currentUser = AuthenticatedUserResolver.require(authentication);
        voiceGatewayService.ensureSessionOwnedBy(sessionId, currentUser);
        voiceAsrPrewarmService.prewarm(sessionId, currentUser);
        return ResponseEntity.accepted().build();
    }

    @DeleteMapping("/sessions/{sessionId}/prewarm")
    @Operation(summary = "Release prewarmed ASR provider connection")
    public ResponseEntity<Void> releasePrewarmedAsr(
        Authentication authentication,
        @PathVariable UUID sessionId
    ) {
        JwtAuthenticatedUser currentUser = AuthenticatedUserResolver.require(authentication);
        voiceAsrPrewarmService.release(sessionId, currentUser.userId());
        return ResponseEntity.noContent().build();
    }

    private VoiceMetricContext resolveMetricContext(VoiceTtsRequest request, JwtAuthenticatedUser currentUser) {
        UUID voiceSessionId = parseUuid(request.voiceSessionId());
        String turnId = request.voiceTurnId() == null ? "" : request.voiceTurnId().trim();
        if (voiceSessionId == null || turnId.isBlank()) {
            return new VoiceMetricContext(null, streamIdFallback(), parseUuid(request.conversationId()), hashlessUser(currentUser), request.pageType(), request.commandIntent());
        }
        UUID conversationId = parseUuid(request.conversationId());
        voiceTurnMetricsService.attachConversation(voiceSessionId, turnId, conversationId, request.pageType(), request.commandIntent());
        return voiceTurnMetricsService.context(voiceSessionId, turnId);
    }

    private UUID parseUuid(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        try {
            return UUID.fromString(value.trim());
        } catch (IllegalArgumentException ex) {
            return null;
        }
    }

    private String hashlessUser(JwtAuthenticatedUser currentUser) {
        return currentUser == null || currentUser.userId() == null ? "" : Integer.toHexString(currentUser.userId().hashCode());
    }

    private String streamIdFallback() {
        return "";
    }

    private void recordTtsMetric(
        String metric,
        VoiceMetricContext metricContext,
        String streamId,
        long startedAtNanos,
        String outcome,
        Integer inputLength,
        Integer outputLength,
        String errorCode
    ) {
        long durationMs = "tts_request_start_ms".equals(metric)
            ? ttsRequestStartMs(metricContext)
            : TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAtNanos);
        voiceMetricLogger.record(
            metric,
            metricContext.voiceSessionId() == null ? new VoiceMetricContext(null, streamId, metricContext.conversationId(), metricContext.userHash(), metricContext.pageType(), metricContext.commandIntent()) : metricContext,
            durationMs,
            appProperties.getVoice().getProvider(),
            appProperties.getVoice().getTtsModel(),
            outcome,
            inputLength,
            outputLength,
            errorCode
        );
    }

    private long ttsRequestStartMs(VoiceMetricContext metricContext) {
        if (metricContext.voiceSessionId() == null || metricContext.turnId() == null || metricContext.turnId().isBlank()) {
            return 0L;
        }
        return voiceTurnMetricsService.elapsedMs(metricContext.voiceSessionId(), metricContext.turnId());
    }

    private void recordVoiceTurnTotal(
        VoiceMetricContext metricContext,
        String outcome,
        Integer inputLength,
        Integer outputLength,
        String errorCode
    ) {
        if (metricContext.voiceSessionId() == null || metricContext.turnId() == null || metricContext.turnId().isBlank()) {
            return;
        }
        voiceMetricLogger.record(
            "voice_turn_total_ms",
            metricContext,
            voiceTurnMetricsService.elapsedMs(metricContext.voiceSessionId(), metricContext.turnId()),
            appProperties.getVoice().getProvider(),
            appProperties.getVoice().getTtsModel(),
            outcome,
            inputLength,
            outputLength,
            errorCode
        );
        voiceTurnMetricsService.complete(metricContext.voiceSessionId(), metricContext.turnId());
    }

    private void sendTtsChunk(SseEmitter emitter, AtomicInteger sequence, VoiceTtsChunk chunk) {
        if (chunk.finished()) {
            sendEvent(emitter, "done", sequence, Map.of("finished", true));
            return;
        }
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("audio", chunk.audioBase64());
        payload.put("sampleRate", chunk.sampleRate() > 0 ? chunk.sampleRate() : appProperties.getVoice().getSampleRate());
        payload.put("format", chunk.format());
        sendEvent(emitter, "audio", sequence, payload);
    }

    private void sendEvent(SseEmitter emitter, String name, AtomicInteger sequence, Map<String, Object> payload) {
        int nextSeq = sequence.incrementAndGet();
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("event", name);
        body.put("seq", nextSeq);
        body.put("timestamp", OffsetDateTime.now());
        body.put("payload", payload);
        try {
            emitter.send(SseEmitter.event()
                .name(name)
                .id(String.valueOf(nextSeq))
                .data(body));
        } catch (IOException | IllegalStateException ex) {
            throw new VoiceClientDisconnectedException(ex);
        }
    }

    private void safeComplete(SseEmitter emitter) {
        try {
            emitter.complete();
        } catch (IllegalStateException ex) {
            LOGGER.debug("Voice SSE emitter already completed", ex);
        }
    }

    private static final class VoiceClientDisconnectedException extends RuntimeException {
        private VoiceClientDisconnectedException(Throwable cause) {
            super(cause);
        }
    }
}
