package com.project.api.note.dto;

public record NoteTodoResponse(
    String title,
    String priority,
    boolean completed
) {
}
