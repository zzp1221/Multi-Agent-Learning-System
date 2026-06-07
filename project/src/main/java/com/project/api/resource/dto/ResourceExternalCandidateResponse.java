package com.project.api.resource.dto;

import java.util.List;

public record ResourceExternalCandidateResponse(
    String title,
    String sourceUrl,
    String sourceName,
    String summaryText,
    String resourceType,
    String displayType,
    String difficultyLevel,
    String coverUrl,
    Double qualityScore,
    Double popularityScore,
    List<String> tags
) {
}
