package com.project.application.voice;

import com.project.config.AppProperties;
import com.project.security.JwtAuthenticatedUser;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.core.task.TaskExecutor;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

@Service
public class VoiceAsrPrewarmService {

    private static final Logger LOGGER = LoggerFactory.getLogger(VoiceAsrPrewarmService.class);
    private final VoiceRealtimeAsrClient realtimeAsrClient;
    private final AppProperties appProperties;
    private final TaskExecutor voiceTaskExecutor;
    private final VoiceMetricLogger voiceMetricLogger;
    private final Map<UUID, PrewarmedAsrSession> sessions = new ConcurrentHashMap<>();

    public VoiceAsrPrewarmService(
        VoiceRealtimeAsrClient realtimeAsrClient,
        AppProperties appProperties,
        @Qualifier("voiceTaskExecutor") TaskExecutor voiceTaskExecutor,
        VoiceMetricLogger voiceMetricLogger
    ) {
        this.realtimeAsrClient = realtimeAsrClient;
        this.appProperties = appProperties;
        this.voiceTaskExecutor = voiceTaskExecutor;
        this.voiceMetricLogger = voiceMetricLogger;
    }

    public void prewarm(UUID voiceSessionId, JwtAuthenticatedUser currentUser) {
        if (voiceSessionId == null || currentUser == null) {
            return;
        }
        sessions.compute(voiceSessionId, (ignored, existing) -> {
            if (existing != null && !existing.isExpired()) {
                return existing;
            }
            if (existing != null) {
                existing.close();
            }
            PrewarmedAsrSession created = new PrewarmedAsrSession(
                voiceSessionId,
                currentUser.userId(),
                "turn-1",
                Instant.now().plus(appProperties.getVoice().getAsrPrewarmTtl())
            );
            startAsync(created);
            return created;
        });
    }

    public VoiceRealtimeAsrSession take(
        UUID voiceSessionId,
        UUID userId,
        String turnId,
        VoiceRealtimeAsrListener listener
    ) {
        PrewarmedAsrSession prewarmed = sessions.remove(voiceSessionId);
        if (prewarmed == null || prewarmed.isExpired() || !prewarmed.isOwnedBy(userId, turnId)) {
            if (prewarmed != null) {
                prewarmed.close();
            }
            return null;
        }
        VoiceRealtimeAsrSession session = prewarmed.take(listener);
        if (session == null) {
            prewarmed.close();
        }
        return session;
    }

    public void release(UUID voiceSessionId, UUID userId) {
        PrewarmedAsrSession prewarmed = sessions.remove(voiceSessionId);
        if (prewarmed != null && prewarmed.userId().equals(userId)) {
            prewarmed.close();
        }
    }

    @Scheduled(fixedDelay = 15_000)
    public void removeExpiredSessions() {
        sessions.entrySet().removeIf(entry -> {
            boolean expired = entry.getValue().isExpired();
            if (expired) {
                entry.getValue().close();
            }
            return expired;
        });
    }

    private void startAsync(PrewarmedAsrSession prewarmed) {
        voiceTaskExecutor.execute(() -> {
            long startedAtNanos = System.nanoTime();
            try {
                VoiceRealtimeAsrSession session = realtimeAsrClient.start(
                    prewarmed.voiceSessionId() + ":" + prewarmed.turnId(),
                    appProperties.getVoice().getSampleRate(),
                    prewarmed.listener()
                );
                if (!prewarmed.attach(session)) {
                    session.close();
                }
                record("asr_prewarm_ready_ms", prewarmed, startedAtNanos, "success", "");
            } catch (Exception ex) {
                LOGGER.debug("Voice ASR prewarm failed sessionId={}: {}", prewarmed.voiceSessionId(), ex.getMessage());
                record("asr_prewarm_error_ms", prewarmed, startedAtNanos, "error", ex.getClass().getSimpleName());
                sessions.remove(prewarmed.voiceSessionId(), prewarmed);
                prewarmed.fail(ex);
            }
        });
    }

    private void record(String metric, PrewarmedAsrSession prewarmed, long startedAtNanos, String outcome, String errorCode) {
        voiceMetricLogger.record(
            metric,
            VoiceMetricContext.empty(prewarmed.voiceSessionId(), prewarmed.turnId()),
            java.util.concurrent.TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAtNanos),
            appProperties.getVoice().getProvider(),
            appProperties.getVoice().getAsrModel(),
            outcome,
            null,
            null,
            errorCode
        );
    }

    private static final class PrewarmedAsrSession {
        private final UUID voiceSessionId;
        private final UUID userId;
        private final String turnId;
        private final Instant expiresAt;
        private final AtomicBoolean taken = new AtomicBoolean(false);
        private final AtomicReference<VoiceRealtimeAsrListener> activeListener = new AtomicReference<>();
        private volatile VoiceRealtimeAsrSession session;
        private volatile Throwable startupError;

        private PrewarmedAsrSession(UUID voiceSessionId, UUID userId, String turnId, Instant expiresAt) {
            this.voiceSessionId = voiceSessionId;
            this.userId = userId;
            this.turnId = turnId;
            this.expiresAt = expiresAt;
        }

        private UUID voiceSessionId() {
            return voiceSessionId;
        }

        private UUID userId() {
            return userId;
        }

        private String turnId() {
            return turnId;
        }

        private VoiceRealtimeAsrListener listener() {
            return new VoiceRealtimeAsrListener() {
                @Override
                public void onReady() {
                    VoiceRealtimeAsrListener listener = activeListener.get();
                    if (listener != null) {
                        listener.onReady();
                    }
                }

                @Override
                public void onPartial(String text) {
                    VoiceRealtimeAsrListener listener = activeListener.get();
                    if (listener != null) {
                        listener.onPartial(text);
                    }
                }

                @Override
                public void onFinal(String text) {
                    VoiceRealtimeAsrListener listener = activeListener.get();
                    if (listener != null) {
                        listener.onFinal(text);
                    }
                }

                @Override
                public void onError(Throwable error) {
                    VoiceRealtimeAsrListener listener = activeListener.get();
                    if (listener != null) {
                        listener.onError(error);
                    }
                }
            };
        }

        private boolean attach(VoiceRealtimeAsrSession nextSession) {
            if (isExpired() || taken.get() || startupError != null) {
                return false;
            }
            session = nextSession;
            return true;
        }

        private VoiceRealtimeAsrSession take(VoiceRealtimeAsrListener listener) {
            if (!taken.compareAndSet(false, true) || isExpired() || startupError != null) {
                return null;
            }
            activeListener.set(listener);
            return session;
        }

        private void fail(Throwable error) {
            startupError = error;
        }

        private boolean isOwnedBy(UUID requestedUserId, String requestedTurnId) {
            return userId.equals(requestedUserId) && turnId.equals(requestedTurnId);
        }

        private boolean isExpired() {
            return !Instant.now().isBefore(expiresAt);
        }

        private void close() {
            VoiceRealtimeAsrSession current = session;
            if (current != null) {
                current.close();
            }
        }
    }
}
