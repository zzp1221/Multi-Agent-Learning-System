package com.project.api.conversation.dto;

import jakarta.validation.constraints.Size;

import java.util.LinkedHashMap;
import java.util.Map;

public record VoiceContextRequest(
    @Size(max = 40, message = "页面类型不能超过 40 字")
    String pageType,

    @Size(max = 80, message = "题目 ID 不能超过 80 字")
    String questionId,

    @Size(max = 80, message = "课程 ID 不能超过 80 字")
    String courseId,

    @Size(max = 80, message = "知识点 ID 不能超过 80 字")
    String knowledgePointId,

    @Size(max = 120, message = "页面标题不能超过 120 字")
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
    String commandIntent
) {
    public Map<String, String> normalizedMap() {
        Map<String, String> context = new LinkedHashMap<>();
        putIfPresent(context, "pageType", pageType);
        putIfPresent(context, "questionId", questionId);
        putIfPresent(context, "courseId", courseId);
        putIfPresent(context, "knowledgePointId", knowledgePointId);
        putIfPresent(context, "pageTitle", pageTitle);
        putIfPresent(context, "currentPath", currentPath);
        putIfPresent(context, "source", source);
        putIfPresent(context, "conversationId", conversationId);
        putIfPresent(context, "recentMessagesSummary", recentMessagesSummary);
        putIfPresent(context, "commandIntent", commandIntent);
        return context;
    }

    public boolean hasContext() {
        return !normalizedMap().isEmpty();
    }

    private static void putIfPresent(Map<String, String> target, String key, String value) {
        if (value != null && !value.isBlank()) {
            target.put(key, value.trim());
        }
    }
}
