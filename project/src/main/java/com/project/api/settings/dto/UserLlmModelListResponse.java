package com.project.api.settings.dto;

import java.util.List;

public record UserLlmModelListResponse(
    String provider,
    String baseUrl,
    List<String> models
) {
}
