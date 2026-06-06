package com.project.api.resource.dto;

import java.time.OffsetDateTime;
import java.util.UUID;

public record ResourceUserStateResponse(
    UUID resourceId,
    boolean favorite,
    int progress,
    boolean completed,
    OffsetDateTime lastStudyAt
) {
}
