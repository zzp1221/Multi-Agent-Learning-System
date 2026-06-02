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
import org.junit.jupiter.api.Test;
import org.springframework.core.task.TaskExecutor;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;

import java.net.URI;
import java.time.Duration;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Deque;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertTimeoutPreemptively;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class VoiceRealtimeWebSocketHandlerTest {

    private final ObjectMapper objectMapper = new ObjectMapper().findAndRegisterModules();
    private final AppProperties appProperties = new AppProperties();
    private final JwtAuthenticatedUser user = new JwtAuthenticatedUser(UUID.randomUUID(), "demo", "USER");

    @Test
    void sendsReadyBeforeAsrProviderConnectionCompletes() throws Exception {
        UUID voiceSessionId = UUID.randomUUID();
        List<String> outbound = new CopyOnWriteArrayList<>();
        WebSocketSession socket = socket(voiceSessionId, outbound);
        CountDownLatch providerStarted = new CountDownLatch(1);
        CountDownLatch releaseProvider = new CountDownLatch(1);
        VoiceRealtimeAsrClient blockingClient = (sessionKey, sampleRate, listener) -> {
            providerStarted.countDown();
            try {
                releaseProvider.await(2, TimeUnit.SECONDS);
            } catch (InterruptedException ex) {
                Thread.currentThread().interrupt();
            }
            return new FakeAsrSession();
        };

        VoiceRealtimeWebSocketHandler handler = handler(
            voiceSessionId,
            blockingClient,
            command -> new Thread(command, "voice-test-asr").start()
        );

        try {
            assertTimeoutPreemptively(Duration.ofMillis(200), () -> handler.afterConnectionEstablished(socket));

            assertThat(providerStarted.await(1, TimeUnit.SECONDS)).isTrue();
            assertThat(outbound).anySatisfy(payload -> assertThat(readType(payload)).isEqualTo("ready"));
        } finally {
            releaseProvider.countDown();
        }
    }

    @Test
    void buffersAudioAndCommitUntilAsrSessionIsReady() throws Exception {
        UUID voiceSessionId = UUID.randomUUID();
        List<String> outbound = new CopyOnWriteArrayList<>();
        WebSocketSession socket = socket(voiceSessionId, outbound);
        ManualTaskExecutor executor = new ManualTaskExecutor();
        CapturingAsrClient asrClient = new CapturingAsrClient();
        VoiceRealtimeWebSocketHandler handler = handler(voiceSessionId, asrClient, executor);

        handler.afterConnectionEstablished(socket);
        handler.handleTextMessage(socket, jsonMessage(Map.of(
            "type", "audio_chunk",
            "turnId", "turn-1",
            "data", base64(new byte[] {1, 2, 3})
        )));
        handler.handleTextMessage(socket, jsonMessage(Map.of(
            "type", "commit",
            "turnId", "turn-1"
        )));

        assertThat(asrClient.sessions).isEmpty();

        executor.runNext();

        FakeAsrSession session = asrClient.sessions.get(0);
        assertThat(session.appended).hasSize(1);
        assertThat(session.appended.get(0)).containsExactly(1, 2, 3);
        assertThat(session.commitCount).isEqualTo(1);
        assertThat(outbound).anySatisfy(payload -> assertThat(readType(payload)).isEqualTo("commit_ack"));
    }

    @Test
    void ignoresCallbacksFromCancelledTurn() throws Exception {
        UUID voiceSessionId = UUID.randomUUID();
        List<String> outbound = new CopyOnWriteArrayList<>();
        WebSocketSession socket = socket(voiceSessionId, outbound);
        ManualTaskExecutor executor = new ManualTaskExecutor();
        CapturingAsrClient asrClient = new CapturingAsrClient();
        VoiceRealtimeWebSocketHandler handler = handler(voiceSessionId, asrClient, executor);

        handler.afterConnectionEstablished(socket);
        executor.runNext();
        VoiceRealtimeAsrListener oldTurnListener = asrClient.listeners.get(voiceSessionId + ":turn-1");

        handler.handleTextMessage(socket, jsonMessage(Map.of(
            "type", "cancel",
            "turnId", "turn-1"
        )));
        oldTurnListener.onFinal("旧回答");
        executor.runNext();

        assertThat(asrClient.sessions.get(0).closed).isTrue();
        assertThat(outbound).anySatisfy(payload -> assertThat(readType(payload)).isEqualTo("cancelled"));
        assertThat(outbound).noneMatch(payload -> payload.contains("旧回答"));
    }

    @Test
    void keepsRecordingAfterProviderSegmentFinalAndMergesNextPartial() throws Exception {
        UUID voiceSessionId = UUID.randomUUID();
        List<String> outbound = new CopyOnWriteArrayList<>();
        WebSocketSession socket = socket(voiceSessionId, outbound);
        ManualTaskExecutor executor = new ManualTaskExecutor();
        CapturingAsrClient asrClient = new CapturingAsrClient();
        VoiceRealtimeWebSocketHandler handler = handler(voiceSessionId, asrClient, executor);

        handler.afterConnectionEstablished(socket);
        executor.runNext();
        VoiceRealtimeAsrListener listener = asrClient.listeners.get(voiceSessionId + ":turn-1");

        listener.onFinal("第一句");
        listener.onPartial("第二句");

        assertThat(outbound).anySatisfy(payload -> {
            assertThat(readType(payload)).isEqualTo("asr_final");
            assertThat(readText(payload)).isEqualTo("第一句");
        });
        assertThat(outbound).anySatisfy(payload -> {
            assertThat(readType(payload)).isEqualTo("asr_partial");
            assertThat(readText(payload)).isEqualTo("第一句第二句");
        });
    }

    private VoiceRealtimeWebSocketHandler handler(
        UUID voiceSessionId,
        VoiceRealtimeAsrClient asrClient,
        TaskExecutor executor
    ) {
        JwtProvider jwtProvider = mock(JwtProvider.class);
        VoiceSessionService sessionService = mock(VoiceSessionService.class);
        when(jwtProvider.parse("token")).thenReturn(user);
        when(sessionService.isOwnedBy(voiceSessionId, user.userId())).thenReturn(true);
        return new VoiceRealtimeWebSocketHandler(
            jwtProvider,
            sessionService,
            asrClient,
            appProperties,
            objectMapper,
            executor,
            new VoiceMetricLogger()
        );
    }

    private WebSocketSession socket(UUID voiceSessionId, List<String> outbound) throws Exception {
        WebSocketSession socket = mock(WebSocketSession.class);
        when(socket.getId()).thenReturn("ws-" + voiceSessionId);
        when(socket.getUri()).thenReturn(URI.create("ws://localhost/api/voice/ws?sessionId=" + voiceSessionId + "&token=token"));
        when(socket.isOpen()).thenReturn(true);
        doAnswer(invocation -> {
            TextMessage message = invocation.getArgument(0);
            outbound.add(message.getPayload());
            return null;
        }).when(socket).sendMessage(any(TextMessage.class));
        return socket;
    }

    private TextMessage jsonMessage(Map<String, ?> payload) throws Exception {
        return new TextMessage(objectMapper.writeValueAsString(payload));
    }

    private String readType(String payload) {
        try {
            JsonNode event = objectMapper.readTree(payload);
            return event.path("type").asText();
        } catch (Exception ex) {
            return "";
        }
    }

    private String readText(String payload) {
        try {
            JsonNode event = objectMapper.readTree(payload);
            return event.path("text").asText();
        } catch (Exception ex) {
            return "";
        }
    }

    private String base64(byte[] bytes) {
        return Base64.getEncoder().encodeToString(bytes);
    }

    private static final class ManualTaskExecutor implements TaskExecutor {
        private final Deque<Runnable> tasks = new ArrayDeque<>();

        @Override
        public void execute(Runnable task) {
            tasks.addLast(task);
        }

        private void runNext() {
            tasks.removeFirst().run();
        }
    }

    private static final class CapturingAsrClient implements VoiceRealtimeAsrClient {
        private final List<FakeAsrSession> sessions = new ArrayList<>();
        private final Map<String, VoiceRealtimeAsrListener> listeners = new ConcurrentHashMap<>();

        @Override
        public VoiceRealtimeAsrSession start(String sessionKey, int sampleRate, VoiceRealtimeAsrListener listener) {
            listeners.put(sessionKey, listener);
            FakeAsrSession session = new FakeAsrSession();
            sessions.add(session);
            return session;
        }
    }

    private static final class FakeAsrSession implements VoiceRealtimeAsrSession {
        private final List<byte[]> appended = new ArrayList<>();
        private int commitCount;
        private boolean closed;

        @Override
        public void appendAudio(byte[] pcmAudio) {
            appended.add(pcmAudio);
        }

        @Override
        public void commit() {
            commitCount++;
        }

        @Override
        public void cancel() {
        }

        @Override
        public void close() {
            closed = true;
        }
    }
}
