package com.project.api.note.dto;

public record NoteSemanticHitResponse(
    long chunkId,
    int chunkNo,
    double similarity,
    String content
) {
}
