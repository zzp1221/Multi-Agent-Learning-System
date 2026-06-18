package com.project.api.profile.dto;

import java.util.List;

public record KnowledgeGraphResponse(
    List<KnowledgeNodeDto> nodes,
    List<KnowledgeEdgeDto> edges,
    List<String> nextRecommended,
    KnowledgeGraphMetadata metadata
) {
    public KnowledgeGraphResponse(
        List<KnowledgeNodeDto> nodes,
        List<KnowledgeEdgeDto> edges,
        List<String> nextRecommended
    ) {
        this(nodes, edges, nextRecommended, KnowledgeGraphMetadata.empty());
    }

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

    public record KnowledgeGraphMetadata(
        String rootKey,
        int visibleNodeLimit,
        boolean sparseState,
        int orphanNodeCount,
        CurationStats curationStats,
        List<EdgeExplanation> edgeExplanations
    ) {
        public static KnowledgeGraphMetadata empty() {
            return new KnowledgeGraphMetadata(
                "",
                0,
                false,
                0,
                new CurationStats(0, 0, 0),
                List.of()
            );
        }
    }

    public record CurationStats(
        int filteredNodeCount,
        int lowConfidenceEdgeCount,
        int suspiciousEdgeCount
    ) {}

    public record EdgeExplanation(
        String type,
        String label,
        String description
    ) {}
}
