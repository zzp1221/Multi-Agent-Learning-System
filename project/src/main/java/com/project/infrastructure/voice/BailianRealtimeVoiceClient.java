package com.project.infrastructure.voice;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.application.common.ApplicationException;
import com.project.application.voice.VoiceAsrClient;
import com.project.application.voice.VoiceAsrResult;
import com.project.application.voice.VoiceRealtimeAsrClient;
import com.project.application.voice.VoiceRealtimeAsrListener;
import com.project.application.voice.VoiceRealtimeAsrSession;
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
public class BailianRealtimeVoiceClient implements VoiceAsrClient, VoiceTtsClient, VoiceRealtimeAsrClient {

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
            sendJson(exchange.websocket(), withEventId(buildAsrSessionUpdate(sampleRate)));
            for (int offset = 0; offset < pcmAudio.length; offset += ASR_CHUNK_BYTES) {
                int length = Math.min(ASR_CHUNK_BYTES, pcmAudio.length - offset);
                byte[] chunkBytes = java.util.Arrays.copyOfRange(pcmAudio, offset, offset + length);
                String chunk = Base64.getEncoder().encodeToString(chunkBytes);
                sendJson(exchange.websocket(), withEventId(Map.of("type", "input_audio_buffer.append", "audio", chunk)));
            }
            sendJson(exchange.websocket(), withEventId(Map.of("type", "input_audio_buffer.commit")));
            sendJson(exchange.websocket(), withEventId(Map.of("type", "session.finish")));

            JsonNode completed = exchange.awaitEvent(
                appProperties.getVoice().getRequestTimeout(),
                "conversation.item.input_audio_transcription.completed",
                "session.finished"
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
    public VoiceRealtimeAsrSession start(String sessionKey, int sampleRate, VoiceRealtimeAsrListener listener) {
        ensureConfigured();
        RealtimeExchange exchange = openExchange(
            appProperties.getVoice().getAsrWebsocketUrl(),
            appProperties.getVoice().getAsrModel(),
            "ASR-REALTIME",
            event -> handleRealtimeAsrEvent(sessionKey, event, listener)
        );
        RealtimeAsrSession session = new RealtimeAsrSession(sessionKey, exchange);
        try {
            session.send(withEventId(buildAsrSessionUpdate(sampleRate)));
            return session;
        } catch (Exception ex) {
            session.close();
            throw ex;
        }
    }

    @Override
    public void synthesize(String text, String voice, Consumer<VoiceTtsChunk> chunkConsumer) {
        ensureConfigured();
        RealtimeExchange exchange = openExchange(appProperties.getVoice().getTtsWebsocketUrl(), appProperties.getVoice().getTtsModel(), "TTS");
        try {
            sendJson(exchange.websocket(), withEventId(buildTtsSessionUpdate(voice)));
            sendJson(exchange.websocket(), withEventId(Map.of("type", "input_text_buffer.append", "text", text)));
            sendJson(exchange.websocket(), withEventId(Map.of("type", "input_text_buffer.commit")));
            exchange.forwardAudio(appProperties.getVoice().getRequestTimeout(), chunkConsumer);
            sendJson(exchange.websocket(), withEventId(Map.of("type", "session.finish")));
        } finally {
            exchange.close();
        }
    }

    private Map<String, Object> buildAsrSessionUpdate(int sampleRate) {
        Map<String, Object> inputAudioTranscription = new LinkedHashMap<>();
        inputAudioTranscription.put("language", "zh");

        Map<String, Object> turnDetection = new LinkedHashMap<>();
        turnDetection.put("type", "server_vad");
        turnDetection.put("threshold", 0.5D);
        turnDetection.put("silence_duration_ms", 400);

        Map<String, Object> session = new LinkedHashMap<>();
        session.put("input_audio_format", "pcm");
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
        session.put("voice", voice);
        session.put("mode", "commit");
        session.put("language_type", "Auto");
        session.put("response_format", "pcm");
        session.put("sample_rate", appProperties.getVoice().getSampleRate());

        Map<String, Object> event = new LinkedHashMap<>();
        event.put("type", "session.update");
        event.put("session", session);
        return event;
    }

    private RealtimeExchange openExchange(String endpoint, String model, String label) {
        return openExchange(endpoint, model, label, null);
    }

    private RealtimeExchange openExchange(String endpoint, String model, String label, Consumer<JsonNode> eventConsumer) {
        try {
            RealtimeListener listener = new RealtimeListener(label, objectMapper, eventConsumer);
            URI uri = URI.create(endpoint + (endpoint.contains("?") ? "&" : "?") + "model=" + model);
            WebSocket websocket = httpClient.newWebSocketBuilder()
                .connectTimeout(appProperties.getVoice().getConnectTimeout())
                .header("Authorization", "Bearer " + resolvedApiKey())
                .header("User-Agent", "zhixue-voice-java")
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

    private Map<String, Object> withEventId(Map<String, ?> event) {
        Map<String, Object> copy = new LinkedHashMap<>(event);
        copy.putIfAbsent("event_id", "event_" + UUID.randomUUID());
        return copy;
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

    private void handleRealtimeAsrEvent(String sessionKey, JsonNode event, VoiceRealtimeAsrListener listener) {
        String type = event.path("type").asText("");
        try {
            if ("session.created".equals(type) || "session.updated".equals(type)) {
                listener.onReady();
                return;
            }
            if ("conversation.item.input_audio_transcription.completed".equals(type)) {
                String text = extractTranscriptPayload(event);
                listener.onFinal(text == null ? "" : text.trim());
                return;
            }
            if ("conversation.item.input_audio_transcription.text".equals(type)
                || "conversation.item.input_audio_transcription.delta".equals(type)) {
                String text = extractTranscriptPayload(event);
                if (text != null && !text.isBlank()) {
                    listener.onPartial(text.trim());
                }
                return;
            }
            if ("error".equals(type)) {
                listener.onError(new IllegalStateException(extractProviderError(event)));
            }
        } catch (Exception ex) {
            LOGGER.warn("Realtime ASR event handling failed sessionKey={} type={}: {}", sessionKey, type, ex.getMessage());
            listener.onError(ex);
        }
    }

    private String extractTranscriptPayload(JsonNode event) {
        String transcript = textField(event, "transcript");
        if (!transcript.isBlank()) {
            return transcript;
        }

        String text = textField(event, "text");
        String stash = textField(event, "stash");
        if (!text.isBlank() || !stash.isBlank()) {
            return text + stash;
        }

        JsonNode delta = event.path("delta");
        if (delta.isTextual()) {
            return delta.asText();
        }
        if (delta.isObject()) {
            String deltaText = textField(delta, "text");
            if (!deltaText.isBlank()) {
                return deltaText;
            }
            String deltaTranscript = textField(delta, "transcript");
            if (!deltaTranscript.isBlank()) {
                return deltaTranscript;
            }
        }

        JsonNode item = event.path("item");
        if (item.isObject()) {
            String itemTranscript = textField(item, "transcript");
            if (!itemTranscript.isBlank()) {
                return itemTranscript;
            }
        }
        return findText(event);
    }

    private String textField(JsonNode node, String fieldName) {
        JsonNode value = node.path(fieldName);
        return value.isTextual() ? value.asText() : "";
    }

    private String extractProviderError(JsonNode event) {
        JsonNode error = event.path("error");
        String message = textField(error, "message");
        if (!message.isBlank()) {
            return message;
        }
        return "语音识别服务返回错误";
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

    private final class RealtimeAsrSession implements VoiceRealtimeAsrSession {
        private final String sessionKey;
        private final RealtimeExchange exchange;
        private final Object sendLock = new Object();
        private final AtomicBoolean closed = new AtomicBoolean(false);

        private RealtimeAsrSession(String sessionKey, RealtimeExchange exchange) {
            this.sessionKey = sessionKey;
            this.exchange = exchange;
        }

        @Override
        public void appendAudio(byte[] pcmAudio) {
            if (pcmAudio == null || pcmAudio.length == 0 || closed.get()) {
                return;
            }
            String chunk = Base64.getEncoder().encodeToString(pcmAudio);
            send(withEventId(Map.of("type", "input_audio_buffer.append", "audio", chunk)));
        }

        @Override
        public void commit() {
            if (!closed.get()) {
                send(withEventId(Map.of("type", "input_audio_buffer.commit")));
            }
        }

        @Override
        public void cancel() {
            if (!closed.get()) {
                try {
                    send(withEventId(Map.of("type", "input_audio_buffer.clear")));
                } catch (Exception ex) {
                    LOGGER.debug("Realtime ASR clear skipped sessionKey={}: {}", sessionKey, ex.getMessage());
                }
            }
        }

        @Override
        public void close() {
            if (closed.get()) {
                return;
            }
            try {
                sendAllowingClose(withEventId(Map.of("type", "session.finish")));
            } catch (Exception ex) {
                LOGGER.debug("Realtime ASR finish skipped sessionKey={}: {}", sessionKey, ex.getMessage());
            } finally {
                closed.set(true);
                exchange.close();
            }
        }

        private void send(Map<String, ?> payload) {
            synchronized (sendLock) {
                if (closed.get()) {
                    return;
                }
                sendJson(exchange.websocket(), payload);
            }
        }

        private void sendAllowingClose(Map<String, ?> payload) {
            synchronized (sendLock) {
                sendJson(exchange.websocket(), payload);
            }
        }
    }

    private static final class RealtimeListener implements WebSocket.Listener {
        private final String label;
        private final ObjectMapper objectMapper;
        private final Consumer<JsonNode> eventConsumer;
        private final CountDownLatch doneLatch = new CountDownLatch(1);
        private final AtomicBoolean closed = new AtomicBoolean(false);
        private final AtomicReference<JsonNode> lastEvent = new AtomicReference<>();
        private final AtomicReference<String> lastText = new AtomicReference<>("");
        private final LinkedBlockingQueue<JsonNode> events = new LinkedBlockingQueue<>();
        private final StringBuilder buffer = new StringBuilder();
        private volatile WebSocket websocket;

        private RealtimeListener(String label, ObjectMapper objectMapper, Consumer<JsonNode> eventConsumer) {
            this.label = label;
            this.objectMapper = objectMapper;
            this.eventConsumer = eventConsumer;
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
                if (eventConsumer != null) {
                    eventConsumer.accept(event);
                }
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
                    if ("response.audio.done".equals(type) || "response.done".equals(type)) {
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
