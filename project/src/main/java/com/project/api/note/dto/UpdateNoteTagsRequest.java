package com.project.api.note.dto;

import jakarta.validation.constraints.Size;

import java.util.List;

public record UpdateNoteTagsRequest(
    List<@Size(max = 32, message = "标签不能超过 32 字") String> tags
) {
}
