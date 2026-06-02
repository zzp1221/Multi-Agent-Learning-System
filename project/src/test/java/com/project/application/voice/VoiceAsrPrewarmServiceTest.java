package com.project.application.voice;

import com.project.config.AppProperties;
import com.project.security.JwtAuthenticatedUser;
import org.junit.jupiter.api.Test;
import org.springframework.core.task.SyncTaskExecutor;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

class VoiceAsrPrewarmServiceTest {

    private final AppProperties appProperties = new AppProperties();
    private final JwtAuthenticatedUser user = new JwtAuthenticatedUser(UUID.randomUUID(), "demo", "USER");

    @Test
    void takesPrewarmedSessionForOwnerAndTurn() {
        CapturingRealtimeAsrClient asrClient = new CapturingRealtimeAsrClient();
        VoiceAsrPrewarmService service = new VoiceAsrPrewarmService(
            asrClient,
            appProperties,
            new SyncTaskExecutor(),
            new VoiceMetricLogger()
        );
        UUID voiceSessionId = UUID.randomUUID();

        service.prewarm(voiceSessionId, user);
        VoiceRealtimeAsrSession session = service.take(voiceSessionId, user.userId(), "turn-1", listener());

        assertThat(session).isNotNull();
        assertThat(asrClient.sessions).hasSize(1);
        session.appendAudio(new byte[] {1, 2});
        assertThat(asrClient.sessions.get(0).appended).hasSize(1);
    }

    @Test
    void expiredPrewarmIsClosedAndNotTaken() {
        appProperties.getVoice().setAsrPrewarmTtl(Duration.ZERO);
        CapturingRealtimeAsrClient asrClient = new CapturingRealtimeAsrClient();
        VoiceAsrPrewarmService service = new VoiceAsrPrewarmService(
            asrClient,
            appProperties,
            new SyncTaskExecutor(),
            new VoiceMetricLogger()
        );
        UUID voiceSessionId = UUID.randomUUID();

        service.prewarm(voiceSessionId, user);
        VoiceRealtimeAsrSession session = service.take(voiceSessionId, user.userId(), "turn-1", listener());

        assertThat(session).isNull();
        assertThat(asrClient.sessions.get(0).closed).isTrue();
    }

    @Test
    void takeWaitsForAsyncPrewarmStartup() throws Exception {
        appProperties.getVoice().setConnectTimeout(Duration.ofSeconds(1));
        BlockingRealtimeAsrClient asrClient = new BlockingRealtimeAsrClient();
        VoiceAsrPrewarmService service = new VoiceAsrPrewarmService(
            asrClient,
            appProperties,
            command -> new Thread(command, "voice-prewarm-test").start(),
            new VoiceMetricLogger()
        );
        UUID voiceSessionId = UUID.randomUUID();

        service.prewarm(voiceSessionId, user);
        assertThat(asrClient.started.await(1, TimeUnit.SECONDS)).isTrue();
        Thread releaser = new Thread(() -> {
            try {
                Thread.sleep(50);
            } catch (InterruptedException ex) {
                Thread.currentThread().interrupt();
            }
            asrClient.releaseStartup.countDown();
        }, "voice-prewarm-release-test");
        releaser.start();
        VoiceRealtimeAsrSession session = service.take(voiceSessionId, user.userId(), "turn-1", listener());
        releaser.join(1_000);

        assertThat(session).isNotNull();
        assertThat(asrClient.sessions).hasSize(1);
    }

    @Test
    void releaseByDifferentUserDoesNotDropPrewarm() {
        CapturingRealtimeAsrClient asrClient = new CapturingRealtimeAsrClient();
        VoiceAsrPrewarmService service = new VoiceAsrPrewarmService(
            asrClient,
            appProperties,
            new SyncTaskExecutor(),
            new VoiceMetricLogger()
        );
        UUID voiceSessionId = UUID.randomUUID();

        service.prewarm(voiceSessionId, user);
        service.release(voiceSessionId, UUID.randomUUID());
        VoiceRealtimeAsrSession session = service.take(voiceSessionId, user.userId(), "turn-1", listener());

        assertThat(session).isNotNull();
        assertThat(asrClient.sessions.get(0).closed).isFalse();
    }

    private VoiceRealtimeAsrListener listener() {
        return new VoiceRealtimeAsrListener() {
            @Override
            public void onReady() {
            }

            @Override
            public void onPartial(String text) {
            }

            @Override
            public void onFinal(String text) {
            }

            @Override
            public void onError(Throwable error) {
            }
        };
    }

    private static final class CapturingRealtimeAsrClient implements VoiceRealtimeAsrClient {
        private final List<FakeRealtimeAsrSession> sessions = new ArrayList<>();

        @Override
        public VoiceRealtimeAsrSession start(String sessionKey, int sampleRate, VoiceRealtimeAsrListener listener) {
            FakeRealtimeAsrSession session = new FakeRealtimeAsrSession();
            sessions.add(session);
            listener.onReady();
            return session;
        }
    }

    private static final class BlockingRealtimeAsrClient implements VoiceRealtimeAsrClient {
        private final List<FakeRealtimeAsrSession> sessions = new ArrayList<>();
        private final CountDownLatch started = new CountDownLatch(1);
        private final CountDownLatch releaseStartup = new CountDownLatch(1);

        @Override
        public VoiceRealtimeAsrSession start(String sessionKey, int sampleRate, VoiceRealtimeAsrListener listener) {
            started.countDown();
            try {
                releaseStartup.await(1, TimeUnit.SECONDS);
            } catch (InterruptedException ex) {
                Thread.currentThread().interrupt();
            }
            FakeRealtimeAsrSession session = new FakeRealtimeAsrSession();
            sessions.add(session);
            listener.onReady();
            return session;
        }
    }

    private static final class FakeRealtimeAsrSession implements VoiceRealtimeAsrSession {
        private final List<byte[]> appended = new ArrayList<>();
        private boolean closed;

        @Override
        public void appendAudio(byte[] pcmAudio) {
            appended.add(pcmAudio);
        }

        @Override
        public void commit() {
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
