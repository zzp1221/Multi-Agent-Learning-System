package com.project.api.settings.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.util.Map;

public record UserLlmSettingsRequest(
    boolean enabled,
    @Size(max = 48) String activeProvider,
    @Size(max = 48) String fallbackProvider,
    @NotNull Map<@Size(max = 48) String, @Valid UserLlmProviderConfigDto> providers,
    @NotNull Map<@Size(max = 64) String, @Valid UserLlmComponentOverrideDto> componentOverrides,
    Map<@Size(max = 80) String, @Valid UserLlmSkillOverrideDto> skillOverrides
) {
}
