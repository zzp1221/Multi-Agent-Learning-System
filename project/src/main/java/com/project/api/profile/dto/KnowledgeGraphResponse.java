package com.project.api.profile.dto;

import java.util.List;

public record KnowledgeGraphResponse(
    List<KnowledgeNodeDto> nodes,
    List<KnowledgeEdgeDto> edges,
    List<String> nextRecommended
) {
    public record KnowledgeNodeDto(
        String key,
        String topic,
        double mastery,
        String status,
        String source
    ) {}

    public record KnowledgeEdgeDto(
        String from,
        String to,
        String type,
        double weight
    ) {}
}
