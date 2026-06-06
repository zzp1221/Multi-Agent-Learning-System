package com.project.api.resource.dto;

import java.util.List;
import java.util.Map;

public record ResourceStatsResponse(
    long totalResources,
    long favoriteResources,
    long startedResources,
    long completedResources,
    double averageProgress,
    Map<String, Long> typeCounts,
    Map<String, Long> categoryCounts,
    Map<String, Long> subcategoryCounts,
    List<ResourceTagResponse> hotTags
) {
}
