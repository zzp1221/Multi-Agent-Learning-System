package com.project.infrastructure.python;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.resource.dto.ResourceExternalCandidateResponse;
import com.project.api.resource.dto.ResourceSemanticHitResponse;
import com.project.api.resource.dto.ResourceSemanticResultResponse;
import com.project.api.resource.dto.ResourceSemanticSearchResponse;
import com.project.application.resource.ResourceSemanticSearchClient;
import com.project.config.AppProperties;
import org.springframework.stereotype.Component;
import org.springframework.web.util.UriComponentsBuilder;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.UUID;

@Component
public class HttpResourceSemanticSearchClient implements ResourceSemanticSearchClient {

    private static final String INTERNAL_TOKEN_HEADER = "X-Zhixue-Internal-Token";
    private static final Path INTERNAL_TOKEN_FILE = Path.of("/run/secrets/zhixue-python-agent-internal-token");
    private static final TypeReference<PythonSemanticSearchPayload> PAYLOAD_TYPE = new TypeReference<>() {
    };

    private final ObjectMapper objectMapper;
    private final AppProperties appProperties;
    private final HttpClient httpClient;

    public HttpResourceSemanticSearchClient(ObjectMapper objectMapper, AppProperties appProperties) {
        this.objectMapper = objectMapper;
        this.appProperties = appProperties;
        this.httpClient = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(appProperties.getPythonAgent().getConnectTimeout())
            .build();
    }

    @Override
    public ResourceSemanticSearchResponse search(UUID userId, String query, int topK) {
        try {
            URI uri = UriComponentsBuilder
                .fromUriString(appProperties.getPythonAgent().getBaseUrl() + "/internal/resources/search/semantic")
                .queryParam("userId", userId)
                .queryParam("query", query)
                .queryParam("topK", topK)
                .build()
                .encode(StandardCharsets.UTF_8)
                .toUri();
            HttpRequest request = HttpRequest.newBuilder()
                .uri(uri)
                .header("Accept", "application/json")
                .header(INTERNAL_TOKEN_HEADER, internalToken())
                .timeout(appProperties.getPythonAgent().getReadTimeout())
                .GET()
                .build();

            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                throw new IllegalStateException("Python resource semantic search returned status " + response.statusCode());
            }
            PythonSemanticSearchPayload payload = objectMapper.readValue(response.body(), PAYLOAD_TYPE);
            return new ResourceSemanticSearchResponse(
                payload.query(),
                payload.available(),
                payload.message(),
                payload.results() == null ? List.of() : payload.results().stream()
                    .map(item -> new ResourceSemanticResultResponse(
                        item.resourceId(),
                        null,
                        item.score(),
                        item.reason(),
                        item.hits() == null ? List.of() : item.hits().stream()
                            .map(hit -> new ResourceSemanticHitResponse(
                                hit.chunkId(),
                                hit.chunkNo(),
                                hit.similarity(),
                                hit.content(),
                                hit.sourceUrl()
                            ))
                            .toList(),
                        item.externalResource()
                    ))
                    .toList()
            );
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Failed to search resources semantically", ex);
        } catch (IOException ex) {
            throw new IllegalStateException("Failed to search resources semantically", ex);
        }
    }

    private String internalToken() {
        String token = appProperties.getPythonAgent().getInternalToken();
        if (token == null || token.isBlank()) {
            token = readInternalTokenFile();
        }
        if (token == null || token.isBlank()) {
            throw new IllegalStateException("PYTHON_AGENT_INTERNAL_TOKEN must be configured");
        }
        return token.trim();
    }

    private String readInternalTokenFile() {
        try {
            return Files.exists(INTERNAL_TOKEN_FILE) ? Files.readString(INTERNAL_TOKEN_FILE, StandardCharsets.UTF_8) : "";
        } catch (IOException ex) {
            throw new IllegalStateException("Failed to read Python agent internal token file", ex);
        }
    }

    private record PythonSemanticSearchPayload(
        String query,
        boolean available,
        String message,
        List<PythonSemanticResultPayload> results
    ) {
    }

    private record PythonSemanticResultPayload(
        UUID resourceId,
        double score,
        String reason,
        List<PythonSemanticHitPayload> hits,
        ResourceExternalCandidateResponse externalResource
    ) {
    }

    private record PythonSemanticHitPayload(
        long chunkId,
        int chunkNo,
        double similarity,
        String content,
        String sourceUrl
    ) {
    }
}
