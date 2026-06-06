package com.project.api.resource.dto;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;

public record ResourceProgressRequest(
    @Min(0) @Max(100) Integer progress,
    Boolean completed
) {
}
