package com.project.application.note;

public record NoteRagIndexResult(
    boolean indexed,
    int chunkCount,
    String message
) {
}
