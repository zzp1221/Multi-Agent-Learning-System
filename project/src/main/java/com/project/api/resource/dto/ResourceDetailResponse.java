package com.project.api.resource.dto;

import java.util.List;

public record ResourceDetailResponse(
    ResourceItemResponse resource,
    boolean ragReady,
    int chunkCount,
    List<String> previewChunks
) {
}
