package com.project.application.resource;

import com.project.api.resource.dto.ResourceSemanticSearchResponse;

import java.util.UUID;

public interface ResourceSemanticSearchClient {

    ResourceSemanticSearchResponse search(UUID userId, String query, int topK);
}
