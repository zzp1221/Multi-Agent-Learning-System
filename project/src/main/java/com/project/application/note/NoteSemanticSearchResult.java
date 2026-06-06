package com.project.application.note;

import java.util.List;

public record NoteSemanticSearchResult(
    String query,
    boolean available,
    String message,
    List<NoteSemanticResult> results
) {
}
