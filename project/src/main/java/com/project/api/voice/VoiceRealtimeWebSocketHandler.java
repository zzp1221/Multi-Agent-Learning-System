package com.project.api.voice;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.application.voice.VoiceAsrClient;
import com.project.application.voice.VoiceAsrResult;
import com.project.application.voice.VoiceSessionService;
import com.project.config.AppProperties;
import com.project.security.JwtAuthenticatedUser;
import com.project.security.JwtProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.TextWebSocketHandler;

import java.io.ByteArrayOutputStream;
import java.net.URI;
import java.time.OffsetDateTime;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

@Component
public class VoiceRealtimeWebSocketHandler extends TextWebSocketHandler {

    private static final Logger LOGGER = LoggerFactory.getLogger(VoiceRealtimeWebSocketHandler.class);

    private final JwtProvider jwtProvider;
    private final VoiceSessionService sessionService;
    private final VoiceAsrClient asrClient;
    private final AppProperties appProperties;
    private final ObjectMapper objectMapper;
    private final Map<String, VoiceSocketBuffer> buffers = new ConcurrentHashMap<>();

    public VoiceRealtimeWebSocketHandler(
        JwtProvider jwtProvider,
        VoiceSessionService sessionService,
        VoiceAsrClient asrClient,
        AppProperties appProperties,
        ObjectMapper objectMapper
    ) {
        this.jwtProvider = jwtProvider;
        this.sessionService = sessionService;
        this.asrClient = asrClient;
        this.appProperties = appProperties;
        this.objectMapper = objectMapper;
    }

    @Override
    public void afterConnectionEstablished(WebSocketSession session) throws Exception {
        JwtAuthenticatedUser user = authenticate(session);
        UUID voiceSessionId = readSessionId(session.getUri());
        if (!sessionService.isOwnedBy(voiceSessionId, user.userId())) {
            session.close(CloseStatus.NOT_ACCEPTABLE.withReason("invalid voice session"));
            return;
        }
        buffers.put(session.getId(), new VoiceSocketBuffer(user.userId(), voiceSessionId));
        send(session, "ready", Map.of(
            "sessionId", voiceSessionId.toString(),
            "sampleRate", appProperties.getVoice().getSampleRate()
        ));
    }

    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) throws Exception {
        VoiceSocketBuffer buffer = buffers.get(session.getId());
        if (buffer == null) {
            session.close(CloseStatus.NOT_ACCEPTABLE);
            return;
        }
        JsonNode event = objectMapper.readTree(message.getPayload());
        String type = event.path("type").asText("");
        if ("audio_chunk".equals(type)) {
            appendAudio(buffer, event.path("data").asText(""));
            send(session, "asr_partial", Map.of("text", "", "seq", event.path("seq").asInt(0)));
            return;
        }
        if ("commit".equals(type)) {
            VoiceAsrResult result = asrClient.transcribePcm16(buffer.toByteArray(), appProperties.getVoice().getSampleRate());
            send(session, "asr_final", Map.of(
                "text", result.text(),
                "durationMs", result.durationMs(),
                "provider", result.provider(),
                "model", result.model()
            ));
            buffer.reset();
            return;
        }
        if ("cancel".equals(type)) {
            buffer.reset();
            send(session, "cancelled", Map.of("sessionId", buffer.sessionId().toString()));
        }
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        buffers.remove(session.getId());
    }

    private void appendAudio(VoiceSocketBuffer buffer, String base64) {
        if (base64 == null || base64.isBlank()) {
            return;
        }
        byte[] chunk = Base64.getDecoder().decode(base64);
        if (buffer.size() + chunk.length > appProperties.getVoice().getMaxAudioBytes()) {
            throw new IllegalArgumentException("voice audio too large");
        }
        buffer.write(chunk);
    }

    private JwtAuthenticatedUser authenticate(WebSocketSession session) {
        String token = readToken(session.getUri());
        return jwtProvider.parse(token);
    }

    private String readToken(URI uri) {
        Map<String, String> params = parseQuery(uri);
        String token = params.getOrDefault("token", "");
        if (token.isBlank()) {
            throw new IllegalArgumentException("missing token");
        }
        return token;
    }

    private UUID readSessionId(URI uri) {
        Map<String, String> params = parseQuery(uri);
        return UUID.fromString(params.getOrDefault("sessionId", ""));
    }

    private Map<String, String> parseQuery(URI uri) {
        Map<String, String> params = new LinkedHashMap<>();
        String query = uri == null ? "" : uri.getRawQuery();
        if (query == null || query.isBlank()) {
            return params;
        }
        for (String item : query.split("&")) {
            int index = item.indexOf('=');
            if (index <= 0) {
                continue;
            }
            params.put(item.substring(0, index), java.net.URLDecoder.decode(item.substring(index + 1), java.nio.charset.StandardCharsets.UTF_8));
        }
        return params;
    }

    private void send(WebSocketSession session, String type, Map<String, Object> payload) throws Exception {
        if (!session.isOpen()) {
            return;
        }
        Map<String, Object> event = new LinkedHashMap<>();
        event.put("type", type);
        event.put("timestamp", OffsetDateTime.now());
        event.putAll(payload);
        session.sendMessage(new TextMessage(objectMapper.writeValueAsString(event)));
    }

    private static final class VoiceSocketBuffer {
        private final UUID userId;
        private final UUID sessionId;
        private final ByteArrayOutputStream audio = new ByteArrayOutputStream();

        private VoiceSocketBuffer(UUID userId, UUID sessionId) {
            this.userId = userId;
            this.sessionId = sessionId;
        }

        private UUID sessionId() {
            return sessionId;
        }

        private int size() {
            return audio.size();
        }

        private void write(byte[] chunk) {
            audio.writeBytes(chunk);
        }

        private byte[] toByteArray() {
            return audio.toByteArray();
        }

        private void reset() {
            audio.reset();
        }
    }
}
