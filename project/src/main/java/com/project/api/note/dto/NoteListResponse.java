package com.project.api.note.dto;

import java.util.List;

public record NoteListResponse(
    List<NoteListItemResponse> items,
    long total,
    int page,
    int size
) {
}
