package com.project.api.resource.dto;

import java.util.List;

public record ResourceListResponse(
    List<ResourceItemResponse> items,
    long total,
    int page,
    int size
) {
}
