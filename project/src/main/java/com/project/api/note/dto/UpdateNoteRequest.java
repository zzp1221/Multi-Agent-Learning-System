package com.project.api.note.dto;

import com.fasterxml.jackson.annotation.JsonAlias;
import jakarta.validation.constraints.Size;

import java.util.List;
import java.util.UUID;

public record UpdateNoteRequest(
    @Size(max = 160, message = "笔记标题不能超过 160 字")
    String title,

    @Size(max = 120000, message = "笔记内容不能超过 120000 字")
    @JsonAlias("content")
    String markdownContent,

    UUID folderId,

    Boolean clearFolder,

    List<@Size(max = 32, message = "标签不能超过 32 字") String> tags
) {
}
