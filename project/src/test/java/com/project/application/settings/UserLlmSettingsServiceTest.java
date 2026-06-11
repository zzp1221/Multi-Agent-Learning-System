package com.project.application.settings;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.settings.dto.UserLlmComponentOverrideDto;
import com.project.api.settings.dto.UserLlmProviderConfigDto;
import com.project.api.settings.dto.UserLlmSettingsRequest;
import com.project.api.settings.dto.UserLlmSkillOverrideDto;
import com.project.config.AppProperties;
import com.project.domain.settings.UserLlmSettingsRepository;
import com.project.domain.user.UserAccount;
import com.project.domain.user.UserAccountRepository;
import org.junit.jupiter.api.Test;

import java.util.Map;
import java.util.Optional;
import java.util.UUID;

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
        var testResult = service.testSettings(userId, new UserLlmSettingsRequest(
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
        ));

        assertThat(view.enabled()).isTrue();
        assertThat(testResult).containsEntry("ok", false);
        assertThat(service.runtimeConfig(userId).enabled()).isTrue();
        assertThat(service.runtimeConfig(userId).allowEnvironmentFallback()).isFalse();
        assertThat(service.runtimeConfig(userId).providers().get("openai").apiKey()).isEmpty();
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
