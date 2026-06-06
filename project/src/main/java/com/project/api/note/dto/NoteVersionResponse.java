package com.project.api.note.dto;

import java.time.OffsetDateTime;
import java.util.UUID;

public record NoteVersionResponse(
    UUID id,
    int versionNo,
    String title,
    String markdownContent,
    String plainText,
    String contentHash,
    String changeSummary,
    OffsetDateTime createdAt
) {
}
