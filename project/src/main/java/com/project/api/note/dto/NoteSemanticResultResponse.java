package com.project.api.note.dto;

import java.util.List;

public record NoteSemanticResultResponse(
    NoteListItemResponse note,
    double score,
    String reason,
    List<NoteSemanticHitResponse> hits
) {
}
