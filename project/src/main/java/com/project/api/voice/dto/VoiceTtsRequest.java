package com.project.api.voice.dto;

import jakarta.validation.constraints.Size;

public record VoiceTtsRequest(
    @Size(max = 1200, message = "朗读内容不能超过 1200 字")
    String text,
    String voice
) {
    public String normalizedText() {
        return text == null ? "" : text.trim();
    }

    public String normalizedVoice() {
        return voice == null ? "" : voice.trim();
    }
}
