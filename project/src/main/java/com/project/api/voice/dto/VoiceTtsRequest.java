package com.project.api.voice.dto;

import jakarta.validation.constraints.Size;

public record VoiceTtsRequest(
    @Size(max = 1200, message = "朗读内容不能超过 1200 字")
    String text,
    String voice,
    @Size(max = 80, message = "语音会话 ID 不能超过 80 字")
    String voiceSessionId,
    @Size(max = 40, message = "语音轮次 ID 不能超过 40 字")
    String voiceTurnId,
    @Size(max = 80, message = "会话 ID 不能超过 80 字")
    String conversationId,
    @Size(max = 40, message = "页面类型不能超过 40 字")
    String pageType,
    @Size(max = 60, message = "语音意图不能超过 60 字")
    String commandIntent,
    Boolean turnComplete
) {
    public String normalizedText() {
        return text == null ? "" : text.trim();
    }

    public String normalizedVoice() {
        return voice == null ? "" : voice.trim();
    }

    public boolean isTurnComplete() {
        return turnComplete == null || Boolean.TRUE.equals(turnComplete);
    }
}
