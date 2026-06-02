package com.project.api.voice.dto;

import java.util.Map;

public record VoiceCommandResponse(
    String intent,
    String normalizedText,
    boolean handledLocally,
    Map<String, String> context
) {
}
