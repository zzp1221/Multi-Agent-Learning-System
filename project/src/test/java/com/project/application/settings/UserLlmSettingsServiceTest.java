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
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.Authenticator;
import java.net.CookieHandler;
import java.net.InetAddress;
import java.net.ProxySelector;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpHeaders;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayDeque;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Queue;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executor;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLParameters;
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
        StubHttpClient httpClient = new StubHttpClient(
            StubHttpResponse.json(401, "{\"error\":\"unauthorized\"}"),
            StubHttpResponse.json(200, "{\"data\":[{\"id\":\"verified-model\"}]}")
        );
        UserLlmSettingsService service = service(repository, mock(UserAccountRepository.class), httpClient, publicResolver());
        String baseUrl = "https://models.example.com/v1";

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
        assertThat(httpClient.requests()).extracting(HttpRequest::uri)
            .containsExactly(URI.create("https://models.example.com/v1/models"), URI.create("https://models.example.com/v1/models"));
        assertThat(service.runtimeConfig(userId).providers().get("custom_openai_compatible").apiKey()).isEqualTo("valid-key");
    }

    @Test
    void saveSettingsRejectsPrivateOrNonHttpsBaseUrl() throws Exception {
        UUID userId = UUID.randomUUID();
        InMemoryRepository repository = new InMemoryRepository();
        UserLlmSettingsService service = service(
            repository,
            mock(UserAccountRepository.class),
            new StubHttpClient(),
            host -> new InetAddress[] { InetAddress.getByName("10.0.0.5") }
        );

        assertThatThrownBy(() -> service.saveSettings(
            userId,
            settingsRequest("custom_openai_compatible", "https://llm.internal.example/v1", "key")
        ))
            .isInstanceOf(ApplicationException.class)
            .extracting("code")
            .isEqualTo("LLM_BASE_URL_INVALID");

        assertThatThrownBy(() -> service.saveSettings(
            userId,
            settingsRequest("custom_openai_compatible", "http://models.example.com/v1", "key")
        ))
            .isInstanceOf(ApplicationException.class)
            .extracting("code")
            .isEqualTo("LLM_BASE_URL_INVALID");
    }

    @Test
    void modelListRejectsRedirectToPrivateAddress() throws Exception {
        UUID userId = UUID.randomUUID();
        InMemoryRepository repository = new InMemoryRepository();
        UserLlmSettingsService service = service(
            repository,
            mock(UserAccountRepository.class),
            new StubHttpClient(StubHttpResponse.redirect("https://metadata.example.com/models")),
            host -> {
                if ("metadata.example.com".equals(host)) {
                    return new InetAddress[] { InetAddress.getByName("127.0.0.1") };
                }
                return new InetAddress[] { InetAddress.getByName("93.184.216.34") };
            }
        );

        assertThatThrownBy(() -> service.testSettings(
            userId,
            settingsRequest("custom_openai_compatible", "https://models.example.com/v1", "key")
        ))
            .isInstanceOf(ApplicationException.class)
            .extracting("code")
            .isEqualTo("LLM_BASE_URL_INVALID");
        assertThat(repository.record).isNull();
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

    private UserLlmSettingsService service(InMemoryRepository repository, UserAccountRepository userRepository) {
        return service(repository, userRepository, new StubHttpClient(), publicResolver());
    }

    private UserLlmSettingsService service(
        InMemoryRepository repository,
        UserAccountRepository userRepository,
        HttpClient httpClient,
        UserLlmSettingsService.HostAddressResolver resolver
    ) {
        AppProperties properties = new AppProperties();
        properties.getUserLlm().setEncryptionKey("0123456789abcdef0123456789abcdef");
        return new UserLlmSettingsService(
            repository,
            new UserLlmSecretCryptoService(properties),
            new ObjectMapper(),
            properties,
            userRepository,
            httpClient,
            resolver
        );
    }

    private UserLlmSettingsService.HostAddressResolver publicResolver() {
        return host -> new InetAddress[] { InetAddress.getByName("93.184.216.34") };
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

    private static class StubHttpClient extends HttpClient {
        private final Queue<HttpResponse<String>> responses = new ArrayDeque<>();
        private final List<HttpRequest> requests = new java.util.ArrayList<>();

        StubHttpClient(HttpResponse<String>... responses) {
            this.responses.addAll(List.of(responses));
        }

        List<HttpRequest> requests() {
            return requests;
        }

        @Override
        public Optional<CookieHandler> cookieHandler() {
            return Optional.empty();
        }

        @Override
        public Optional<Duration> connectTimeout() {
            return Optional.of(Duration.ofSeconds(1));
        }

        @Override
        public Redirect followRedirects() {
            return Redirect.NEVER;
        }

        @Override
        public Optional<ProxySelector> proxy() {
            return Optional.empty();
        }

        @Override
        public SSLContext sslContext() {
            try {
                return SSLContext.getDefault();
            } catch (Exception ex) {
                throw new IllegalStateException(ex);
            }
        }

        @Override
        public SSLParameters sslParameters() {
            return new SSLParameters();
        }

        @Override
        public Optional<Authenticator> authenticator() {
            return Optional.empty();
        }

        @Override
        public HttpClient.Version version() {
            return HttpClient.Version.HTTP_1_1;
        }

        @Override
        public Optional<Executor> executor() {
            return Optional.empty();
        }

        @Override
        @SuppressWarnings("unchecked")
        public <T> HttpResponse<T> send(HttpRequest request, HttpResponse.BodyHandler<T> responseBodyHandler) throws IOException {
            requests.add(request);
            HttpResponse<String> response = responses.poll();
            if (response == null) {
                response = StubHttpResponse.json(200, "{\"data\":[{\"id\":\"default-model\"}]}");
            }
            return (HttpResponse<T>) response;
        }

        @Override
        public <T> CompletableFuture<HttpResponse<T>> sendAsync(
            HttpRequest request,
            HttpResponse.BodyHandler<T> responseBodyHandler
        ) {
            throw new UnsupportedOperationException("sendAsync is not used in these tests");
        }

        @Override
        public <T> CompletableFuture<HttpResponse<T>> sendAsync(
            HttpRequest request,
            HttpResponse.BodyHandler<T> responseBodyHandler,
            HttpResponse.PushPromiseHandler<T> pushPromiseHandler
        ) {
            throw new UnsupportedOperationException("sendAsync is not used in these tests");
        }
    }

    private record StubHttpResponse(
        int statusCode,
        String body,
        HttpHeaders headers,
        URI uri
    ) implements HttpResponse<String> {
        static StubHttpResponse json(int status, String body) {
            return new StubHttpResponse(
                status,
                body,
                HttpHeaders.of(Map.of("Content-Type", List.of("application/json")), (name, value) -> true),
                URI.create("https://models.example.com/v1/models")
            );
        }

        static StubHttpResponse redirect(String location) {
            return new StubHttpResponse(
                302,
                "",
                HttpHeaders.of(Map.of("Location", List.of(location)), (name, value) -> true),
                URI.create("https://models.example.com/v1/models")
            );
        }

        @Override
        public HttpRequest request() {
            return null;
        }

        @Override
        public Optional<HttpResponse<String>> previousResponse() {
            return Optional.empty();
        }

        @Override
        public HttpHeaders headers() {
            return headers;
        }

        @Override
        public String body() {
            return body;
        }

        @Override
        public Optional<javax.net.ssl.SSLSession> sslSession() {
            return Optional.empty();
        }

        @Override
        public URI uri() {
            return uri;
        }

        @Override
        public HttpClient.Version version() {
            return HttpClient.Version.HTTP_1_1;
        }
    }
}
