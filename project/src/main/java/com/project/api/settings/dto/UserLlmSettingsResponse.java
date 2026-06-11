package com.project.api.settings.dto;

import java.util.List;
import java.util.Map;

public record UserLlmSettingsResponse(
    boolean enabled,
    String activeProvider,
    String fallbackProvider,
    List<ProviderCapabilityDto> providerCapabilities,
    Map<String, UserLlmProviderViewDto> providers,
    Map<String, UserLlmComponentViewDto> componentOverrides,
    Map<String, UserLlmSkillViewDto> skillOverrides
) {
}
