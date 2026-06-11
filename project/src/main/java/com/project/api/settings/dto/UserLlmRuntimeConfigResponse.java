package com.project.api.settings.dto;

import java.util.Map;

public record UserLlmRuntimeConfigResponse(
    boolean enabled,
    boolean allowEnvironmentFallback,
    String activeProvider,
    String fallbackProvider,
    Map<String, RuntimeProviderConfig> providers,
    Map<String, RuntimeComponentOverride> componentOverrides,
    Map<String, RuntimeSkillOverride> skillOverrides
) {
    public record RuntimeProviderConfig(
        String provider,
        String baseUrl,
        String apiKey,
        String apiSecret,
        String appId,
        Map<String, String> modelOverrides
    ) {
    }

    public record RuntimeComponentOverride(
        String provider,
        String model
    ) {
    }

    public record RuntimeSkillOverride(
        boolean enabled,
        String name,
        String description,
        String body
    ) {
    }
}
