package com.project.api.resource.dto;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;

public record ResourceItemResponse(
    UUID id,
    String title,
    String domain,
    String resourceType,
    String displayType,
    String difficultyLevel,
    String sourceKind,
    String summaryText,
    List<String> tags,
    String sourceUrl,
    String sourceName,
    String coverUrl,
    String license,
    String copyrightStatus,
    String accessibilityStatus,
    Integer httpStatus,
    OffsetDateTime lastCheckedAt,
    Double qualityScore,
    Double popularityScore,
    Long favoriteCount,
    Long viewCount,
    Long likeCount,
    Integer durationMinutes,
    Long fileSizeBytes,
    Boolean favorite,
    Integer progress,
    Boolean completed,
    OffsetDateTime lastStudyAt,
    OffsetDateTime createdAt,
    OffsetDateTime updatedAt,
    String csCategory,
    String csSubcategory,
    Map<String, Object> metadata
) {
}
