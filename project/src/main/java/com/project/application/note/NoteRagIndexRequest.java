package com.project.application.note;

import java.util.List;
import java.util.UUID;

public record NoteRagIndexRequest(
    UUID userId,
    UUID noteId,
    UUID resourceId,
    String title,
    String markdownContent,
    String plainText,
    String contentHash,
    List<String> tags
) {
}
