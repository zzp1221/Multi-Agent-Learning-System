package com.project.api.settings.dto;

import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.util.Map;

public record UserLlmSettingsRequest(
    boolean enabled,
    @Size(max = 48) String activeProvider,
    @Size(max = 48) String fallbackProvider,
    @NotNull Map<String, UserLlmProviderConfigDto> providers,
    @NotNull Map<String, UserLlmComponentOverrideDto> componentOverrides,
    Map<String, UserLlmSkillOverrideDto> skillOverrides
) {
}
