package com.project.application.voice;

public record VoiceTtsChunk(
    String audioBase64,
    int sampleRate,
    String format,
    boolean finished
) {
}
