package com.project.application.settings;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.settings.dto.UserLlmComponentOverrideDto;
import com.project.api.settings.dto.UserLlmProviderConfigDto;
import com.project.api.settings.dto.UserLlmSettingsRequest;
import com.project.api.settings.dto.UserLlmSkillOverrideDto;
import com.project.application.common.ApplicationException;
import com.project.config.AppProperties;
import com.project.domain.settings.UserLlmSettingsRepository;
import com.project.domain.user.UserAccount;
import com.project.domain.user.UserAccountRepository;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class UserLlmSettingsServiceTest {

    @Test
    void savesMaskedSettingsAndDecryptsRuntimeConfig() {
        UUID userId = UUID.randomUUID();
        InMemoryRepository repository = new InMemoryRepository();
        UserLlmSettingsService service = service(repository);

        service.saveSettings(userId, new UserLlmSettingsRequest(
            false,
            "deepseek",
            "",
            Map.of("deepseek", new UserLlmProviderConfigDto(
                "deepseek",
                "https://api.deepseek.com",
                "raw-api-key",
                "",
                "",
                Map.of("main_chat_model", "deepseek-chat")
            )),
            Map.of("query_rewrite_llm", new UserLlmComponentOverrideDto("deepseek", "fast_model")),
            Map.of("tutor_llm", new UserLlmSkillOverrideDto(
                true,
                "Tutor style",
                "Socratic tutor preference",
                "Answer with one concise check question."
            ))
        ));

        String encryptedSecrets = repository.record.encryptedSecretsJson();
        assertThat(encryptedSecrets).doesNotContain("raw-api-key");

        var view = service.getSettings(userId);
        assertThat(view.enabled()).isTrue();
        assertThat(view.providers().get("deepseek").hasApiKey()).isTrue();
        assertThat(view.providers().get("deepseek").modelOverrides()).containsEntry("main_chat_model", "deepseek-chat");

        var runtime = service.runtimeConfig(userId);
        assertThat(runtime.enabled()).isTrue();
        assertThat(runtime.providers().get("deepseek").apiKey()).isEqualTo("raw-api-key");
        assertThat(runtime.componentOverrides().get("query_rewrite_llm").model()).isEqualTo("fast_model");
        assertThat(runtime.skillOverrides().get("tutor_llm").body()).isEqualTo("Answer with one concise check question.");
        assertThat(view.skillOverrides().get("tutor_llm").enabled()).isTrue();
    }

    @Test
    void defaultViewIsAlwaysEnabledButNotReadyWithoutUserKey() {
        UUID userId = UUID.randomUUID();
        UserLlmSettingsService service = service(new InMemoryRepository());

        var view = service.getSettings(userId);

        assertThat(view.enabled()).isTrue();
        assertThatThrownBy(() -> service.testSettings(userId, new UserLlmSettingsRequest(
            false,
            "openai",
            "",
            Map.of("openai", new UserLlmProviderConfigDto(
                "openai",
                "https://api.openai.com/v1",
                "",
                "",
                "",
                Map.of()
            )),
            Map.of(),
            Map.of()
        )))
            .isInstanceOf(ApplicationException.class)
            .extracting("code")
            .isEqualTo("LLM_API_KEY_REQUIRED");
        var runtime = service.runtimeConfig(userId);
        assertThat(runtime.enabled()).isFalse();
        assertThat(runtime.allowEnvironmentFallback()).isFalse();
        assertThat(runtime.providers()).isEmpty();
    }

    @Test
    void testLoginIdAllowsRuntimeEnvironmentFallbackWhenUserConfigIsMissing() {
        UUID userId = UUID.randomUUID();
        InMemoryRepository repository = new InMemoryRepository();
        UserAccountRepository userRepository = mock(UserAccountRepository.class);
        UserAccount user = new UserAccount();
        user.setLoginId("testuser_123456");
        when(userRepository.findById(userId)).thenReturn(Optional.of(user));
        UserLlmSettingsService service = service(repository, userRepository);

        var runtime = service.runtimeConfig(userId);

        assertThat(runtime.enabled()).isFalse();
        assertThat(runtime.allowEnvironmentFallback()).isTrue();
        assertThat(service.isUserLlmReadyOrAllowedFallback(userId)).isTrue();
    }

    @Test
    void formalUserRequiresActiveProviderWithApiKeyBeforeLlmUse() {
        UUID userId = UUID.randomUUID();
        InMemoryRepository repository = new InMemoryRepository();
        UserLlmSettingsService service = service(repository);

        assertThat(service.isUserLlmReadyOrAllowedFallback(userId)).isFalse();

        service.saveSettings(userId, new UserLlmSettingsRequest(
            true,
            "dashscope",
            "",
            Map.of("dashscope", new UserLlmProviderConfigDto(
                "dashscope",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "",
                "",
                "",
                Map.of("main_chat_model", "qwen-plus")
            )),
            Map.of(),
            Map.of()
        ));
        assertThat(service.isUserLlmReadyOrAllowedFallback(userId)).isFalse();

        service.saveSettings(userId, new UserLlmSettingsRequest(
            true,
            "dashscope",
            "",
            Map.of("dashscope", new UserLlmProviderConfigDto(
                "dashscope",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "sk-user-key",
                "",
                "",
                Map.of("main_chat_model", "qwen-plus")
            )),
            Map.of(),
            Map.of()
        ));
        assertThat(service.isUserLlmReadyOrAllowedFallback(userId)).isTrue();
    }

    @Test
    void blankSecretKeepsExistingEncryptedSecret() {
        UUID userId = UUID.randomUUID();
        InMemoryRepository repository = new InMemoryRepository();
        UserLlmSettingsService service = service(repository);

        service.saveSettings(userId, new UserLlmSettingsRequest(
            true,
            "openai",
            "",
            Map.of("openai", new UserLlmProviderConfigDto(
                "openai",
                "https://api.openai.com/v1",
                "first-key",
                "",
                "",
                Map.of()
            )),
            Map.of(),
            Map.of()
        ));
        String firstEncryptedJson = repository.record.encryptedSecretsJson();

        service.saveSettings(userId, new UserLlmSettingsRequest(
            true,
            "openai",
            "",
            Map.of("openai", new UserLlmProviderConfigDto(
                "openai",
                "https://api.openai.com/v1",
                "",
                "",
                "",
                Map.of("fast_model", "gpt-fast")
            )),
            Map.of(),
            Map.of()
        ));

        assertThat(repository.record.encryptedSecretsJson()).isEqualTo(firstEncryptedJson);
        assertThat(service.runtimeConfig(userId).providers().get("openai").apiKey()).isEqualTo("first-key");
        assertThat(service.getSettings(userId).providers().get("openai").modelOverrides()).containsEntry("fast_model", "gpt-fast");
    }

    @Test
    void storesLongProviderSecretWithoutPlaintextLeak() {
        UUID userId = UUID.randomUUID();
        InMemoryRepository repository = new InMemoryRepository();
        UserLlmSettingsService service = service(repository);
        String longKey = "sk-proj-" + "a".repeat(260);

        service.saveSettings(userId, new UserLlmSettingsRequest(
            true,
            "openai",
            "",
            Map.of("openai", new UserLlmProviderConfigDto(
                "openai",
                "https://api.openai.com/v1",
                longKey,
                "",
                "",
                Map.of()
            )),
            Map.of(),
            Map.of()
        ));

        assertThat(repository.record.encryptedSecretsJson()).doesNotContain(longKey);
        assertThat(service.runtimeConfig(userId).providers().get("openai").apiKey()).isEqualTo(longKey);
    }

    @Test
    void testSettingsVerifiesProviderBeforeSaving() throws Exception {
        UUID userId = UUID.randomUUID();
        InMemoryRepository repository = new InMemoryRepository();
        UserLlmSettingsService service = service(repository);
        HttpServer server = startModelListServer("valid-key");
        String baseUrl = "http://" + server.getAddress().getHostString() + ":" + server.getAddress().getPort();

        try {
            UserLlmSettingsRequest invalidRequest = settingsRequest("custom_openai_compatible", baseUrl, "bad-key");
            assertThatThrownBy(() -> service.testSettings(userId, invalidRequest))
                .isInstanceOf(ApplicationException.class)
                .extracting("code")
                .isEqualTo("LLM_MODEL_LIST_UNAUTHORIZED");
            assertThat(repository.record).isNull();

            Map<String, Object> result = service.testSettings(
                userId,
                settingsRequest("custom_openai_compatible", baseUrl, "valid-key")
            );

            assertThat(result)
                .containsEntry("ok", true)
                .containsEntry("activeProvider", "custom_openai_compatible");
            assertThat(repository.record).isNotNull();
            assertThat(service.runtimeConfig(userId).providers().get("custom_openai_compatible").apiKey()).isEqualTo("valid-key");
        } finally {
            server.stop(0);
        }
    }

    @Test
    void extractsProviderModelIdsFromCommonModelListShapes() {
        UserLlmSettingsService service = service(new InMemoryRepository());

        assertThat(service.extractModelIds("""
            {"object":"list","data":[{"id":"gpt-4.1-mini"},{"id":"o4-mini"}]}
            """))
            .containsExactly("gpt-4.1-mini", "o4-mini");
        assertThat(service.extractModelIds("""
            {"models":["qwen-plus","qwen-turbo"]}
            """))
            .containsExactly("qwen-plus", "qwen-turbo");
    }

    @Test
    void readsLegacyFlatComponentOverrideJson() {
        UUID userId = UUID.randomUUID();
        InMemoryRepository repository = new InMemoryRepository();
        repository.record = new UserLlmSettingsRepository.UserLlmSettingsRecord(
            userId,
            true,
            "openai",
            "",
            "{}",
            "{\"tutor_llm\":{\"provider\":\"openai\",\"model\":\"fast_model\"}}",
            "{}",
            "{}"
        );
        UserLlmSettingsService service = service(repository);

        assertThat(service.getSettings(userId).componentOverrides().get("tutor_llm").model()).isEqualTo("fast_model");
        assertThat(service.runtimeConfig(userId).skillOverrides()).isEmpty();
    }

    private UserLlmSettingsService service(InMemoryRepository repository) {
        return service(repository, mock(UserAccountRepository.class));
    }

    private UserLlmSettingsRequest settingsRequest(String provider, String baseUrl, String apiKey) {
        return new UserLlmSettingsRequest(
            true,
            provider,
            "",
            Map.of(provider, new UserLlmProviderConfigDto(
                provider,
                baseUrl,
                apiKey,
                "",
                "",
                Map.of()
            )),
            Map.of(),
            Map.of()
        );
    }

    private HttpServer startModelListServer(String acceptedApiKey) throws IOException {
        HttpServer server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        server.createContext("/models", exchange -> {
            String authHeader = exchange.getRequestHeaders().getFirst("Authorization");
            byte[] body;
            int status;
            if (("Bearer " + acceptedApiKey).equals(authHeader)) {
                status = 200;
                body = "{\"data\":[{\"id\":\"verified-model\"}]}".getBytes(StandardCharsets.UTF_8);
            } else {
                status = 401;
                body = "{\"error\":\"unauthorized\"}".getBytes(StandardCharsets.UTF_8);
            }
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, body.length);
            exchange.getResponseBody().write(body);
            exchange.close();
        });
        server.start();
        return server;
    }

    private UserLlmSettingsService service(InMemoryRepository repository, UserAccountRepository userRepository) {
        AppProperties properties = new AppProperties();
        properties.getUserLlm().setEncryptionKey("0123456789abcdef0123456789abcdef");
        return new UserLlmSettingsService(
            repository,
            new UserLlmSecretCryptoService(properties),
            new ObjectMapper(),
            properties,
            userRepository
        );
    }

    private static class InMemoryRepository implements UserLlmSettingsRepository {
        private UserLlmSettingsRecord record;

        @Override
        public Optional<UserLlmSettingsRecord> findByUserId(UUID userId) {
            if (record == null || !record.userId().equals(userId)) {
                return Optional.empty();
            }
            return Optional.of(record);
        }

        @Override
        public void upsert(UserLlmSettingsRecord record) {
            this.record = record;
        }

        @Override
        public void deleteByUserId(UUID userId) {
            if (record != null && record.userId().equals(userId)) {
                record = null;
            }
        }
    }
}
