package com.project.api.note.dto;

import java.time.OffsetDateTime;
import java.util.List;

public record NoteAnalysisResponse(
    String inputHash,
    String summary,
    List<String> keywords,
    List<NoteTodoResponse> todos,
    String provider,
    String model,
    OffsetDateTime generatedAt,
    boolean fromCache
) {
}
