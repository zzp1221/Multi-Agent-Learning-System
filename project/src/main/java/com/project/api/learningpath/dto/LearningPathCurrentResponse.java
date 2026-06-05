package com.project.api.learningpath.dto;

import com.project.api.smartengine.dto.TaskStatusResponse;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * 当前持久化学习路径及最近后台刷新任务。
 */
public record LearningPathCurrentResponse(
    UUID planId,
    UUID userId,
    UUID courseId,
    String status,
    Map<String, Object> learningPath,
    Map<String, Object> activeStep,
    Map<String, Object> resourcePushPlan,
    List<Map<String, Object>> pushedResources,
    Integer version,
    String triggerSource,
    String summary,
    OffsetDateTime updatedAt,
    TaskStatusResponse refreshTask,
    TaskStatusResponse resourceRefreshTask
) {
}
