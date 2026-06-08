package com.project.infrastructure.python;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.application.smartengine.PythonAgentClient;
import com.project.application.smartengine.PythonStreamEvent;
import com.project.application.smartengine.SmartEngineInvocation;
import com.project.application.smartengine.StreamEventType;
import com.project.config.AppProperties;
import com.project.security.InternalTokenProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Consumer;

/**
 * 基于 JDK {@link HttpClient} 的 Python 流式端点实现，
 * 支持瞬态故障重试和任务取消。
 */
@Component
public class HttpStreamingPythonAgentClient implements PythonAgentClient {

    private static final String INTERNAL_TOKEN_HEADER = "X-Zhixue-Internal-Token";
    private static final TypeReference<Map<String, Object>> MAP_TYPE = new TypeReference<>() {
    };
    private static final Logger LOGGER = LoggerFactory.getLogger(HttpStreamingPythonAgentClient.class);

    private final ObjectMapper objectMapper;
    private final AppProperties appProperties;
    private final InternalTokenProvider internalTokenProvider;
    private final HttpClient httpClient;
    private final ConcurrentHashMap<String, java.io.Closeable> activeStreams = new ConcurrentHashMap<>();

    public HttpStreamingPythonAgentClient(
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
    public void stream(SmartEngineInvocation invocation, Consumer<PythonStreamEvent> eventConsumer) {
        int maxRetries = appProperties.getPythonAgent().getMaxRetries();
        Duration backoff = appProperties.getPythonAgent().getRetryBackoff();
        String traceId = invocation.traceId();

        for (int attempt = 0; attempt <= maxRetries; attempt++) {
            if (attempt > 0) {
                LOGGER.info("Retrying Python agent stream attempt={}/{} traceId={}", attempt, maxRetries, traceId);
                try {
                    Thread.sleep(backoff.multipliedBy(attempt).toMillis());
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    throw new IllegalStateException("Retry interrupted", e);
                }
            }

            try {
                doStream(invocation, eventConsumer);
                return;
            } catch (RetryableStreamException ex) {
                if (attempt == maxRetries) {
                    throw new IllegalStateException(
                        "Python agent stream failed after " + (maxRetries + 1) + " attempts: " + ex.getMessage(), ex);
                }
                LOGGER.warn("Python agent stream attempt {}/{} failed traceId={}: {}",
                    attempt + 1, maxRetries + 1, traceId, ex.getMessage());
            }
        }
    }

    private void doStream(SmartEngineInvocation invocation, Consumer<PythonStreamEvent> eventConsumer) throws RetryableStreamException {
        try {
            String requestBody = buildRequestBody(invocation);
            HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(appProperties.getPythonAgent().getBaseUrl() + "/internal/smart-engine/stream"))
                .header("Content-Type", "application/json")
                .header("Accept", "text/event-stream")
                .header(INTERNAL_TOKEN_HEADER, internalToken())
                .timeout(appProperties.getPythonAgent().getReadTimeout())
                .POST(HttpRequest.BodyPublishers.ofString(requestBody))
                .build();

            HttpResponse<java.io.InputStream> response = httpClient.send(request, HttpResponse.BodyHandlers.ofInputStream());

            if (response.statusCode() >= 500 || response.statusCode() == 429) {
                String body = readBodySafely(response);
                throw new RetryableStreamException(
                    "Python agent returned status " + response.statusCode() + ": " + body);
            }

            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                String body = readBodySafely(response);
                LOGGER.warn(
                    "Python agent rejected request status={} traceId={} taskId={} body={}",
                    response.statusCode(), invocation.traceId(), invocation.taskId(), body);
                throw new IllegalStateException(
                    body == null || body.isBlank()
                        ? "Python agent returned status " + response.statusCode()
                        : "Python agent returned status " + response.statusCode() + ": " + body);
            }

            activeStreams.put(invocation.taskId().toString(), response.body());
            try {
                readEventStream(response, eventConsumer);
            } finally {
                activeStreams.remove(invocation.taskId().toString());
            }
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            activeStreams.remove(invocation.taskId().toString());
            throw new IllegalStateException("Stream interrupted (cancelled)", ex);
        } catch (java.net.ConnectException | java.net.http.HttpConnectTimeoutException ex) {
            throw new RetryableStreamException("Connection failed: " + ex.getMessage(), ex);
        } catch (IOException ex) {
            if (isConnectionReset(ex)) {
                throw new RetryableStreamException("Connection reset: " + ex.getMessage(), ex);
            }
            throw new IllegalStateException("Failed to call Python agent stream", ex);
        }
    }

    @Override
    public void cancel(String taskId) {
        java.io.Closeable stream = activeStreams.remove(taskId);
        if (stream != null) {
            try {
                stream.close();
            } catch (IOException ignored) {
            }
        }
        try {
            HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(appProperties.getPythonAgent().getBaseUrl() + "/internal/smart-engine/" + taskId + "/cancel"))
                .header(INTERNAL_TOKEN_HEADER, internalToken())
                .timeout(Duration.ofSeconds(3))
                .POST(HttpRequest.BodyPublishers.noBody())
                .build();
            httpClient.send(request, HttpResponse.BodyHandlers.discarding());
        } catch (Exception ex) {
            LOGGER.debug("Python cancel notification failed for taskId={}: {}", taskId, ex.getMessage());
        }
    }

    private String internalToken() {
        return internalTokenProvider.requireConfigured();
    }

    private String readBodySafely(HttpResponse<java.io.InputStream> response) {
        try (InputStream body = response.body()) {
            return new String(body.readAllBytes(), StandardCharsets.UTF_8);
        } catch (IOException e) {
            return "";
        }
    }

    private boolean isConnectionReset(IOException ex) {
        String message = ex.getMessage();
        return message != null && (message.contains("Connection reset") || message.contains("GOAWAY"));
    }

    private String buildRequestBody(SmartEngineInvocation invocation) throws IOException {
        Map<String, Object> requestBody = new LinkedHashMap<>();
        requestBody.put("userId", invocation.userId());
        requestBody.put("taskId", invocation.taskId());
        requestBody.put("traceId", invocation.traceId());
        requestBody.put("conversationId", invocation.conversationId());
        requestBody.put("serviceType", invocation.serviceType().value());
        requestBody.put("params", invocation.params());
        return objectMapper.writeValueAsString(requestBody);
    }

    private void readEventStream(
        HttpResponse<java.io.InputStream> response,
        Consumer<PythonStreamEvent> eventConsumer
    ) throws IOException {
        try (BufferedReader reader = new BufferedReader(
            new InputStreamReader(response.body(), StandardCharsets.UTF_8)
        )) {
            String eventType = null;
            String currentStage = null;
            StringBuilder dataBuffer = new StringBuilder();
            String line;

            while ((line = reader.readLine()) != null) {
                if (line.startsWith("event:")) {
                    eventType = line.substring(6).trim();
                    continue;
                }
                if (line.startsWith("data:")) {
                    if (!dataBuffer.isEmpty()) {
                        dataBuffer.append('\n');
                    }
                    dataBuffer.append(line.substring(5));
                    continue;
                }
                if (line.isBlank()) {
                    currentStage = dispatch(eventType, dataBuffer.toString(), currentStage, eventConsumer);
                    eventType = null;
                    dataBuffer.setLength(0);
                }
            }

            if (!dataBuffer.isEmpty() || eventType != null) {
                dispatch(eventType, dataBuffer.toString(), currentStage, eventConsumer);
            }
        }
    }

    private String dispatch(
        String eventType,
        String rawData,
        String currentStage,
        Consumer<PythonStreamEvent> eventConsumer
    ) throws IOException {
        if ((eventType == null || eventType.isBlank()) && (rawData == null || rawData.isBlank())) {
            return currentStage;
        }

        Map<String, Object> envelope = rawData == null || rawData.isBlank()
            ? new LinkedHashMap<>()
            : objectMapper.readValue(rawData, MAP_TYPE);
        Object payloadCandidate = envelope.get("payload");
        @SuppressWarnings("unchecked")
        Map<String, Object> payload = payloadCandidate instanceof Map<?, ?>
            ? new LinkedHashMap<>((Map<String, Object>) payloadCandidate)
            : envelope;

        String resolvedEventType = eventType;
        if ((resolvedEventType == null || resolvedEventType.isBlank()) && envelope.get("event") instanceof String envelopeEventType) {
            resolvedEventType = envelopeEventType;
        }
        String stage = payload.get("stage") instanceof String stageValue ? stageValue : currentStage;
        eventConsumer.accept(new PythonStreamEvent(
            StreamEventType.resolve(resolvedEventType).wireValue(),
            stage,
            payload
        ));
        return stage;
    }

    /**
     * 表示可重试故障（5xx、限流、连接错误）的异常。
     */
    private static class RetryableStreamException extends Exception {
        RetryableStreamException(String message) {
            super(message);
        }

        RetryableStreamException(String message, Throwable cause) {
            super(message, cause);
        }
    }
}
