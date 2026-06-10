package com.project.api.study.dto;

import com.project.api.mistake.dto.MistakeRecordResponse;
import com.project.api.profile.dto.KnowledgeGraphResponse.KnowledgeEdgeDto;
import com.project.api.profile.dto.KnowledgeGraphResponse.KnowledgeNodeDto;
import com.project.api.resource.dto.ResourceItemResponse;

import java.util.List;
import java.util.Map;
import java.util.UUID;

public record KnowledgeNodeDetailResponse(
    UUID userId,
    KnowledgeNodeDto node,
    List<KnowledgeNodeDto> prerequisites,
    List<KnowledgeNodeDto> nextNodes,
    List<KnowledgeNodeDto> relatedNodes,
    List<KnowledgeEdgeDto> edges,
    List<MistakeRecordResponse> relatedMistakes,
    List<ResourceItemResponse> relatedResources,
    List<String> recommendedNextActions,
    Map<String, Object> practiceContext
) {
}
