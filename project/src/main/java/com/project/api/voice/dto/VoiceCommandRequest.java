package com.project.api.voice.dto;

import jakarta.validation.constraints.Size;

public record VoiceCommandRequest(
    @Size(max = 500, message = "语音指令不能超过 500 字")
    String text,
    String pageType,
    String questionId,
    String courseId,
    String knowledgePointId,
    String pageTitle,
    @Size(max = 160, message = "当前路径不能超过 160 字")
    String currentPath,
    @Size(max = 40, message = "来源不能超过 40 字")
    String source,
    @Size(max = 80, message = "会话 ID 不能超过 80 字")
    String conversationId,
    @Size(max = 800, message = "最近对话摘要不能超过 800 字")
    String recentMessagesSummary,
    @Size(max = 60, message = "语音意图不能超过 60 字")
    String commandIntent,
    @Size(max = 80, message = "语音会话 ID 不能超过 80 字")
    String voiceSessionId,
    @Size(max = 40, message = "语音轮次 ID 不能超过 40 字")
    String voiceTurnId,
    String selectedService,
    String formParametersSummary,
    String taskStatus,
    String currentMistakeSummary,
    String reviewStatus,
    String weakPointsSummary,
    String currentGoal,
    String lowestMasteryKnowledge,
    String resourceResultSummary,
    String downloadResourceSummary,
    String recommendedAction
) {
    public String normalizedText() {
        return text == null ? "" : text.trim();
    }
}
