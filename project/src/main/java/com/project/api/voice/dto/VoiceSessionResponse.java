package com.project.api.voice.dto;

import java.time.OffsetDateTime;
import java.util.UUID;

public record VoiceSessionResponse(
    UUID sessionId,
    OffsetDateTime expiresAt,
    int sampleRate,
    String provider,
    String asrModel,
    String ttsModel
) {
}
