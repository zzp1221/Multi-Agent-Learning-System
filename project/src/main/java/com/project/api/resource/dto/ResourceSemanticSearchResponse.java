package com.project.api.resource.dto;

import java.util.List;

public record ResourceSemanticSearchResponse(
    String query,
    boolean available,
    String message,
    List<ResourceSemanticResultResponse> results
) {
}
