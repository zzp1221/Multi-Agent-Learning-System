package com.project.api.settings.dto;

import jakarta.validation.constraints.Size;

public record UserLlmComponentOverrideDto(
    @Size(max = 64) String provider,
    @Size(max = 256) String model
) {
}
