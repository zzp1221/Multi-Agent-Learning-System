package com.project.infrastructure.python;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.application.note.NoteAiAnalysisResult;
import com.project.application.note.NotePythonClient;
import com.project.application.note.NoteRagIndexRequest;
import com.project.application.note.NoteRagIndexResult;
import com.project.application.note.NoteSemanticHit;
import com.project.application.note.NoteSemanticResult;
import com.project.application.note.NoteSemanticSearchResult;
import com.project.application.note.NoteTodoResult;
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
import java.util.Map;
import java.util.UUID;

@Component
public class HttpNotePythonClient implements NotePythonClient {

    private static final String INTERNAL_TOKEN_HEADER = "X-Zhixue-Internal-Token";
    private static final Path INTERNAL_TOKEN_FILE = Path.of("/run/secrets/zhixue-python-agent-internal-token");
    private static final TypeReference<PythonNoteAnalysisPayload> ANALYSIS_TYPE = new TypeReference<>() {
    };
    private static final TypeReference<PythonNoteIndexPayload> INDEX_TYPE = new TypeReference<>() {
    };
    private static final TypeReference<PythonNoteSemanticSearchPayload> SEARCH_TYPE = new TypeReference<>() {
    };

    private final ObjectMapper objectMapper;
    private final AppProperties appProperties;
    private final HttpClient httpClient;

    public HttpNotePythonClient(ObjectMapper objectMapper, AppProperties appProperties) {
        this.objectMapper = objectMapper;
        this.appProperties = appProperties;
        this.httpClient = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(appProperties.getPythonAgent().getConnectTimeout())
            .build();
    }

    @Override
    public NoteAiAnalysisResult analyze(String title, String markdownContent, String plainText) {
        PythonNoteAnalysisPayload payload = postJson(
            "/internal/notes/analyze",
            Map.of(
                "title", title == null ? "" : title,
                "markdownContent", markdownContent == null ? "" : markdownContent,
                "plainText", plainText == null ? "" : plainText
            ),
            ANALYSIS_TYPE
        );
        return new NoteAiAnalysisResult(
            payload.summary(),
            payload.keywords() == null ? List.of() : payload.keywords(),
            payload.todos() == null ? List.of() : payload.todos().stream()
                .map(todo -> new NoteTodoResult(todo.title(), todo.priority(), todo.completed()))
                .toList(),
            payload.provider(),
            payload.model()
        );
    }

    @Override
    public NoteRagIndexResult index(NoteRagIndexRequest request) {
        PythonNoteIndexPayload payload = postJson(
            "/internal/notes/index",
            Map.of(
                "userId", request.userId(),
                "noteId", request.noteId(),
                "resourceId", request.resourceId(),
                "title", request.title(),
                "markdownContent", request.markdownContent(),
                "plainText", request.plainText(),
                "contentHash", request.contentHash(),
                "tags", request.tags()
            ),
            INDEX_TYPE
        );
        return new NoteRagIndexResult(payload.indexed(), payload.chunkCount(), payload.message());
    }

    @Override
    public NoteSemanticSearchResult semanticSearch(UUID userId, String query, int topK) {
        try {
            URI uri = UriComponentsBuilder
                .fromUriString(appProperties.getPythonAgent().getBaseUrl() + "/internal/notes/search/semantic")
                .queryParam("userId", userId)
                .queryParam("query", query)
                .queryParam("topK", topK)
                .build()
                .encode(StandardCharsets.UTF_8)
                .toUri();
            HttpRequest request = baseRequest(uri).GET().build();
            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            ensureSuccess(response);
            PythonNoteSemanticSearchPayload payload = objectMapper.readValue(response.body(), SEARCH_TYPE);
            return new NoteSemanticSearchResult(
                payload.query(),
                payload.available(),
                payload.message(),
                payload.results() == null ? List.of() : payload.results().stream()
                    .map(item -> new NoteSemanticResult(
                        item.noteId(),
                        item.resourceId(),
                        item.score(),
                        item.reason(),
                        item.hits() == null ? List.of() : item.hits().stream()
                            .map(hit -> new NoteSemanticHit(hit.chunkId(), hit.chunkNo(), hit.similarity(), hit.content()))
                            .toList()
                    ))
                    .toList()
            );
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Failed to search notes semantically", ex);
        } catch (IOException ex) {
            throw new IllegalStateException("Failed to search notes semantically", ex);
        }
    }

    private <T> T postJson(String path, Object body, TypeReference<T> responseType) {
        try {
            URI uri = URI.create(appProperties.getPythonAgent().getBaseUrl() + path);
            HttpRequest request = baseRequest(uri)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(objectMapper.writeValueAsString(body), StandardCharsets.UTF_8))
                .build();
            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            ensureSuccess(response);
            return objectMapper.readValue(response.body(), responseType);
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Failed to call Python note endpoint", ex);
        } catch (IOException ex) {
            throw new IllegalStateException("Failed to call Python note endpoint", ex);
        }
    }

    private HttpRequest.Builder baseRequest(URI uri) {
        return HttpRequest.newBuilder()
            .uri(uri)
            .header("Accept", "application/json")
            .header(INTERNAL_TOKEN_HEADER, internalToken())
            .timeout(appProperties.getPythonAgent().getReadTimeout());
    }

    private void ensureSuccess(HttpResponse<String> response) {
        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new IllegalStateException("Python note endpoint returned status " + response.statusCode());
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

    private record PythonNoteAnalysisPayload(
        String summary,
        List<String> keywords,
        List<PythonNoteTodoPayload> todos,
        String provider,
        String model
    ) {
    }

    private record PythonNoteTodoPayload(String title, String priority, boolean completed) {
    }

    private record PythonNoteIndexPayload(boolean indexed, int chunkCount, String message) {
    }

    private record PythonNoteSemanticSearchPayload(
        String query,
        boolean available,
        String message,
        List<PythonNoteSemanticResultPayload> results
    ) {
    }

    private record PythonNoteSemanticResultPayload(
        UUID noteId,
        UUID resourceId,
        double score,
        String reason,
        List<PythonNoteSemanticHitPayload> hits
    ) {
    }

    private record PythonNoteSemanticHitPayload(
        long chunkId,
        int chunkNo,
        double similarity,
        String content
    ) {
    }
}
