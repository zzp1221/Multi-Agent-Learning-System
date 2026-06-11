package com.project.api.settings.dto;

import jakarta.validation.constraints.Size;

public record UserLlmSkillOverrideDto(
    boolean enabled,
    @Size(max = 80) String name,
    @Size(max = 240) String description,
    @Size(max = 8000) String body
) {
}
