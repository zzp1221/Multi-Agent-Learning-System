package com.project.api.note.dto;

import java.util.UUID;

public record NoteTagResponse(
    UUID id,
    String name,
    String color,
    long count
) {
}
