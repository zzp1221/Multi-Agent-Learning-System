package com.project.infrastructure.python;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.conversation.dto.ConversationMessageItemResponse;
import com.project.application.conversation.PythonConversationMessageClient;
import com.project.config.AppProperties;
import com.project.security.InternalTokenProvider;
import org.springframework.stereotype.Component;
import org.springframework.web.util.UriComponentsBuilder;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Python Agent 对话记录持久化 API 的 HTTP 客户端。
 */
@Component
public class HttpPythonConversationMessageClient implements PythonConversationMessageClient {

    private static final String INTERNAL_TOKEN_HEADER = "X-Zhixue-Internal-Token";
    private static final TypeReference<List<PythonConversationMessagePayload>> LIST_TYPE = new TypeReference<>() {
    };

    private final ObjectMapper objectMapper;
    private final AppProperties appProperties;
    private final InternalTokenProvider internalTokenProvider;
    private final HttpClient httpClient;

    public HttpPythonConversationMessageClient(
        ObjectMapper objectMapper,
        AppProperties appProperties,
        InternalTokenProvider internalTokenProvider
    ) {
        this.objectMapper = objectMapper;
        this.appProperties = appProperties;
        this.internalTokenProvider = internalTokenProvider;
        this.httpClient = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(appProperties.getPythonAgent().getConnectTimeout())
            .build();
    }

    @Override
    public void appendMessage(
        UUID conversationId,
        UUID userId,
        String role,
        String content,
        List<String> imageUrls,
        String reasoningContent
    ) {
        List<String> normalizedImageUrls = imageUrls == null ? List.of() : imageUrls.stream()
            .filter(item -> item != null && !item.isBlank())
            .map(String::trim)
            .distinct()
            .toList();
        String normalizedContent = content == null ? "" : content.trim();
        String normalizedReasoningContent = reasoningContent == null ? "" : reasoningContent.trim();
        if (normalizedContent.isBlank() && normalizedImageUrls.isEmpty() && normalizedReasoningContent.isBlank()) {
            return;
        }
        try {
            Map<String, Object> payload = new LinkedHashMap<>();
            payload.put("role", role);
            payload.put("content", normalizedContent);
            payload.put("imageUrls", normalizedImageUrls);
            payload.put("userId", userId.toString());
            if (!normalizedReasoningContent.isBlank()) {
                payload.put("reasoningContent", normalizedReasoningContent);
            }
            HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(appProperties.getPythonAgent().getBaseUrl() + "/internal/conversations/" + conversationId + "/messages"))
                .header("Content-Type", "application/json")
                .header(INTERNAL_TOKEN_HEADER, internalToken())
                .timeout(appProperties.getPythonAgent().getReadTimeout())
                .POST(HttpRequest.BodyPublishers.ofString(objectMapper.writeValueAsString(payload)))
                .build();

            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                throw new IllegalStateException("Python transcript append returned status " + response.statusCode());
            }
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Failed to append conversation message", ex);
        } catch (IOException ex) {
            throw new IllegalStateException("Failed to append conversation message", ex);
        }
    }

    @Override
    public List<ConversationMessageItemResponse> listMessages(UUID conversationId, UUID userId) {
        return listMessages(conversationId, userId, null, null);
    }

    @Override
    public List<ConversationMessageItemResponse> listMessages(UUID conversationId, UUID userId, Integer page, Integer size) {
        try {
            UriComponentsBuilder builder = UriComponentsBuilder
                .fromUriString(appProperties.getPythonAgent().getBaseUrl() + "/internal/conversations/" + conversationId + "/messages")
                .queryParam("userId", userId);
            if (page != null) {
                builder.queryParam("page", page);
            }
            if (size != null) {
                builder.queryParam("size", size);
            }
            URI uri = builder.build(true).toUri();
            HttpRequest request = HttpRequest.newBuilder()
                .uri(uri)
                .header("Accept", "application/json")
                .header(INTERNAL_TOKEN_HEADER, internalToken())
                .timeout(appProperties.getPythonAgent().getReadTimeout())
                .GET()
                .build();

            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                throw new IllegalStateException("Python transcript query returned status " + response.statusCode());
            }
            List<PythonConversationMessagePayload> payloads = objectMapper.readValue(response.body(), LIST_TYPE);
            return payloads.stream()
                .map(payload -> new ConversationMessageItemResponse(
                    payload.messageId(),
                    payload.role(),
                    payload.content(),
                    payload.imageUrls(),
                    payload.createdAt(),
                    payload.reasoningContent()
                ))
                .toList();
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Failed to list conversation messages", ex);
        } catch (IOException ex) {
            throw new IllegalStateException("Failed to list conversation messages", ex);
        }
    }

    private record PythonConversationMessagePayload(
        String messageId,
        String role,
        String content,
        List<String> imageUrls,
        OffsetDateTime createdAt,
        String reasoningContent
    ) {
    }

    private String internalToken() {
        return internalTokenProvider.requireConfigured();
    }
}
