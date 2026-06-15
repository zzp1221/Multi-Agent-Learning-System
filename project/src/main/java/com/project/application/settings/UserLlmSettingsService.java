package com.project.application.settings;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.settings.dto.ProviderCapabilityDto;
import com.project.api.settings.dto.UserLlmComponentOverrideDto;
import com.project.api.settings.dto.UserLlmComponentViewDto;
import com.project.api.settings.dto.UserLlmModelListRequest;
import com.project.api.settings.dto.UserLlmModelListResponse;
import com.project.api.settings.dto.UserLlmProviderConfigDto;
import com.project.api.settings.dto.UserLlmProviderViewDto;
import com.project.api.settings.dto.UserLlmRuntimeConfigResponse;
import com.project.api.settings.dto.UserLlmSettingsRequest;
import com.project.api.settings.dto.UserLlmSettingsResponse;
import com.project.api.settings.dto.UserLlmSkillOverrideDto;
import com.project.api.settings.dto.UserLlmSkillViewDto;
import com.project.application.common.ApplicationException;
import com.project.config.AppProperties;
import com.project.domain.settings.UserLlmSettingsRepository;
import com.project.domain.user.UserAccountRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.UUID;

@Service
public class UserLlmSettingsService {

    private static final TypeReference<Map<String, Object>> OBJECT_MAP = new TypeReference<>() {
    };
    private static final TypeReference<Map<String, UserLlmProviderStoredConfig>> PROVIDER_MAP = new TypeReference<>() {
    };
    private static final TypeReference<Map<String, UserLlmComponentOverrideDto>> COMPONENT_MAP = new TypeReference<>() {
    };
    private static final TypeReference<OverrideBundle> OVERRIDE_BUNDLE = new TypeReference<>() {
    };
    private static final int SKILL_BODY_MAX_CHARS = 8000;
    private static final Set<String> ALLOWED_COMPONENT_KEYS = Set.of(
        "query_rewrite_llm",
        "retrieval_llm",
        "generation_llm",
        "practice_llm",
        "judge_llm",
        "profile_llm",
        "tutor_llm",
        "conversation_summary_llm",
        "planning_llm",
        "review_llm",
        "safety_llm",
        "evaluation_llm",
        "path_planning_llm",
        "resource_push_llm"
    );
    private static final Set<String> ALLOWED_SKILL_KEYS = Set.of(
        "ability:rewrite_tutor",
        "ability:generation",
        "ability:assessment",
        "ability:path",
        "query_rewrite_llm",
        "retrieval_llm",
        "generation_llm",
        "practice_llm",
        "judge_llm",
        "profile_llm",
        "tutor_llm",
        "conversation_summary_llm",
        "planning_llm",
        "review_llm",
        "safety_llm",
        "evaluation_llm",
        "path_planning_llm",
        "resource_push_llm"
    );
    private static final Map<String, String> PROVIDER_DEFAULT_BASE_URLS = Map.of(
        "openai", "https://api.openai.com/v1",
        "dashscope", "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "deepseek", "https://api.deepseek.com",
        "moonshot", "https://api.moonshot.cn/v1",
        "zhipu", "https://open.bigmodel.cn/api/paas/v4",
        "spark", "https://spark-api-open.xf-yun.com/v1",
        "mimo", "https://api.xiaomimimo.com/v1",
        "custom_openai_compatible", ""
    );
    private static final List<ProviderCapabilityDto> PROVIDER_CAPABILITIES = List.of(
        new ProviderCapabilityDto("openai", "OpenAI", "Chat Completions / Structured Outputs"),
        new ProviderCapabilityDto("dashscope", "DashScope", "OpenAI-compatible endpoint"),
        new ProviderCapabilityDto("deepseek", "DeepSeek", "OpenAI-compatible chat completions"),
        new ProviderCapabilityDto("moonshot", "Moonshot", "OpenAI-compatible chat completions"),
        new ProviderCapabilityDto("zhipu", "Zhipu GLM", "OpenAI-compatible chat completions"),
        new ProviderCapabilityDto("spark", "iFlytek Spark", "Spark OpenAI-compatible adapter"),
        new ProviderCapabilityDto("mimo", "MiMo", "MiMo /chat/completions adapter"),
        new ProviderCapabilityDto("custom_openai_compatible", "Custom compatible endpoint", "Custom baseUrl + model names")
    );

    private final UserLlmSettingsRepository repository;
    private final UserLlmSecretCryptoService cryptoService;
    private final ObjectMapper objectMapper;
    private final AppProperties appProperties;
    private final HttpClient httpClient;
    private final UserAccountRepository userAccountRepository;

    public UserLlmSettingsService(
        UserLlmSettingsRepository repository,
        UserLlmSecretCryptoService cryptoService,
        ObjectMapper objectMapper,
        AppProperties appProperties,
        UserAccountRepository userAccountRepository
    ) {
        this.repository = repository;
        this.cryptoService = cryptoService;
        this.objectMapper = objectMapper;
        this.appProperties = appProperties;
        this.userAccountRepository = userAccountRepository;
        this.httpClient = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(6))
            .build();
    }

    @Transactional(readOnly = true)
    public UserLlmSettingsResponse getSettings(UUID userId) {
        return repository.findByUserId(userId)
            .map(this::toView)
            .orElseGet(this::defaultView);
    }

    @Transactional
    public UserLlmSettingsResponse saveSettings(UUID userId, UserLlmSettingsRequest request) {
        ensureFeatureEnabled();
        UserLlmSettingsRepository.UserLlmSettingsRecord existing = repository.findByUserId(userId).orElse(null);
        Map<String, UserLlmProviderStoredConfig> existingProviders = existing == null
            ? Map.of()
            : readJson(existing.providerConfigJson(), PROVIDER_MAP, Map.of());
        Map<String, Object> existingSecrets = existing == null
            ? Map.of()
            : readJson(existing.encryptedSecretsJson(), OBJECT_MAP, Map.of());

        Map<String, UserLlmProviderConfigDto> requestProviders = request.providers() == null ? Map.of() : request.providers();
        Map<String, UserLlmProviderStoredConfig> storedProviders = normalizeProviders(requestProviders);
        Map<String, Object> encryptedSecrets = mergeEncryptedSecrets(storedProviders, requestProviders, existingProviders, existingSecrets);
        Map<String, Object> secretMeta = buildSecretMeta(storedProviders, encryptedSecrets);
        Map<String, UserLlmComponentOverrideDto> componentOverrides = normalizeComponentOverrides(request.componentOverrides());
        Map<String, UserLlmSkillOverrideDto> skillOverrides = normalizeSkillOverrides(request.skillOverrides());

        String activeProvider = normalizeProviderKey(request.activeProvider());
        if (activeProvider.isBlank() && !storedProviders.isEmpty()) {
            activeProvider = storedProviders.keySet().iterator().next();
        }
        if (!activeProvider.isBlank() && !storedProviders.containsKey(activeProvider)) {
            throw new ApplicationException("LLM_PROVIDER_NOT_CONFIGURED", "Active LLM provider is not configured", HttpStatus.BAD_REQUEST);
        }
        String fallbackProvider = normalizeProviderKey(request.fallbackProvider());
        if (!fallbackProvider.isBlank() && !storedProviders.containsKey(fallbackProvider)) {
            throw new ApplicationException("LLM_FALLBACK_PROVIDER_NOT_CONFIGURED", "Fallback LLM provider is not configured", HttpStatus.BAD_REQUEST);
        }

        repository.upsert(new UserLlmSettingsRepository.UserLlmSettingsRecord(
            userId,
            true,
            activeProvider,
            fallbackProvider,
            writeJson(storedProviders),
            writeJson(new OverrideBundle(componentOverrides, skillOverrides)),
            writeJson(encryptedSecrets),
            writeJson(secretMeta)
        ));
        return getSettings(userId);
    }

    @Transactional
    public void deleteSettings(UUID userId) {
        repository.deleteByUserId(userId);
    }

    @Transactional(readOnly = true)
    public UserLlmRuntimeConfigResponse runtimeConfig(UUID userId) {
        if (!appProperties.getUserLlm().isEnabled()) {
            return emptyRuntimeConfig(allowEnvironmentFallback(userId));
        }
        return repository.findByUserId(userId)
            .map(this::toRuntime)
            .orElseGet(() -> emptyRuntimeConfig(allowEnvironmentFallback(userId)));
    }

    @Transactional(readOnly = true)
    public boolean isUserLlmReadyOrAllowedFallback(UUID userId) {
        if (allowEnvironmentFallback(userId)) {
            return true;
        }
        if (!appProperties.getUserLlm().isEnabled()) {
            return false;
        }
        return repository.findByUserId(userId)
            .map(this::hasReadyActiveProvider)
            .orElse(false);
    }

    public Map<String, Object> testSettings(UUID userId, UserLlmSettingsRequest request) {
        ensureFeatureEnabled();
        String provider = resolveTestProvider(request);
        List<String> models = verifyProviderConnection(userId, provider, request);
        UserLlmSettingsResponse saved = saveSettings(userId, request);
        return Map.of(
            "ok", true,
            "activeProvider", saved.activeProvider(),
            "message", "User LLM settings saved and verified",
            "models", models
        );
    }

    @Transactional(readOnly = true)
    public UserLlmModelListResponse listModels(UUID userId, UserLlmModelListRequest request) {
        ensureFeatureEnabled();
        String provider = normalizeProviderKey(request.provider());
        if (provider.isBlank()) {
            throw new ApplicationException("LLM_PROVIDER_REQUIRED", "请选择厂商后再拉取模型", HttpStatus.BAD_REQUEST);
        }

        UserLlmSettingsRepository.UserLlmSettingsRecord existing = repository.findByUserId(userId).orElse(null);
        Map<String, UserLlmProviderStoredConfig> existingProviders = existing == null
            ? Map.of()
            : readJson(existing.providerConfigJson(), PROVIDER_MAP, Map.of());
        Map<String, Object> existingSecrets = existing == null
            ? Map.of()
            : readJson(existing.encryptedSecretsJson(), OBJECT_MAP, Map.of());
        UserLlmProviderStoredConfig storedProvider = existingProviders.get(provider);

        String baseUrl = firstNonBlank(request.baseUrl(), storedProvider == null ? "" : storedProvider.baseUrl());
        baseUrl = firstNonBlank(baseUrl, PROVIDER_DEFAULT_BASE_URLS.getOrDefault(provider, ""));
        if (baseUrl.isBlank()) {
            throw new ApplicationException("LLM_BASE_URL_REQUIRED", "请先填写厂商官网兼容 Base URL", HttpStatus.BAD_REQUEST);
        }

        Map<String, Object> providerSecrets = readObjectMap(existingSecrets.get(provider));
        String apiKey = firstNonBlank(request.apiKey(), decrypt(providerSecrets.get("apiKey")));
        if (apiKey.isBlank()) {
            throw new ApplicationException("LLM_API_KEY_REQUIRED", "请先输入或保存该厂商 API Key", HttpStatus.BAD_REQUEST);
        }

        List<String> models = fetchProviderModels(baseUrl, apiKey);
        return new UserLlmModelListResponse(provider, trimTrailingSlash(baseUrl), models);
    }

    private String resolveTestProvider(UserLlmSettingsRequest request) {
        String provider = normalizeProviderKey(request.activeProvider());
        if (!provider.isBlank()) {
            return provider;
        }
        Map<String, UserLlmProviderConfigDto> providers = request.providers() == null ? Map.of() : request.providers();
        for (Map.Entry<String, UserLlmProviderConfigDto> entry : providers.entrySet()) {
            String candidate = normalizeProviderKey(entry.getValue() == null
                ? entry.getKey()
                : firstNonBlank(entry.getValue().provider(), entry.getKey()));
            if (!candidate.isBlank()) {
                return candidate;
            }
        }
        throw new ApplicationException("LLM_PROVIDER_REQUIRED", "请选择厂商后再测试连接", HttpStatus.BAD_REQUEST);
    }

    private List<String> verifyProviderConnection(UUID userId, String provider, UserLlmSettingsRequest request) {
        UserLlmSettingsRepository.UserLlmSettingsRecord existing = repository.findByUserId(userId).orElse(null);
        Map<String, UserLlmProviderStoredConfig> existingProviders = existing == null
            ? Map.of()
            : readJson(existing.providerConfigJson(), PROVIDER_MAP, Map.of());
        Map<String, Object> existingSecrets = existing == null
            ? Map.of()
            : readJson(existing.encryptedSecretsJson(), OBJECT_MAP, Map.of());
        UserLlmProviderStoredConfig existingProvider = existingProviders.get(provider);
        UserLlmProviderConfigDto requestProvider = findRequestProvider(
            request.providers() == null ? Map.of() : request.providers(),
            provider
        );

        String baseUrl = firstNonBlank(requestProvider == null ? "" : requestProvider.baseUrl(), existingProvider == null ? "" : existingProvider.baseUrl());
        baseUrl = firstNonBlank(baseUrl, PROVIDER_DEFAULT_BASE_URLS.getOrDefault(provider, ""));
        if (baseUrl.isBlank()) {
            throw new ApplicationException("LLM_BASE_URL_REQUIRED", "请先填写厂商官网兼容 Base URL", HttpStatus.BAD_REQUEST);
        }

        Map<String, Object> providerSecrets = readObjectMap(existingSecrets.get(provider));
        String apiKey = firstNonBlank(requestProvider == null ? "" : requestProvider.apiKey(), decrypt(providerSecrets.get("apiKey")));
        if (apiKey.isBlank()) {
            throw new ApplicationException("LLM_API_KEY_REQUIRED", "请先输入或保存该厂商 API Key", HttpStatus.BAD_REQUEST);
        }

        return fetchProviderModels(baseUrl, apiKey);
    }

    private List<String> fetchProviderModels(String baseUrl, String apiKey) {
        URI modelsEndpoint = modelsEndpoint(baseUrl);
        HttpRequest httpRequest = HttpRequest.newBuilder(modelsEndpoint)
            .timeout(Duration.ofSeconds(12))
            .header("Accept", "application/json")
            .header("Authorization", "Bearer " + apiKey)
            .GET()
            .build();
        try {
            HttpResponse<String> response = httpClient.send(httpRequest, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() == 401 || response.statusCode() == 403) {
                throw new ApplicationException("LLM_MODEL_LIST_UNAUTHORIZED", "API Key 无权拉取模型列表", HttpStatus.BAD_REQUEST);
            }
            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                throw new ApplicationException("LLM_MODEL_LIST_FAILED", "厂商模型列表接口暂不可用", HttpStatus.BAD_GATEWAY);
            }
            List<String> models = extractModelIds(response.body());
            if (models.isEmpty()) {
                throw new ApplicationException("LLM_MODEL_LIST_EMPTY", "厂商未返回可用模型", HttpStatus.BAD_GATEWAY);
            }
            return models;
        } catch (ApplicationException ex) {
            throw ex;
        } catch (IOException ex) {
            throw new ApplicationException("LLM_MODEL_LIST_IO_FAILED", "拉取模型列表失败，请检查 Base URL", HttpStatus.BAD_GATEWAY);
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            throw new ApplicationException("LLM_MODEL_LIST_INTERRUPTED", "拉取模型列表已中断", HttpStatus.BAD_GATEWAY);
        } catch (IllegalArgumentException ex) {
            throw new ApplicationException("LLM_BASE_URL_INVALID", "Base URL 格式无效", HttpStatus.BAD_REQUEST);
        }
    }

    private UserLlmSettingsResponse toView(UserLlmSettingsRepository.UserLlmSettingsRecord record) {
        Map<String, UserLlmProviderStoredConfig> storedProviders = readJson(record.providerConfigJson(), PROVIDER_MAP, Map.of());
        Map<String, Object> encryptedSecrets = readJson(record.encryptedSecretsJson(), OBJECT_MAP, Map.of());
        Map<String, UserLlmProviderViewDto> providerViews = new LinkedHashMap<>();
        for (Map.Entry<String, UserLlmProviderStoredConfig> entry : storedProviders.entrySet()) {
            Map<String, Object> providerSecrets = readObjectMap(encryptedSecrets.get(entry.getKey()));
            UserLlmProviderStoredConfig stored = entry.getValue();
            providerViews.put(entry.getKey(), new UserLlmProviderViewDto(
                entry.getKey(),
                stored.baseUrl(),
                hasText(providerSecrets.get("apiKey")),
                hasText(providerSecrets.get("apiSecret")),
                hasText(providerSecrets.get("appId")),
                stored.modelOverrides()
            ));
        }
        OverrideBundle overrideBundle = readOverrideBundle(record.componentOverridesJson());
        Map<String, UserLlmComponentViewDto> componentViews = new LinkedHashMap<>();
        overrideBundle.components().forEach((key, value) -> componentViews.put(
            key,
            new UserLlmComponentViewDto(normalizeProviderKey(value.provider()), trim(value.model()))
        ));
        Map<String, UserLlmSkillViewDto> skillViews = new LinkedHashMap<>();
        overrideBundle.skills().forEach((key, value) -> skillViews.put(
            key,
            new UserLlmSkillViewDto(value.enabled(), trim(value.name()), trim(value.description()), trim(value.body()))
        ));
        return new UserLlmSettingsResponse(
            true,
            trim(record.activeProvider()),
            trim(record.fallbackProvider()),
            PROVIDER_CAPABILITIES,
            providerViews,
            componentViews,
            skillViews
        );
    }

    private UserLlmRuntimeConfigResponse toRuntime(UserLlmSettingsRepository.UserLlmSettingsRecord record) {
        Map<String, UserLlmProviderStoredConfig> storedProviders = readJson(record.providerConfigJson(), PROVIDER_MAP, Map.of());
        Map<String, Object> encryptedSecrets = readJson(record.encryptedSecretsJson(), OBJECT_MAP, Map.of());
        Map<String, UserLlmRuntimeConfigResponse.RuntimeProviderConfig> providers = new LinkedHashMap<>();
        for (Map.Entry<String, UserLlmProviderStoredConfig> entry : storedProviders.entrySet()) {
            Map<String, Object> providerSecrets = readObjectMap(encryptedSecrets.get(entry.getKey()));
            UserLlmProviderStoredConfig stored = entry.getValue();
            providers.put(entry.getKey(), new UserLlmRuntimeConfigResponse.RuntimeProviderConfig(
                entry.getKey(),
                stored.baseUrl(),
                decrypt(providerSecrets.get("apiKey")),
                decrypt(providerSecrets.get("apiSecret")),
                decrypt(providerSecrets.get("appId")),
                stored.modelOverrides()
            ));
        }
        OverrideBundle overrideBundle = readOverrideBundle(record.componentOverridesJson());
        Map<String, UserLlmRuntimeConfigResponse.RuntimeComponentOverride> runtimeOverrides = new LinkedHashMap<>();
        overrideBundle.components().forEach((key, value) -> runtimeOverrides.put(
            key,
            new UserLlmRuntimeConfigResponse.RuntimeComponentOverride(
                normalizeProviderKey(value.provider()),
                trim(value.model())
            )
        ));
        Map<String, UserLlmRuntimeConfigResponse.RuntimeSkillOverride> runtimeSkills = new LinkedHashMap<>();
        overrideBundle.skills().forEach((key, value) -> runtimeSkills.put(
            key,
            new UserLlmRuntimeConfigResponse.RuntimeSkillOverride(
                value.enabled(),
                trim(value.name()),
                trim(value.description()),
                trim(value.body())
            )
        ));
        return new UserLlmRuntimeConfigResponse(
            true,
            false,
            trim(record.activeProvider()),
            trim(record.fallbackProvider()),
            providers,
            runtimeOverrides,
            runtimeSkills
        );
    }

    private UserLlmSettingsResponse defaultView() {
        return new UserLlmSettingsResponse(true, "", "", PROVIDER_CAPABILITIES, Map.of(), Map.of(), Map.of());
    }

    private UserLlmRuntimeConfigResponse emptyRuntimeConfig(boolean allowEnvironmentFallback) {
        return new UserLlmRuntimeConfigResponse(false, allowEnvironmentFallback, "", "", Map.of(), Map.of(), Map.of());
    }

    private boolean hasReadyActiveProvider(UserLlmSettingsRepository.UserLlmSettingsRecord record) {
        String activeProvider = trim(record.activeProvider());
        if (activeProvider.isBlank()) {
            return false;
        }
        Map<String, UserLlmProviderStoredConfig> storedProviders = readJson(record.providerConfigJson(), PROVIDER_MAP, Map.of());
        if (!storedProviders.containsKey(activeProvider)) {
            return false;
        }
        Map<String, Object> encryptedSecrets = readJson(record.encryptedSecretsJson(), OBJECT_MAP, Map.of());
        Map<String, Object> providerSecrets = readObjectMap(encryptedSecrets.get(activeProvider));
        return hasText(providerSecrets.get("apiKey"));
    }

    private boolean allowEnvironmentFallback(UUID userId) {
        return userAccountRepository.findById(userId)
            .map(user -> isTestLoginId(user.getLoginId()))
            .orElse(false);
    }

    private boolean isTestLoginId(String loginId) {
        String normalized = trim(loginId).toLowerCase(Locale.ROOT);
        return normalized.startsWith("testuser_")
            || normalized.startsWith("test_chain_")
            || normalized.startsWith("ctest_")
            || normalized.startsWith("score_reg_");
    }

    private Map<String, UserLlmProviderStoredConfig> normalizeProviders(Map<String, UserLlmProviderConfigDto> providers) {
        Map<String, UserLlmProviderStoredConfig> normalized = new LinkedHashMap<>();
        if (providers == null) {
            return normalized;
        }
        providers.forEach((rawKey, config) -> {
            String providerKey = normalizeProviderKey(config == null ? rawKey : firstNonBlank(config.provider(), rawKey));
            if (providerKey.isBlank() || config == null) {
                return;
            }
            normalized.put(providerKey, new UserLlmProviderStoredConfig(
                trim(config.baseUrl()),
                normalizeModelOverrides(config.modelOverrides())
            ));
        });
        return normalized;
    }

    private Map<String, UserLlmComponentOverrideDto> normalizeComponentOverrides(Map<String, UserLlmComponentOverrideDto> overrides) {
        Map<String, UserLlmComponentOverrideDto> normalized = new LinkedHashMap<>();
        if (overrides == null) {
            return normalized;
        }
        overrides.forEach((rawComponent, override) -> {
            String component = trim(rawComponent);
            if (component.isBlank() || override == null) {
                return;
            }
            if (!ALLOWED_COMPONENT_KEYS.contains(component)) {
                throw new ApplicationException("LLM_COMPONENT_OVERRIDE_INVALID", "Unsupported LLM component override", HttpStatus.BAD_REQUEST);
            }
            normalized.put(component, new UserLlmComponentOverrideDto(
                normalizeProviderKey(override.provider()),
                trim(override.model())
            ));
        });
        return normalized;
    }

    private Map<String, UserLlmSkillOverrideDto> normalizeSkillOverrides(Map<String, UserLlmSkillOverrideDto> overrides) {
        Map<String, UserLlmSkillOverrideDto> normalized = new LinkedHashMap<>();
        if (overrides == null) {
            return normalized;
        }
        overrides.forEach((rawKey, override) -> {
            String key = trim(rawKey);
            if (key.isBlank() || override == null) {
                return;
            }
            if (!ALLOWED_SKILL_KEYS.contains(key)) {
                throw new ApplicationException("LLM_SKILL_OVERRIDE_INVALID", "Unsupported user skill target", HttpStatus.BAD_REQUEST);
            }
            String name = trim(override.name());
            String description = trim(override.description());
            String body = normalizeSkillBody(override.body());
            if (override.enabled() && body.isBlank()) {
                throw new ApplicationException("LLM_SKILL_BODY_REQUIRED", "Enabled user skill must include body", HttpStatus.BAD_REQUEST);
            }
            if (body.length() > SKILL_BODY_MAX_CHARS) {
                throw new ApplicationException("LLM_SKILL_BODY_TOO_LONG", "User skill body is too long", HttpStatus.BAD_REQUEST);
            }
            if (override.enabled() || !name.isBlank() || !description.isBlank() || !body.isBlank()) {
                normalized.put(key, new UserLlmSkillOverrideDto(override.enabled(), name, description, body));
            }
        });
        return normalized;
    }

    private String normalizeSkillBody(String value) {
        String body = trim(value).replace("\r\n", "\n").replace('\r', '\n');
        if (body.startsWith("---")) {
            int frontmatterEnd = body.indexOf("\n---", 3);
            if (frontmatterEnd >= 0) {
                body = body.substring(frontmatterEnd + 4).trim();
            }
        }
        return body;
    }

    private OverrideBundle readOverrideBundle(String value) {
        String normalized = value == null ? "" : value.trim();
        if (normalized.isEmpty()) {
            return new OverrideBundle(Map.of(), Map.of());
        }
        try {
            JsonNode root = objectMapper.readTree(normalized);
            if (root != null && root.isObject() && (root.has("components") || root.has("skills"))) {
                OverrideBundle bundle = objectMapper.readValue(normalized, OVERRIDE_BUNDLE);
                return new OverrideBundle(
                    normalizeComponentOverrides(bundle.components()),
                    normalizeSkillOverrides(bundle.skills())
                );
            }
            Map<String, UserLlmComponentOverrideDto> legacy = objectMapper.readValue(normalized, COMPONENT_MAP);
            return new OverrideBundle(normalizeComponentOverrides(legacy), Map.of());
        } catch (JsonProcessingException ex) {
            throw new ApplicationException("LLM_CONFIG_JSON_INVALID", "User LLM config JSON is invalid", HttpStatus.INTERNAL_SERVER_ERROR);
        }
    }

    private Map<String, Object> mergeEncryptedSecrets(
        Map<String, UserLlmProviderStoredConfig> storedProviders,
        Map<String, UserLlmProviderConfigDto> requestProviders,
        Map<String, UserLlmProviderStoredConfig> existingProviders,
        Map<String, Object> existingSecrets
    ) {
        Map<String, Object> result = new LinkedHashMap<>();
        for (String provider : storedProviders.keySet()) {
            Map<String, Object> existingProviderSecrets = readObjectMap(existingSecrets.get(provider));
            Map<String, Object> providerSecrets = new LinkedHashMap<>();
            UserLlmProviderConfigDto requestProvider = findRequestProvider(requestProviders, provider);
            putSecret(providerSecrets, "apiKey", requestProvider == null ? "" : requestProvider.apiKey(), existingProviderSecrets);
            putSecret(providerSecrets, "apiSecret", requestProvider == null ? "" : requestProvider.apiSecret(), existingProviderSecrets);
            putSecret(providerSecrets, "appId", requestProvider == null ? "" : requestProvider.appId(), existingProviderSecrets);
            result.put(provider, providerSecrets);
        }
        return result;
    }

    private UserLlmProviderConfigDto findRequestProvider(Map<String, UserLlmProviderConfigDto> providers, String providerKey) {
        for (Map.Entry<String, UserLlmProviderConfigDto> entry : providers.entrySet()) {
            UserLlmProviderConfigDto config = entry.getValue();
            String candidate = normalizeProviderKey(config == null ? entry.getKey() : firstNonBlank(config.provider(), entry.getKey()));
            if (providerKey.equals(candidate)) {
                return config;
            }
        }
        return null;
    }

    private Map<String, Object> buildSecretMeta(
        Map<String, UserLlmProviderStoredConfig> storedProviders,
        Map<String, Object> encryptedSecrets
    ) {
        Map<String, Object> meta = new LinkedHashMap<>();
        for (String provider : storedProviders.keySet()) {
            Map<String, Object> providerSecrets = readObjectMap(encryptedSecrets.get(provider));
            meta.put(provider, Map.of(
                "hasApiKey", hasText(providerSecrets.get("apiKey")),
                "hasApiSecret", hasText(providerSecrets.get("apiSecret")),
                "hasAppId", hasText(providerSecrets.get("appId"))
            ));
        }
        return meta;
    }

    private void putSecret(Map<String, Object> target, String name, String rawValue, Map<String, Object> existingProviderSecrets) {
        if (hasText(rawValue)) {
            target.put(name, cryptoService.encrypt(rawValue));
            return;
        }
        Object existing = existingProviderSecrets.get(name);
        if (hasText(existing)) {
            target.put(name, String.valueOf(existing));
        }
    }

    private Map<String, String> normalizeModelOverrides(Map<String, String> modelOverrides) {
        Map<String, String> normalized = new LinkedHashMap<>();
        if (modelOverrides == null) {
            return normalized;
        }
        modelOverrides.forEach((key, value) -> {
            String normalizedKey = trim(key);
            String normalizedValue = trim(value);
            if (!normalizedKey.isBlank() && !normalizedValue.isBlank()) {
                normalized.put(normalizedKey, normalizedValue);
            }
        });
        return normalized;
    }

    private String normalizeProviderKey(String value) {
        String normalized = trim(value).toLowerCase(Locale.ROOT);
        return switch (normalized) {
            case "bailian", "aliyun", "dashscope_bailian" -> "dashscope";
            case "openai_compatible", "custom" -> "custom_openai_compatible";
            case "glm", "bigmodel" -> "zhipu";
            default -> normalized;
        };
    }

    private String decrypt(Object value) {
        String encrypted = value == null ? "" : String.valueOf(value);
        if (encrypted.isBlank()) {
            return "";
        }
        return cryptoService.decrypt(encrypted);
    }

    private <T> T readJson(String value, TypeReference<T> type, T fallback) {
        String normalized = value == null ? "" : value.trim();
        if (normalized.isEmpty()) {
            return fallback;
        }
        try {
            return objectMapper.readValue(normalized, type);
        } catch (JsonProcessingException ex) {
            throw new ApplicationException("LLM_CONFIG_JSON_INVALID", "User LLM config JSON is invalid", HttpStatus.INTERNAL_SERVER_ERROR);
        }
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value == null ? Map.of() : value);
        } catch (JsonProcessingException ex) {
            throw new ApplicationException("LLM_CONFIG_JSON_WRITE_FAILED", "Failed to serialize user LLM config", HttpStatus.INTERNAL_SERVER_ERROR);
        }
    }

    private Map<String, Object> readObjectMap(Object value) {
        if (value instanceof Map<?, ?> rawMap) {
            Map<String, Object> result = new LinkedHashMap<>();
            rawMap.forEach((key, mapValue) -> {
                if (key != null) {
                    result.put(String.valueOf(key), mapValue);
                }
            });
            return result;
        }
        return Map.of();
    }

    private void ensureFeatureEnabled() {
        if (!appProperties.getUserLlm().isEnabled()) {
            throw new ApplicationException("USER_LLM_DISABLED", "User LLM settings are disabled", HttpStatus.FORBIDDEN);
        }
    }

    private boolean hasText(Object value) {
        return value != null && !String.valueOf(value).trim().isEmpty();
    }

    private String trim(String value) {
        return value == null ? "" : value.trim();
    }

    private String firstNonBlank(String first, String second) {
        String normalizedFirst = trim(first);
        return normalizedFirst.isBlank() ? trim(second) : normalizedFirst;
    }

    private URI modelsEndpoint(String baseUrl) {
        String normalized = trimTrailingSlash(baseUrl);
        URI baseUri = URI.create(normalized);
        String scheme = baseUri.getScheme() == null ? "" : baseUri.getScheme().toLowerCase(Locale.ROOT);
        if (!scheme.equals("http") && !scheme.equals("https")) {
            throw new IllegalArgumentException("unsupported scheme");
        }
        return URI.create(normalized + "/models");
    }

    private String trimTrailingSlash(String value) {
        String normalized = trim(value);
        while (normalized.endsWith("/")) {
            normalized = normalized.substring(0, normalized.length() - 1);
        }
        return normalized;
    }

    List<String> extractModelIds(String responseBody) {
        try {
            JsonNode root = objectMapper.readTree(responseBody == null ? "{}" : responseBody);
            JsonNode modelArray = root.isArray() ? root : firstArray(root, "data", "models");
            if (modelArray == null || !modelArray.isArray()) {
                return List.of();
            }
            Set<String> modelIds = new TreeSet<>(String.CASE_INSENSITIVE_ORDER);
            for (JsonNode item : modelArray) {
                String id = item.isTextual() ? item.asText() : textField(item, "id", "name", "model");
                if (!id.isBlank()) {
                    modelIds.add(id.trim());
                }
            }
            return new ArrayList<>(modelIds);
        } catch (JsonProcessingException ex) {
            return List.of();
        }
    }

    private JsonNode firstArray(JsonNode root, String... fields) {
        if (root == null || !root.isObject()) {
            return null;
        }
        for (String field : fields) {
            JsonNode candidate = root.get(field);
            if (candidate != null && candidate.isArray()) {
                return candidate;
            }
        }
        return null;
    }

    private String textField(JsonNode node, String... fields) {
        if (node == null || !node.isObject()) {
            return "";
        }
        for (String field : fields) {
            JsonNode candidate = node.get(field);
            if (candidate != null && candidate.isTextual()) {
                return candidate.asText();
            }
        }
        return "";
    }

    public record UserLlmProviderStoredConfig(
        String baseUrl,
        Map<String, String> modelOverrides
    ) {
    }

    public record OverrideBundle(
        Map<String, UserLlmComponentOverrideDto> components,
        Map<String, UserLlmSkillOverrideDto> skills
    ) {
        public OverrideBundle {
            components = components == null ? Map.of() : components;
            skills = skills == null ? Map.of() : skills;
        }
    }
}
