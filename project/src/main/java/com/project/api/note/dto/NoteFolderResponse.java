package com.project.api.note.dto;

import java.time.OffsetDateTime;
import java.util.UUID;

public record NoteFolderResponse(
    UUID id,
    UUID parentId,
    String name,
    int sortOrder,
    long noteCount,
    OffsetDateTime createdAt,
    OffsetDateTime updatedAt
) {
}
