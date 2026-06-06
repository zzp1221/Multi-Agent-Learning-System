package com.project.api.note.dto;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

public record NoteDetailResponse(
    UUID id,
    UUID folderId,
    String title,
    String markdownContent,
    String plainText,
    String contentHash,
    List<NoteTagResponse> tags,
    int wordCount,
    int readingMinutes,
    OffsetDateTime lastSavedAt,
    OffsetDateTime createdAt,
    OffsetDateTime updatedAt,
    boolean ragIndexed,
    UUID ragResourceId
) {
}
