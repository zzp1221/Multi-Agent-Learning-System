package com.project.api.resource.dto;

public record ResourceSemanticHitResponse(
    long chunkId,
    int chunkNo,
    double similarity,
    String content,
    String sourceUrl
) {
}
