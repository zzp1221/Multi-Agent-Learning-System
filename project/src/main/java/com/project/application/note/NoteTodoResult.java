package com.project.application.note;

public record NoteTodoResult(
    String title,
    String priority,
    boolean completed
) {
}
