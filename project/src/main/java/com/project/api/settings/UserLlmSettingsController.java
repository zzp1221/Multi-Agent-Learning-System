package com.project.api.settings;

import com.project.api.settings.dto.UserLlmModelListRequest;
import com.project.api.settings.dto.UserLlmModelListResponse;
import com.project.api.settings.dto.UserLlmRuntimeConfigResponse;
import com.project.api.settings.dto.UserLlmSettingsRequest;
import com.project.api.settings.dto.UserLlmSettingsResponse;
import com.project.application.settings.UserLlmSettingsService;
import com.project.security.AuthenticatedUserResolver;
import com.project.security.InternalTokenVerifier;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;
import java.util.UUID;

@RestController
public class UserLlmSettingsController {

    private final UserLlmSettingsService settingsService;
    private final InternalTokenVerifier internalTokenVerifier;

    public UserLlmSettingsController(
        UserLlmSettingsService settingsService,
        InternalTokenVerifier internalTokenVerifier
    ) {
        this.settingsService = settingsService;
        this.internalTokenVerifier = internalTokenVerifier;
    }

    @GetMapping("/api/settings/llm")
    public ResponseEntity<UserLlmSettingsResponse> getSettings(Authentication authentication) {
        return ResponseEntity.ok(settingsService.getSettings(AuthenticatedUserResolver.require(authentication).userId()));
    }

    @PutMapping("/api/settings/llm")
    public ResponseEntity<UserLlmSettingsResponse> saveSettings(
        Authentication authentication,
        @Valid @RequestBody UserLlmSettingsRequest request
    ) {
        return ResponseEntity.ok(settingsService.saveSettings(
            AuthenticatedUserResolver.require(authentication).userId(),
            request
        ));
    }

    @PostMapping("/api/settings/llm/test")
    public ResponseEntity<Map<String, Object>> testSettings(
        Authentication authentication,
        @Valid @RequestBody UserLlmSettingsRequest request
    ) {
        return ResponseEntity.ok(settingsService.testSettings(
            AuthenticatedUserResolver.require(authentication).userId(),
            request
        ));
    }

    @PostMapping("/api/settings/llm/models")
    public ResponseEntity<UserLlmModelListResponse> listModels(
        Authentication authentication,
        @Valid @RequestBody UserLlmModelListRequest request
    ) {
        return ResponseEntity.ok(settingsService.listModels(
            AuthenticatedUserResolver.require(authentication).userId(),
            request
        ));
    }

    @DeleteMapping("/api/settings/llm")
    public ResponseEntity<Map<String, String>> deleteSettings(Authentication authentication) {
        settingsService.deleteSettings(AuthenticatedUserResolver.require(authentication).userId());
        return ResponseEntity.ok(Map.of("status", "deleted"));
    }

    @GetMapping("/internal/users/{userId}/llm-runtime-config")
    public ResponseEntity<UserLlmRuntimeConfigResponse> runtimeConfig(
        @PathVariable UUID userId,
        @RequestHeader(name = InternalTokenVerifier.INTERNAL_TOKEN_HEADER, required = false) String internalToken
    ) {
        internalTokenVerifier.requireValid(internalToken);
        return ResponseEntity.ok(settingsService.runtimeConfig(userId));
    }
}
