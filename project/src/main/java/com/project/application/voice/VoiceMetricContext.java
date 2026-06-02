package com.project.application.voice;

import java.util.UUID;

public record VoiceMetricContext(
    UUID voiceSessionId,
    String turnId,
    UUID conversationId,
    String userHash,
    String pageType,
    String commandIntent
) {
    public static VoiceMetricContext empty(UUID voiceSessionId, String turnId) {
        return new VoiceMetricContext(voiceSessionId, turnId, null, "", "", "");
    }
}
