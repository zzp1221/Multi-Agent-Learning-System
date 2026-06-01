package com.project.infrastructure.voice;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.application.common.ApplicationException;
import com.project.application.voice.VoiceAsrClient;
import com.project.application.voice.VoiceAsrResult;
import com.project.application.voice.VoiceTtsChunk;
import com.project.application.voice.VoiceTtsClient;
import com.project.config.AppProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;

@Component
public class BailianRealtimeVoiceClient implements VoiceAsrClient, VoiceTtsClient {

    private static final Logger LOGGER = LoggerFactory.getLogger(BailianRealtimeVoiceClient.class);
    private static final int ASR_CHUNK_BYTES = 6400;
    private static final int NORMAL_CLOSE = 1000;

    private final AppProperties appProperties;
    private final ObjectMapper objectMapper;
    private final HttpClient httpClient;

    public BailianRealtimeVoiceClient(AppProperties appProperties, ObjectMapper objectMapper) {
        this.appProperties = appProperties;
        this.objectMapper = objectMapper;
        this.httpClient = HttpClient.newBuilder()
            .connectTimeout(appProperties.getVoice().getConnectTimeout())
            .build();
    }

    @Override
    public VoiceAsrResult transcribePcm16(byte[] pcmAudio, int sampleRate) {
        ensureConfigured();
        RealtimeExchange exchange = openExchange(appProperties.getVoice().getAsrWebsocketUrl(), appProperties.getVoice().getAsrModel(), "ASR");
        try {
            sendJson(exchange.websocket(), buildAsrSessionUpdate(sampleRate));
            for (int offset = 0; offset < pcmAudio.length; offset += ASR_CHUNK_BYTES) {
                int length = Math.min(ASR_CHUNK_BYTES, pcmAudio.length - offset);
                byte[] chunkBytes = java.util.Arrays.copyOfRange(pcmAudio, offset, offset + length);
                String chunk = Base64.getEncoder().encodeToString(chunkBytes);
                sendJson(exchange.websocket(), Map.of("type", "input_audio_buffer.append", "audio", chunk));
            }
            sendJson(exchange.websocket(), Map.of("type", "input_audio_buffer.commit"));
            sendJson(exchange.websocket(), Map.of("type", "response.create"));

            JsonNode completed = exchange.awaitEvent(
                appProperties.getVoice().getRequestTimeout(),
                "conversation.item.input_audio_transcription.completed",
                "response.done"
            );
            String text = findText(completed);
            if (text.isBlank()) {
                text = exchange.lastText();
            }
            int durationMs = (int) Math.round((pcmAudio.length / 2.0D) * 1000.0D / sampleRate);
            return new VoiceAsrResult(text.trim(), durationMs, appProperties.getVoice().getProvider(), appProperties.getVoice().getAsrModel());
        } finally {
            exchange.close();
        }
    }

    @Override
    public void synthesize(String text, String voice, Consumer<VoiceTtsChunk> chunkConsumer) {
        ensureConfigured();
        RealtimeExchange exchange = openExchange(appProperties.getVoice().getTtsWebsocketUrl(), appProperties.getVoice().getTtsModel(), "TTS");
        try {
            sendJson(exchange.websocket(), buildTtsSessionUpdate(voice));
            sendJson(exchange.websocket(), Map.of(
                "type", "conversation.item.create",
                "item", Map.of(
                    "type", "message",
                    "role", "user",
                    "content", new Object[] { Map.of("type", "input_text", "text", text) }
                )
            ));
            sendJson(exchange.websocket(), Map.of("type", "response.create"));
            exchange.forwardAudio(appProperties.getVoice().getRequestTimeout(), chunkConsumer);
        } finally {
            exchange.close();
        }
    }

    private Map<String, Object> buildAsrSessionUpdate(int sampleRate) {
        Map<String, Object> turnDetection = new LinkedHashMap<>();
        turnDetection.put("type", "server_vad");

        Map<String, Object> inputAudioTranscription = new LinkedHashMap<>();
        inputAudioTranscription.put("model", appProperties.getVoice().getAsrModel());

        Map<String, Object> session = new LinkedHashMap<>();
        session.put("modalities", new String[] {"text"});
        session.put("input_audio_format", "pcm16");
        session.put("sample_rate", sampleRate);
        session.put("input_audio_transcription", inputAudioTranscription);
        session.put("turn_detection", turnDetection);

        Map<String, Object> event = new LinkedHashMap<>();
        event.put("type", "session.update");
        event.put("session", session);
        return event;
    }

    private Map<String, Object> buildTtsSessionUpdate(String requestedVoice) {
        String voice = requestedVoice == null || requestedVoice.isBlank()
            ? appProperties.getVoice().getTtsVoice()
            : requestedVoice.trim();
        Map<String, Object> session = new LinkedHashMap<>();
        session.put("modalities", new String[] {"audio"});
        session.put("voice", voice);
        session.put("output_audio_format", "pcm16");
        session.put("sample_rate", appProperties.getVoice().getSampleRate());
        session.put("model", appProperties.getVoice().getTtsModel());

        Map<String, Object> event = new LinkedHashMap<>();
        event.put("type", "session.update");
        event.put("session", session);
        return event;
    }

    private RealtimeExchange openExchange(String endpoint, String model, String label) {
        try {
            RealtimeListener listener = new RealtimeListener(label, objectMapper);
            URI uri = URI.create(endpoint + (endpoint.contains("?") ? "&" : "?") + "model=" + model);
            WebSocket websocket = httpClient.newWebSocketBuilder()
                .connectTimeout(appProperties.getVoice().getConnectTimeout())
                .header("Authorization", "Bearer " + resolvedApiKey())
                .header("X-DashScope-DataInspection", "enable")
                .buildAsync(uri, listener)
                .join();
            listener.attach(websocket);
            return new RealtimeExchange(websocket, listener);
        } catch (Exception ex) {
            LOGGER.warn("{} websocket open failed: {}", label, ex.getMessage());
            throw new ApplicationException("VOICE_PROVIDER_UNAVAILABLE", "语音服务连接失败，请稍后重试", HttpStatus.BAD_GATEWAY);
        }
    }

    private void sendJson(WebSocket websocket, Object payload) {
        try {
            websocket.sendText(objectMapper.writeValueAsString(payload), true).join();
        } catch (JsonProcessingException ex) {
            throw new ApplicationException("VOICE_PAYLOAD_INVALID", "语音请求构造失败", HttpStatus.INTERNAL_SERVER_ERROR);
        }
    }

    private String findText(JsonNode node) {
        if (node == null || node.isMissingNode() || node.isNull()) {
            return "";
        }
        for (String field : new String[] {"transcript", "text", "output_text"}) {
            JsonNode value = node.findValue(field);
            if (value != null && value.isTextual()) {
                return value.asText();
            }
        }
        return "";
    }

    private void ensureConfigured() {
        if (!appProperties.getVoice().isEnabled()) {
            throw new ApplicationException("VOICE_DISABLED", "语音助手未启用", HttpStatus.SERVICE_UNAVAILABLE);
        }
        if (resolvedApiKey().isBlank()) {
            throw new ApplicationException("VOICE_API_KEY_MISSING", "语音服务密钥未配置", HttpStatus.SERVICE_UNAVAILABLE);
        }
    }

    private String resolvedApiKey() {
        String apiKey = appProperties.getVoice().getApiKey();
        return apiKey == null ? "" : apiKey.trim();
    }

    private final class RealtimeExchange {
        private final WebSocket websocket;
        private final RealtimeListener listener;

        private RealtimeExchange(WebSocket websocket, RealtimeListener listener) {
            this.websocket = websocket;
            this.listener = listener;
        }

        private WebSocket websocket() {
            return websocket;
        }

        private JsonNode awaitEvent(Duration timeout, String... eventTypes) {
            if (!listener.await(timeout)) {
                throw new ApplicationException("VOICE_PROVIDER_TIMEOUT", "语音服务响应超时", HttpStatus.GATEWAY_TIMEOUT);
            }
            JsonNode event = listener.findLast(eventTypes);
            if (event == null) {
                throw new ApplicationException("VOICE_PROVIDER_RESPONSE_INVALID", "语音服务未返回有效结果", HttpStatus.BAD_GATEWAY);
            }
            return event;
        }

        private void forwardAudio(Duration timeout, Consumer<VoiceTtsChunk> chunkConsumer) {
            if (!listener.forwardAudio(timeout, chunkConsumer)) {
                throw new ApplicationException("VOICE_PROVIDER_TIMEOUT", "语音合成响应超时", HttpStatus.GATEWAY_TIMEOUT);
            }
        }

        private String lastText() {
            return listener.lastText();
        }

        private void close() {
            try {
                websocket.sendClose(NORMAL_CLOSE, "done").join();
            } catch (Exception ex) {
                LOGGER.debug("Voice websocket close skipped", ex);
            }
        }
    }

    private static final class RealtimeListener implements WebSocket.Listener {
        private final String label;
        private final ObjectMapper objectMapper;
        private final CountDownLatch doneLatch = new CountDownLatch(1);
        private final AtomicBoolean closed = new AtomicBoolean(false);
        private final AtomicReference<JsonNode> lastEvent = new AtomicReference<>();
        private final AtomicReference<String> lastText = new AtomicReference<>("");
        private final LinkedBlockingQueue<JsonNode> events = new LinkedBlockingQueue<>();
        private final StringBuilder buffer = new StringBuilder();
        private volatile WebSocket websocket;

        private RealtimeListener(String label, ObjectMapper objectMapper) {
            this.label = label;
            this.objectMapper = objectMapper;
        }

        @Override
        public void onOpen(WebSocket websocket) {
            websocket.request(1);
        }

        private void attach(WebSocket websocket) {
            this.websocket = websocket;
        }

        @Override
        public CompletionStage<?> onText(WebSocket websocket, CharSequence data, boolean last) {
            buffer.append(data);
            if (last) {
                handleMessage(buffer.toString());
                buffer.setLength(0);
            }
            websocket.request(1);
            return CompletableFuture.completedFuture(null);
        }

        @Override
        public CompletionStage<?> onClose(WebSocket websocket, int statusCode, String reason) {
            closed.set(true);
            doneLatch.countDown();
            return WebSocket.Listener.super.onClose(websocket, statusCode, reason);
        }

        @Override
        public void onError(WebSocket websocket, Throwable error) {
            closed.set(true);
            doneLatch.countDown();
            LOGGER.warn("{} websocket error: {}", label, error.getMessage());
        }

        private void handleMessage(String raw) {
            try {
                JsonNode event = objectMapper.readTree(raw);
                lastEvent.set(event);
                events.offer(event);
                updateText(event);
                String type = event.path("type").asText("");
                if (type.endsWith(".done")
                    || "conversation.item.input_audio_transcription.completed".equals(type)
                    || "response.done".equals(type)
                    || "error".equals(type)) {
                    doneLatch.countDown();
                }
            } catch (Exception ex) {
                LOGGER.debug("{} websocket ignored malformed message: {}", label, raw, ex);
            }
        }

        private void updateText(JsonNode event) {
            for (String field : new String[] {"transcript", "text", "output_text"}) {
                JsonNode value = event.findValue(field);
                if (value != null && value.isTextual() && !value.asText().isBlank()) {
                    lastText.set(value.asText());
                    return;
                }
            }
        }

        private boolean await(Duration timeout) {
            try {
                return doneLatch.await(timeout.toMillis(), TimeUnit.MILLISECONDS);
            } catch (InterruptedException ex) {
                Thread.currentThread().interrupt();
                return false;
            }
        }

        private JsonNode findLast(String... eventTypes) {
            JsonNode event = lastEvent.get();
            if (event == null) {
                return null;
            }
            String type = event.path("type").asText("");
            for (String eventType : eventTypes) {
                if (eventType.equals(type)) {
                    return event;
                }
            }
            return event;
        }

        private String lastText() {
            return lastText.get();
        }

        private boolean forwardAudio(Duration timeout, Consumer<VoiceTtsChunk> chunkConsumer) {
            long deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeout.toMillis());
            while (System.nanoTime() < deadline && !closed.get()) {
                long remainingMs = TimeUnit.NANOSECONDS.toMillis(deadline - System.nanoTime());
                JsonNode event = pollEvent(Math.min(Math.max(remainingMs, 1), 100));
                if (event != null) {
                    String type = event.path("type").asText("");
                    JsonNode delta = event.findValue("delta");
                    if (type.contains("audio") && delta != null && delta.isTextual() && !delta.asText().isBlank()) {
                        chunkConsumer.accept(new VoiceTtsChunk(delta.asText(), 16000, "pcm16", false));
                    }
                    if ("response.done".equals(type) || type.endsWith(".done")) {
                        chunkConsumer.accept(new VoiceTtsChunk("", 16000, "pcm16", true));
                        return true;
                    }
                    if ("error".equals(type)) {
                        return false;
                    }
                }
                requestNext();
            }
            return false;
        }

        private JsonNode pollEvent(long timeoutMs) {
            try {
                return events.poll(timeoutMs, TimeUnit.MILLISECONDS);
            } catch (InterruptedException ex) {
                Thread.currentThread().interrupt();
                return null;
            }
        }

        private void requestNext() {
            WebSocket current = websocket;
            if (current != null) {
                current.request(1);
            }
        }
    }
}
