package com.project.api.study.dto;

import com.project.api.learningpath.dto.LearningPathCurrentResponse;
import com.project.api.mistake.dto.MistakeRecordResponse;
import com.project.api.profile.dto.KnowledgeGraphResponse;
import com.project.api.profile.dto.UserProfileResponse;
import com.project.api.resource.dto.ResourceItemResponse;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;

public record DailyStudyWorkbenchResponse(
    UUID userId,
    LocalDate workDate,
    OffsetDateTime generatedAt,
    WorkbenchSummary summary,
    LearningPathCurrentResponse learningPath,
    Map<String, Object> activeStep,
    DailyExecutionPlan executionPlan,
    List<DailyTaskItem> tasks,
    List<MistakeRecordResponse> dueMistakes,
    List<ResourceItemResponse> recommendedResources,
    KnowledgeGraphResponse knowledgeGraph,
    UserProfileResponse profile,
    boolean dataAvailable
) {
    public record WorkbenchSummary(
        int totalTasks,
        int completedTasks,
        int dueMistakeCount,
        int recommendedResourceCount,
        int weakKnowledgeCount,
        int progressPercent,
        String nextAction,
        boolean stageTestReady
    ) {}

    public record DailyTaskItem(
        String id,
        String type,
        String title,
        String description,
        String status,
        Integer progress,
        String actionLabel,
        String actionRoute,
        Map<String, Object> actionPayload,
        OffsetDateTime dueAt
    ) {}

    public record DailyExecutionPlan(
        String title,
        String subtitle,
        String focusReason,
        String successCriteria,
        int estimatedMinutes,
        DailyTaskItem primaryTask,
        List<LearningSessionStep> steps,
        List<PlanSupportItem> supportItems
    ) {}

    public record LearningSessionStep(
        String id,
        String phase,
        String title,
        String description,
        String status,
        Integer minutes,
        String actionLabel,
        String actionRoute,
        String sourceTaskId,
        String sourceTaskType
    ) {}

    public record PlanSupportItem(
        String id,
        String type,
        String title,
        String description,
        String actionRoute
    ) {}
}
