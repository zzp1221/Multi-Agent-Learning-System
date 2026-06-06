package com.project.api.note.dto;

import java.util.List;

public record NoteSemanticSearchResponse(
    String query,
    boolean available,
    String message,
    List<NoteSemanticResultResponse> results
) {
}
