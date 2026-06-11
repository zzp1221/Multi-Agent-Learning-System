package com.project.api.settings.dto;

import java.util.Map;

public record UserLlmProviderViewDto(
    String provider,
    String baseUrl,
    boolean hasApiKey,
    boolean hasApiSecret,
    boolean hasAppId,
    Map<String, String> modelOverrides
) {
}
