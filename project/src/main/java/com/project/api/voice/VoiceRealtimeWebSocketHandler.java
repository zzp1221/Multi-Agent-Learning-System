package com.project.api.voice;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.application.voice.VoiceRealtimeAsrClient;
import com.project.application.voice.VoiceRealtimeAsrListener;
import com.project.application.voice.VoiceRealtimeAsrSession;
import com.project.application.voice.VoiceMetricLogger;
import com.project.application.voice.VoiceSessionService;
import com.project.config.AppProperties;
import com.project.security.JwtAuthenticatedUser;
import com.project.security.JwtProvider;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.core.task.TaskExecutor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.TextWebSocketHandler;

import java.net.URI;
import java.time.OffsetDateTime;
import java.util.ArrayDeque;
import java.util.Base64;
import java.util.Deque;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

@Component
public class VoiceRealtimeWebSocketHandler extends TextWebSocketHandler {

    private static final Logger LOGGER = LoggerFactory.getLogger(VoiceRealtimeWebSocketHandler.class);

    private final JwtProvider jwtProvider;
    private final VoiceSessionService sessionService;
    private final VoiceRealtimeAsrClient realtimeAsrClient;
    private final AppProperties appProperties;
    private final ObjectMapper objectMapper;
    private final TaskExecutor voiceTaskExecutor;
    private final VoiceMetricLogger voiceMetricLogger;
    private final Map<String, VoiceSocketState> states = new ConcurrentHashMap<>();

    public VoiceRealtimeWebSocketHandler(
        JwtProvider jwtProvider,
        VoiceSessionService sessionService,
        VoiceRealtimeAsrClient realtimeAsrClient,
        AppProperties appProperties,
        ObjectMapper objectMapper,
        @Qualifier("voiceTaskExecutor") TaskExecutor voiceTaskExecutor,
        VoiceMetricLogger voiceMetricLogger
    ) {
        this.jwtProvider = jwtProvider;
        this.sessionService = sessionService;
        this.realtimeAsrClient = realtimeAsrClient;
        this.appProperties = appProperties;
        this.objectMapper = objectMapper;
        this.voiceTaskExecutor = voiceTaskExecutor;
        this.voiceMetricLogger = voiceMetricLogger;
    }

    @Override
    public void afterConnectionEstablished(WebSocketSession session) throws Exception {
        JwtAuthenticatedUser user = authenticate(session);
        UUID voiceSessionId = readSessionId(session.getUri());
        if (!sessionService.isOwnedBy(voiceSessionId, user.userId())) {
            session.close(CloseStatus.NOT_ACCEPTABLE.withReason("invalid voice session"));
            return;
        }
        session.setTextMessageSizeLimit(256 * 1024);
        session.setBinaryMessageSizeLimit(256 * 1024);
        VoiceSocketState state = new VoiceSocketState(user.userId(), voiceSessionId);
        states.put(session.getId(), state);
        send(session, "ready", Map.of(
            "sessionId", voiceSessionId.toString(),
            "sampleRate", appProperties.getVoice().getSampleRate(),
            "turnId", state.turnId()
        ));
        startAsrAsync(session, state, state.turnId());
    }

    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) throws Exception {
        VoiceSocketState state = states.get(session.getId());
        if (state == null) {
            session.close(CloseStatus.NOT_ACCEPTABLE);
            return;
        }
        JsonNode event = objectMapper.readTree(message.getPayload());
        String type = event.path("type").asText("");
        if ("audio_chunk".equals(type)) {
            appendAudio(session, state, event.path("data").asText(""), event.path("turnId").asText(""));
            return;
        }
        if ("commit".equals(type)) {
            String requestedTurnId = event.path("turnId").asText("");
            if (!requestedTurnId.isBlank() && !requestedTurnId.equals(state.turnId())) {
                return;
            }
            state.commit();
            send(session, "commit_ack", Map.of("turnId", state.turnId()));
            return;
        }
        if ("cancel".equals(type)) {
            cancelTurn(session, state);
        }
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        VoiceSocketState state = states.remove(session.getId());
        if (state != null) {
            state.close();
            sessionService.close(state.sessionId(), state.userId());
        }
    }

    private void appendAudio(WebSocketSession session, VoiceSocketState state, String base64, String requestedTurnId) throws Exception {
        if (base64 == null || base64.isBlank()) {
            return;
        }
        if (!requestedTurnId.isBlank() && !requestedTurnId.equals(state.turnId())) {
            return;
        }
        byte[] chunk = Base64.getDecoder().decode(base64);
        int currentBytes = state.addAudioBytes(chunk.length);
        if (currentBytes > appProperties.getVoice().getMaxAudioBytes()) {
            throw new IllegalArgumentException("voice audio too large");
        }
        try {
            state.appendAudio(chunk);
        } catch (RuntimeException ex) {
            LOGGER.warn("Realtime ASR append failed sessionId={} turnId={}: {}", state.sessionId(), state.turnId(), ex.getMessage());
            send(session, "error", Map.of("turnId", state.turnId(), "message", "语音识别连接异常，请重试"));
            cancelTurn(session, state);
        }
    }

    private void cancelTurn(WebSocketSession session, VoiceSocketState state) throws Exception {
        String cancelledTurnId = state.turnId();
        state.closeCurrentTurn();
        state.nextTurn();
        send(session, "cancelled", Map.of(
            "sessionId", state.sessionId().toString(),
            "cancelledTurnId", cancelledTurnId,
            "turnId", state.turnId()
        ));
        startAsrAsync(session, state, state.turnId());
    }

    private void startAsrAsync(WebSocketSession session, VoiceSocketState state, String turnId) {
        voiceTaskExecutor.execute(() -> {
            VoiceRealtimeAsrSession asrSession = null;
            try {
                if (!isActive(session, state, turnId)) {
                    return;
                }
                asrSession = newAsrSession(session, state, turnId);
                if (!state.attachAsr(turnId, asrSession)) {
                    asrSession.close();
                    return;
                }
                safeSend(session, "asr_ready", Map.of("turnId", turnId));
            } catch (Exception ex) {
                if (asrSession != null) {
                    asrSession.close();
                }
                if (isActive(session, state, turnId)) {
                    LOGGER.warn("Realtime ASR start failed sessionId={} turnId={}: {}", state.sessionId(), turnId, ex.getMessage());
                    safeSend(session, "error", Map.of("turnId", turnId, "message", "语音识别连接失败，请重试"));
                    closeQuietly(session, CloseStatus.SERVER_ERROR.withReason("asr start failed"));
                }
            }
        });
    }

    private boolean isActive(WebSocketSession session, VoiceSocketState state, String turnId) {
        return session.isOpen() && states.get(session.getId()) == state && state.isCurrentTurn(turnId);
    }

    private VoiceRealtimeAsrSession newAsrSession(WebSocketSession session, VoiceSocketState state, String turnId) {
        String sessionKey = state.sessionId() + ":" + turnId;
        return realtimeAsrClient.start(sessionKey, appProperties.getVoice().getSampleRate(), new VoiceRealtimeAsrListener() {
            @Override
            public void onReady() {
                if (isActive(session, state, turnId)) {
                    recordMetric("asr_ready_ms", state, turnId, "success");
                    safeSend(session, "asr_ready", Map.of("turnId", turnId));
                }
            }

            @Override
            public void onPartial(String text) {
                if (!text.isBlank() && isActive(session, state, turnId)) {
                    if (state.markFirstPartial(turnId)) {
                        recordMetric("asr_first_partial_ms", state, turnId, "success");
                    }
                    safeSend(session, "asr_partial", Map.of("turnId", turnId, "text", text));
                }
            }

            @Override
            public void onFinal(String text) {
                if (isActive(session, state, turnId)) {
                    recordMetric("asr_final_ms", state, turnId, "success");
                    safeSend(session, "asr_final", Map.of(
                        "turnId", turnId,
                        "text", text,
                        "provider", appProperties.getVoice().getProvider(),
                        "model", appProperties.getVoice().getAsrModel()
                    ));
                }
            }

            @Override
            public void onError(Throwable error) {
                LOGGER.warn("Realtime ASR failed sessionId={} turnId={}: {}", state.sessionId(), turnId, error.getMessage());
                if (isActive(session, state, turnId)) {
                    recordMetric("asr_error_ms", state, turnId, "error");
                    safeSend(session, "error", Map.of("turnId", turnId, "message", "语音识别失败，请重试"));
                }
            }
        });
    }

    private void recordMetric(String metric, VoiceSocketState state, String turnId, String outcome) {
        voiceMetricLogger.record(
            metric,
            state.sessionId(),
            turnId,
            state.elapsedMs(turnId),
            appProperties.getVoice().getProvider(),
            appProperties.getVoice().getAsrModel(),
            outcome
        );
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

    private void safeSend(WebSocketSession session, String type, Map<String, Object> payload) {
        try {
            synchronized (session) {
                send(session, type, payload);
            }
        } catch (Exception ex) {
            LOGGER.debug("Voice realtime send skipped type={}: {}", type, ex.getMessage());
        }
    }

    private void closeQuietly(WebSocketSession session, CloseStatus status) {
        try {
            session.close(status);
        } catch (Exception ex) {
            LOGGER.debug("Voice realtime close skipped: {}", ex.getMessage());
        }
    }

    private static final class VoiceSocketState {
        private final Object lock = new Object();
        private final UUID userId;
        private final UUID sessionId;
        private final AtomicInteger turnSequence = new AtomicInteger(1);
        private final AtomicInteger audioBytes = new AtomicInteger(0);
        private final Deque<byte[]> pendingAudio = new ArrayDeque<>();
        private volatile VoiceRealtimeAsrSession asrSession;
        private volatile String turnId;
        private volatile long turnStartedAtNanos;
        private volatile boolean firstPartialLogged;
        private boolean pendingCommit;

        private VoiceSocketState(UUID userId, UUID sessionId) {
            this.userId = userId;
            this.sessionId = sessionId;
            this.turnId = "turn-" + turnSequence.get();
            this.turnStartedAtNanos = System.nanoTime();
        }

        private UUID userId() {
            return userId;
        }

        private UUID sessionId() {
            return sessionId;
        }

        private String turnId() {
            return turnId;
        }

        private boolean isCurrentTurn(String requestedTurnId) {
            return turnId.equals(requestedTurnId);
        }

        private boolean attachAsr(String requestedTurnId, VoiceRealtimeAsrSession nextSession) {
            synchronized (lock) {
                if (!isCurrentTurn(requestedTurnId)) {
                    return false;
                }
                asrSession = nextSession;
                while (!pendingAudio.isEmpty()) {
                    nextSession.appendAudio(pendingAudio.removeFirst());
                }
                if (pendingCommit) {
                    nextSession.commit();
                    pendingCommit = false;
                }
                return true;
            }
        }

        private void appendAudio(byte[] chunk) {
            synchronized (lock) {
                if (asrSession == null) {
                    pendingAudio.addLast(chunk);
                    return;
                }
                asrSession.appendAudio(chunk);
            }
        }

        private void commit() {
            synchronized (lock) {
                if (asrSession == null) {
                    pendingCommit = true;
                    return;
                }
                asrSession.commit();
            }
        }

        private int addAudioBytes(int bytes) {
            return audioBytes.addAndGet(bytes);
        }

        private void nextTurn() {
            audioBytes.set(0);
            turnId = "turn-" + turnSequence.incrementAndGet();
            turnStartedAtNanos = System.nanoTime();
            firstPartialLogged = false;
        }

        private long elapsedMs(String requestedTurnId) {
            if (!isCurrentTurn(requestedTurnId)) {
                return -1L;
            }
            return TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - turnStartedAtNanos);
        }

        private boolean markFirstPartial(String requestedTurnId) {
            synchronized (lock) {
                if (!isCurrentTurn(requestedTurnId) || firstPartialLogged) {
                    return false;
                }
                firstPartialLogged = true;
                return true;
            }
        }

        private void closeCurrentTurn() {
            VoiceRealtimeAsrSession current = asrSession;
            synchronized (lock) {
                current = asrSession;
                asrSession = null;
                pendingAudio.clear();
                pendingCommit = false;
            }
            if (current != null) {
                current.close();
            }
        }

        private void close() {
            closeCurrentTurn();
        }
    }
}
