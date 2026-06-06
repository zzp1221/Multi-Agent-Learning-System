package com.project.application.note;

import java.util.List;
import java.util.UUID;

public record NoteSemanticResult(
    UUID noteId,
    UUID resourceId,
    double score,
    String reason,
    List<NoteSemanticHit> hits
) {
}
