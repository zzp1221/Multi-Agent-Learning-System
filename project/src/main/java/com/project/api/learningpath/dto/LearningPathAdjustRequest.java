package com.project.api.learningpath.dto;

import jakarta.validation.constraints.Size;

/**
 * 用户手动调整学习路径时提交的调整意图。
 */
public record LearningPathAdjustRequest(
    @Size(max = 1000) String adjustmentIntent
) {
}
