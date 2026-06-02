package com.project.application.voice;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.time.Instant;
import java.util.HexFormat;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

@Service
public class VoiceTurnMetricsService {

    private static final Duration TURN_TTL = Duration.ofMinutes(15);

    private final Map<String, VoiceTurnTrace> traces = new ConcurrentHashMap<>();

    public VoiceTurnTrace startAsrTurn(UUID voiceSessionId, UUID userId, String turnId) {
        VoiceTurnTrace trace = new VoiceTurnTrace(
            voiceSessionId,
            turnId,
            hashUser(userId),
            System.nanoTime(),
            Instant.now().plus(TURN_TTL)
        );
        traces.put(traceKey(voiceSessionId, turnId), trace);
        return trace;
    }

    public VoiceTurnTrace find(UUID voiceSessionId, String turnId) {
        VoiceTurnTrace trace = traces.get(traceKey(voiceSessionId, turnId));
        if (trace == null || trace.isExpired()) {
            traces.remove(traceKey(voiceSessionId, turnId));
            return null;
        }
        return trace;
    }

    public VoiceMetricContext context(UUID voiceSessionId, String turnId) {
        VoiceTurnTrace trace = find(voiceSessionId, turnId);
        return trace == null ? VoiceMetricContext.empty(voiceSessionId, turnId) : trace.context();
    }

    public void attachConversation(UUID voiceSessionId, String turnId, UUID conversationId, String pageType, String commandIntent) {
        VoiceTurnTrace trace = find(voiceSessionId, turnId);
        if (trace != null) {
            trace.attachConversation(conversationId, pageType, commandIntent);
        }
    }

    public long elapsedMs(UUID voiceSessionId, String turnId) {
        VoiceTurnTrace trace = find(voiceSessionId, turnId);
        return trace == null ? -1L : trace.elapsedMs();
    }

    public long markFirstAudio(UUID voiceSessionId, String turnId) {
        VoiceTurnTrace trace = find(voiceSessionId, turnId);
        return trace == null ? -1L : trace.markFirstAudio();
    }

    public void complete(UUID voiceSessionId, String turnId) {
        traces.remove(traceKey(voiceSessionId, turnId));
    }

    @Scheduled(fixedDelay = 60_000)
    public void removeExpiredTraces() {
        traces.entrySet().removeIf(entry -> entry.getValue().isExpired());
    }

    private String traceKey(UUID voiceSessionId, String turnId) {
        return voiceSessionId + ":" + turnId;
    }

    private String hashUser(UUID userId) {
        if (userId == null) {
            return "";
        }
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] bytes = digest.digest(userId.toString().getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(bytes, 0, 8);
        } catch (NoSuchAlgorithmException ex) {
            return Integer.toHexString(userId.hashCode());
        }
    }

    public static final class VoiceTurnTrace {
        private final UUID voiceSessionId;
        private final String turnId;
        private final String userHash;
        private final long startedAtNanos;
        private final Instant expiresAt;
        private volatile UUID conversationId;
        private volatile String pageType = "";
        private volatile String commandIntent = "";
        private volatile boolean firstAudioLogged;

        private VoiceTurnTrace(UUID voiceSessionId, String turnId, String userHash, long startedAtNanos, Instant expiresAt) {
            this.voiceSessionId = voiceSessionId;
            this.turnId = turnId;
            this.userHash = userHash;
            this.startedAtNanos = startedAtNanos;
            this.expiresAt = expiresAt;
        }

        private void attachConversation(UUID conversationId, String pageType, String commandIntent) {
            this.conversationId = conversationId;
            this.pageType = pageType == null ? "" : pageType.trim();
            this.commandIntent = commandIntent == null ? "" : commandIntent.trim();
        }

        private long markFirstAudio() {
            if (firstAudioLogged) {
                return -1L;
            }
            firstAudioLogged = true;
            return elapsedMs();
        }

        private long elapsedMs() {
            return java.util.concurrent.TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAtNanos);
        }

        private boolean isExpired() {
            return Instant.now().isAfter(expiresAt);
        }

        private VoiceMetricContext context() {
            return new VoiceMetricContext(voiceSessionId, turnId, conversationId, userHash, pageType, commandIntent);
        }
    }
}
