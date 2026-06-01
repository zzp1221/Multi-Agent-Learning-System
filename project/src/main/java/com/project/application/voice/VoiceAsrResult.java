package com.project.application.voice;

public record VoiceAsrResult(
    String text,
    int durationMs,
    String provider,
    String model
) {
}
