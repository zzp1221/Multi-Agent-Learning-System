package com.project.api.resource.dto;

import java.util.List;
import java.util.UUID;

public record ResourceSemanticResultResponse(
    UUID resourceId,
    ResourceItemResponse resource,
    double score,
    String reason,
    List<ResourceSemanticHitResponse> hits
) {
}
