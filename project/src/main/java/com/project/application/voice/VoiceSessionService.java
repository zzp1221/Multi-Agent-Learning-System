package com.project.application.voice;

import com.project.config.AppProperties;
import com.project.security.JwtAuthenticatedUser;
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

    public VoiceSessionState create(JwtAuthenticatedUser currentUser) {
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
}
