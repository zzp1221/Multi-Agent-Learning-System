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
    String commandIntent,

    @Size(max = 80, message = "语音会话 ID 不能超过 80 字")
    String voiceSessionId,

    @Size(max = 40, message = "语音轮次 ID 不能超过 40 字")
    String voiceTurnId,

    @Size(max = 80, message = "当前服务不能超过 80 字")
    String selectedService,

    @Size(max = 800, message = "表单参数摘要不能超过 800 字")
    String formParametersSummary,

    @Size(max = 120, message = "任务状态不能超过 120 字")
    String taskStatus,

    @Size(max = 800, message = "当前错题摘要不能超过 800 字")
    String currentMistakeSummary,

    @Size(max = 120, message = "复习状态不能超过 120 字")
    String reviewStatus,

    @Size(max = 800, message = "薄弱点摘要不能超过 800 字")
    String weakPointsSummary,

    @Size(max = 240, message = "当前目标不能超过 240 字")
    String currentGoal,

    @Size(max = 240, message = "最低掌握知识点不能超过 240 字")
    String lowestMasteryKnowledge,

    @Size(max = 800, message = "资源结果摘要不能超过 800 字")
    String resourceResultSummary,

    @Size(max = 500, message = "下载资源摘要不能超过 500 字")
    String downloadResourceSummary,

    @Size(max = 240, message = "推荐操作不能超过 240 字")
    String recommendedAction,

    @Size(max = 80, message = "学习阶段 ID 不能超过 80 字")
    String activeLearningStepId,

    @Size(max = 160, message = "学习阶段标题不能超过 160 字")
    String activeLearningStepTitle,

    @Size(max = 20, message = "学习阶段进度不能超过 20 字")
    String activeLearningStepProgress,

    @Size(max = 800, message = "学习阶段摘要不能超过 800 字")
    String activeLearningStepSummary,

    @Size(max = 160, message = "用户指定主题不能超过 160 字")
    String explicitUserTopic,

    @Size(max = 20, message = "题量不能超过 20 字")
    String questionCount,

    @Size(max = 60, message = "题型偏好不能超过 60 字")
    String questionTypePreference,

    @Size(max = 60, message = "难度偏好不能超过 60 字")
    String difficultyPreference,

    @Size(max = 20, message = "PPT 大纲确认标记不能超过 20 字")
    String requiresSlideOutlineConfirmation,

    @Size(max = 20, message = "PPT 确认标记不能超过 20 字")
    String confirmedSlideOutline,

    @Size(max = 4000, message = "PPT 大纲文本不能超过 4000 字")
    String confirmedSlideOutlineText
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
        putIfPresent(context, "voiceSessionId", voiceSessionId);
        putIfPresent(context, "voiceTurnId", voiceTurnId);
        putIfPresent(context, "selectedService", selectedService);
        putIfPresent(context, "formParametersSummary", formParametersSummary);
        putIfPresent(context, "taskStatus", taskStatus);
        putIfPresent(context, "currentMistakeSummary", currentMistakeSummary);
        putIfPresent(context, "reviewStatus", reviewStatus);
        putIfPresent(context, "weakPointsSummary", weakPointsSummary);
        putIfPresent(context, "currentGoal", currentGoal);
        putIfPresent(context, "lowestMasteryKnowledge", lowestMasteryKnowledge);
        putIfPresent(context, "resourceResultSummary", resourceResultSummary);
        putIfPresent(context, "downloadResourceSummary", downloadResourceSummary);
        putIfPresent(context, "recommendedAction", recommendedAction);
        putIfPresent(context, "activeLearningStepId", activeLearningStepId);
        putIfPresent(context, "activeLearningStepTitle", activeLearningStepTitle);
        putIfPresent(context, "activeLearningStepProgress", activeLearningStepProgress);
        putIfPresent(context, "activeLearningStepSummary", activeLearningStepSummary);
        putIfPresent(context, "explicitUserTopic", explicitUserTopic);
        putIfPresent(context, "questionCount", questionCount);
        putIfPresent(context, "questionTypePreference", questionTypePreference);
        putIfPresent(context, "difficultyPreference", difficultyPreference);
        putIfPresent(context, "requiresSlideOutlineConfirmation", requiresSlideOutlineConfirmation);
        putIfPresent(context, "confirmedSlideOutline", confirmedSlideOutline);
        putIfPresent(context, "confirmedSlideOutlineText", confirmedSlideOutlineText);
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
