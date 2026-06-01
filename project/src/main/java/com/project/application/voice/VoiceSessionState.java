package com.project.application.voice;

import java.time.OffsetDateTime;
import java.util.UUID;

public record VoiceSessionState(
    UUID sessionId,
    UUID userId,
    OffsetDateTime expiresAt
) {
    public boolean isExpired(OffsetDateTime now) {
        return !expiresAt.isAfter(now);
    }
}
