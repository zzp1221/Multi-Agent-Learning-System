package com.project.api.note.dto;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

public record NoteListItemResponse(
    UUID id,
    UUID folderId,
    String title,
    String preview,
    List<NoteTagResponse> tags,
    int wordCount,
    int readingMinutes,
    OffsetDateTime lastSavedAt,
    OffsetDateTime updatedAt,
    boolean ragIndexed
) {
}
