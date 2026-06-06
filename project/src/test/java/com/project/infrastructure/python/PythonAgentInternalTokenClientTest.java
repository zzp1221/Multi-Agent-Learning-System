package com.project.infrastructure.python;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.config.AppProperties;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

class PythonAgentInternalTokenClientTest {

    private static final String INTERNAL_TOKEN_HEADER = "X-Zhixue-Internal-Token";
    private static final String INTERNAL_TOKEN = "test-internal-token";

    private HttpServer server;
    private AppProperties appProperties;

    @BeforeEach
    void setUp() throws IOException {
        server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        server.start();

        appProperties = new AppProperties();
        appProperties.getPythonAgent().setBaseUrl("http://127.0.0.1:" + server.getAddress().getPort());
        appProperties.getPythonAgent().setInternalToken(INTERNAL_TOKEN);
        appProperties.getPythonAgent().setReadTimeout(Duration.ofSeconds(2));
    }

    @AfterEach
    void tearDown() {
        if (server != null) {
            server.stop(0);
        }
    }

    @Test
    void conversationClientSendsInternalTokenHeader() throws Exception {
        UUID conversationId = UUID.randomUUID();
        CountDownLatch received = new CountDownLatch(1);
        AtomicReference<String> tokenHeader = new AtomicReference<>();
        server.createContext("/internal/conversations/" + conversationId + "/messages", exchange -> {
            tokenHeader.set(exchange.getRequestHeaders().getFirst(INTERNAL_TOKEN_HEADER));
            received.countDown();
            send(exchange, 200, "{\"messageId\":\"message-1\"}");
        });

        HttpPythonConversationMessageClient client = new HttpPythonConversationMessageClient(
            new ObjectMapper(),
            appProperties
        );

        client.appendMessage(conversationId, UUID.randomUUID(), "user", "hello", List.of());

        assertThat(received.await(2, TimeUnit.SECONDS)).isTrue();
        assertThat(tokenHeader.get()).isEqualTo(INTERNAL_TOKEN);
    }

    @Test
    void streamingClientCancelSendsInternalTokenHeader() throws Exception {
        CountDownLatch received = new CountDownLatch(1);
        AtomicReference<String> tokenHeader = new AtomicReference<>();
        server.createContext("/internal/smart-engine/task-1/cancel", exchange -> {
            tokenHeader.set(exchange.getRequestHeaders().getFirst(INTERNAL_TOKEN_HEADER));
            received.countDown();
            send(exchange, 200, "{\"status\":\"cancelled\"}");
        });

        HttpStreamingPythonAgentClient client = new HttpStreamingPythonAgentClient(
            new ObjectMapper(),
            appProperties
        );

        client.cancel("task-1");

        assertThat(received.await(2, TimeUnit.SECONDS)).isTrue();
        assertThat(tokenHeader.get()).isEqualTo(INTERNAL_TOKEN);
    }

    @Test
    void resourceSemanticSearchEncodesQueryAndSendsInternalTokenHeader() throws Exception {
        CountDownLatch received = new CountDownLatch(1);
        AtomicReference<String> tokenHeader = new AtomicReference<>();
        AtomicReference<String> rawQuery = new AtomicReference<>();
        server.createContext("/internal/resources/search/semantic", exchange -> {
            tokenHeader.set(exchange.getRequestHeaders().getFirst(INTERNAL_TOKEN_HEADER));
            rawQuery.set(exchange.getRequestURI().getRawQuery());
            received.countDown();
            send(exchange, 200, """
                {
                  "query": "\\u52a8\\u6001 \\u89c4\\u5212",
                  "available": true,
                  "message": "ok",
                  "results": []
                }
                """);
        });

        HttpResourceSemanticSearchClient client = new HttpResourceSemanticSearchClient(
            new ObjectMapper(),
            appProperties
        );

        client.search(UUID.fromString("60000000-0000-0000-0000-000000000006"), "\u52a8\u6001 \u89c4\u5212", 8);

        assertThat(received.await(2, TimeUnit.SECONDS)).isTrue();
        assertThat(tokenHeader.get()).isEqualTo(INTERNAL_TOKEN);
        assertThat(rawQuery.get())
            .contains("query=%E5%8A%A8%E6%80%81%20%E8%A7%84%E5%88%92")
            .contains("topK=8");
    }

    private void send(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream output = exchange.getResponseBody()) {
            output.write(bytes);
        }
    }
}
