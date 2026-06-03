package com.project.api.voice.dto;

public record VoiceTranscribeResponse(
    String text,
    int durationMs,
    String provider,
    String model
) {
}
