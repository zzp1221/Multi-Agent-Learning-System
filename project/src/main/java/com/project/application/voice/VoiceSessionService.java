package com.project.application.voice;

import com.project.config.AppProperties;
import com.project.security.JwtAuthenticatedUser;
import com.project.application.common.ApplicationException;
import org.springframework.http.HttpStatus;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

@Service
public class VoiceSessionService {

    private final AppProperties appProperties;
    private final Map<UUID, VoiceSessionState> sessions = new ConcurrentHashMap<>();

    public VoiceSessionService(AppProperties appProperties) {
        this.appProperties = appProperties;
    }

    public synchronized VoiceSessionState create(JwtAuthenticatedUser currentUser) {
        removeExpiredSessions();
        enforceUserSessionLimit(currentUser.userId());
        OffsetDateTime expiresAt = OffsetDateTime.now().plus(appProperties.getVoice().getSessionTtl());
        VoiceSessionState session = new VoiceSessionState(UUID.randomUUID(), currentUser.userId(), expiresAt);
        sessions.put(session.sessionId(), session);
        return session;
    }

    public boolean isOwnedBy(UUID sessionId, UUID userId) {
        VoiceSessionState session = sessions.get(sessionId);
        if (session == null || session.isExpired(OffsetDateTime.now())) {
            sessions.remove(sessionId);
            return false;
        }
        return session.userId().equals(userId);
    }

    public void close(UUID sessionId, UUID userId) {
        VoiceSessionState session = sessions.get(sessionId);
        if (session != null && session.userId().equals(userId)) {
            sessions.remove(sessionId);
        }
    }

    @Scheduled(fixedDelay = 60_000)
    public void removeExpiredSessions() {
        OffsetDateTime now = OffsetDateTime.now();
        sessions.entrySet().removeIf(entry -> entry.getValue().isExpired(now));
    }

    int activeSessionCount(UUID userId) {
        removeExpiredSessions();
        return (int) sessions.values().stream()
            .filter(session -> session.userId().equals(userId))
            .count();
    }

    private void enforceUserSessionLimit(UUID userId) {
        int maxSessions = appProperties.getVoice().getMaxConcurrentSessionsPerUser();
        if (maxSessions <= 0 || activeSessionCount(userId) < maxSessions) {
            return;
        }
        throw new ApplicationException(
            "VOICE_SESSION_LIMIT_EXCEEDED",
            "语音会话过多，请关闭其他语音窗口后重试",
            HttpStatus.TOO_MANY_REQUESTS
        );
    }
}
