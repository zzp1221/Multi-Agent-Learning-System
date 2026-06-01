package com.project.api.voice;

import com.project.api.voice.dto.VoiceCommandRequest;
import com.project.api.voice.dto.VoiceCommandResponse;
import com.project.api.voice.dto.VoiceSessionResponse;
import com.project.api.voice.dto.VoiceTranscribeResponse;
import com.project.api.voice.dto.VoiceTtsRequest;
import com.project.application.common.ApplicationException;
import com.project.application.common.ClientDisconnectDetector;
import com.project.application.voice.VoiceGatewayService;
import com.project.application.voice.VoiceTtsClient;
import com.project.application.voice.VoiceTtsChunk;
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
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

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

    public VoiceController(
        VoiceGatewayService voiceGatewayService,
        VoiceTtsClient voiceTtsClient,
        AppProperties appProperties,
        @Qualifier("voiceTaskExecutor") TaskExecutor voiceTaskExecutor
    ) {
        this.voiceGatewayService = voiceGatewayService;
        this.voiceTtsClient = voiceTtsClient;
        this.appProperties = appProperties;
        this.voiceTaskExecutor = voiceTaskExecutor;
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
        AuthenticatedUserResolver.require(authentication);
        String text = request.normalizedText();
        if (text.isBlank()) {
            throw new ApplicationException("VOICE_TTS_TEXT_EMPTY", "朗读内容不能为空", HttpStatus.BAD_REQUEST);
        }
        SseEmitter emitter = new SseEmitter(STREAM_TIMEOUT_MS);
        AtomicInteger sequence = new AtomicInteger(0);
        emitter.onCompletion(() -> LOGGER.debug("Voice TTS SSE completed"));
        emitter.onTimeout(() -> LOGGER.debug("Voice TTS SSE timed out"));
        emitter.onError(ex -> LOGGER.debug("Voice TTS SSE error", ex));

        voiceTaskExecutor.execute(() -> {
            try {
                voiceTtsClient.synthesize(text, request.normalizedVoice(), chunk -> sendTtsChunk(emitter, sequence, chunk));
                sendEvent(emitter, "done", sequence, Map.of("finished", true));
                emitter.complete();
            } catch (Exception ex) {
                if (ClientDisconnectDetector.isClientDisconnect(ex)) {
                    safeComplete(emitter);
                    return;
                }
                LOGGER.warn("Voice TTS stream failed", ex);
                sendEvent(emitter, "error", sequence, Map.of("message", "语音合成失败，请稍后重试"));
                safeComplete(emitter);
            }
        });
        return emitter;
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
