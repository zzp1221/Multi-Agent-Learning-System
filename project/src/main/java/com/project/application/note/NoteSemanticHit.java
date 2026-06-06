package com.project.application.note;

public record NoteSemanticHit(
    long chunkId,
    int chunkNo,
    double similarity,
    String content
) {
}
