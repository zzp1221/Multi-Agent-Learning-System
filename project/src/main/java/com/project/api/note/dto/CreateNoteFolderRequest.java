package com.project.api.note.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

import java.util.UUID;

public record CreateNoteFolderRequest(
    @NotBlank(message = "目录名称不能为空")
    @Size(max = 80, message = "目录名称不能超过 80 字")
    String name,

    UUID parentId,

    Integer sortOrder
) {
}
