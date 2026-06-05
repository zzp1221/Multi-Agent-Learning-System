package com.project.api.profile.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/**
 * 新用户首次进入系统时必须补全的基础学习画像。
 */
public record ProfileOnboardingRequest(
    @NotBlank @Size(max = 32) String majorCode,
    @NotBlank @Size(max = 80) String knowledgeBase,
    @NotBlank @Size(max = 120) String learningGoal,
    @NotBlank @Size(max = 80) String learningPreference,
    @NotBlank @Size(max = 80) String resourcePreference
) {
}
