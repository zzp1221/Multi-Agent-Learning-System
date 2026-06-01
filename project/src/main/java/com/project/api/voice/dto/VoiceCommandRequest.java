package com.project.api.voice.dto;

import jakarta.validation.constraints.Size;

public record VoiceCommandRequest(
    @Size(max = 500, message = "语音指令不能超过 500 字")
    String text,
    String pageType,
    String questionId,
    String courseId,
    String knowledgePointId,
    String pageTitle
) {
    public String normalizedText() {
        return text == null ? "" : text.trim();
    }
}
