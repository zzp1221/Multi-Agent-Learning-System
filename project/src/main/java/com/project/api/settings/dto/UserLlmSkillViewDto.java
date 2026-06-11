package com.project.api.settings.dto;

public record UserLlmSkillViewDto(
    boolean enabled,
    String name,
    String description,
    String body
) {
}
