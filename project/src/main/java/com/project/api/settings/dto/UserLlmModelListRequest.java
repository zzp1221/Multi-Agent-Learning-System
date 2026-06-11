package com.project.api.settings.dto;

import jakarta.validation.constraints.Size;

public record UserLlmModelListRequest(
    @Size(max = 48) String provider,
    @Size(max = 512) String baseUrl,
    @Size(max = 2048) String apiKey,
    @Size(max = 2048) String apiSecret,
    @Size(max = 256) String appId
) {
}
