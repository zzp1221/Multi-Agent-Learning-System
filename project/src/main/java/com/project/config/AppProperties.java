package com.project.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;

/**
 * Java 控制平面的集中式应用属性。
 *
 * <p>将面向基础设施的配置聚合到一个对象中，后续模块只需注入
 * 单一配置聚合体，而非分散的 {@code @Value} 查找。
 * 嵌套类设计为扩展点，支持 JWT 签发、Python 流式客户端、
 * 跨域访问控制等未来模块。</p>
 */
@ConfigurationProperties(prefix = "app")
public class AppProperties {

    private final PythonAgent pythonAgent = new PythonAgent();
    private final Cors cors = new Cors();
    private final Security security = new Security();
    private final Idempotency idempotency = new Idempotency();
    private final RateLimit rateLimit = new RateLimit();
    private final Download download = new Download();
    private final Upload upload = new Upload();
    private final SmartEngineQueue smartEngineQueue = new SmartEngineQueue();
    private final Voice voice = new Voice();

    public PythonAgent getPythonAgent() {
        return pythonAgent;
    }

    public Cors getCors() {
        return cors;
    }

    public Security getSecurity() {
        return security;
    }

    public Idempotency getIdempotency() {
        return idempotency;
    }

    public RateLimit getRateLimit() {
        return rateLimit;
    }

    public Download getDownload() {
        return download;
    }

    public Upload getUpload() {
        return upload;
    }

    public SmartEngineQueue getSmartEngineQueue() {
        return smartEngineQueue;
    }

    public Voice getVoice() {
        return voice;
    }

    public static class PythonAgent {
        private String baseUrl = "http://localhost:8000";
        private String internalToken = "";
        private Duration connectTimeout = Duration.ofSeconds(5);
        private Duration readTimeout = Duration.ofMinutes(10);
        private int maxRetries = 2;
        private Duration retryBackoff = Duration.ofSeconds(1);

        public String getBaseUrl() {
            return baseUrl;
        }

        public void setBaseUrl(String baseUrl) {
            this.baseUrl = baseUrl;
        }

        public String getInternalToken() {
            return internalToken;
        }

        public void setInternalToken(String internalToken) {
            this.internalToken = internalToken;
        }

        public Duration getConnectTimeout() {
            return connectTimeout;
        }

        public void setConnectTimeout(Duration connectTimeout) {
            this.connectTimeout = connectTimeout;
        }

        public Duration getReadTimeout() {
            return readTimeout;
        }

        public void setReadTimeout(Duration readTimeout) {
            this.readTimeout = readTimeout;
        }

        public int getMaxRetries() {
            return maxRetries;
        }

        public void setMaxRetries(int maxRetries) {
            this.maxRetries = maxRetries;
        }

        public Duration getRetryBackoff() {
            return retryBackoff;
        }

        public void setRetryBackoff(Duration retryBackoff) {
            this.retryBackoff = retryBackoff;
        }
    }

    public static class Cors {
        private List<String> allowedOrigins = new ArrayList<>(List.of(
            "http://localhost",
            "http://localhost:80",
            "http://localhost:5173",
            "http://localhost:5174"
        ));

        public List<String> getAllowedOrigins() {
            return allowedOrigins;
        }

        public void setAllowedOrigins(List<String> allowedOrigins) {
            this.allowedOrigins = allowedOrigins;
        }
    }

    public static class Security {
        private final Jwt jwt = new Jwt();

        public Jwt getJwt() {
            return jwt;
        }
    }

    public static class Jwt {
        private String issuer = "zhixue-control-plane";
        private String secret = "";
        private Duration accessTokenTtl = Duration.ofHours(2);
        private Duration refreshTokenTtl = Duration.ofDays(7);

        public String getIssuer() {
            return issuer;
        }

        public void setIssuer(String issuer) {
            this.issuer = issuer;
        }

        public Duration getAccessTokenTtl() {
            return accessTokenTtl;
        }

        public String getSecret() {
            return secret;
        }

        public void setSecret(String secret) {
            this.secret = secret;
        }

        public void setAccessTokenTtl(Duration accessTokenTtl) {
            this.accessTokenTtl = accessTokenTtl;
        }

        public Duration getRefreshTokenTtl() {
            return refreshTokenTtl;
        }

        public void setRefreshTokenTtl(Duration refreshTokenTtl) {
            this.refreshTokenTtl = refreshTokenTtl;
        }
    }

    public static class RateLimit {
        private boolean enabled = true;
        private int userRequestsPerMinute = 240;
        private int ipRequestsPerMinute = 600;

        public boolean isEnabled() {
            return enabled;
        }

        public void setEnabled(boolean enabled) {
            this.enabled = enabled;
        }

        public int getUserRequestsPerMinute() {
            return userRequestsPerMinute;
        }

        public void setUserRequestsPerMinute(int userRequestsPerMinute) {
            this.userRequestsPerMinute = userRequestsPerMinute;
        }

        public int getIpRequestsPerMinute() {
            return ipRequestsPerMinute;
        }

        public void setIpRequestsPerMinute(int ipRequestsPerMinute) {
            this.ipRequestsPerMinute = ipRequestsPerMinute;
        }
    }

    public static class Idempotency {
        private Duration ttl = Duration.ofHours(24);

        public Duration getTtl() {
            return ttl;
        }

        public void setTtl(Duration ttl) {
            this.ttl = ttl;
        }
    }

    public static class Download {
        private long artifactTtlSeconds = 1800;

        public long getArtifactTtlSeconds() {
            return artifactTtlSeconds;
        }

        public void setArtifactTtlSeconds(long artifactTtlSeconds) {
            this.artifactTtlSeconds = artifactTtlSeconds;
        }
    }

    public static class Upload {
        private long imageMaxBytes = 10 * 1024 * 1024L;
        private long imageTokenTtlSeconds = 1800;
        private String imageStorageDir = "sandbox-temp/chat-images";

        public long getImageMaxBytes() {
            return imageMaxBytes;
        }

        public void setImageMaxBytes(long imageMaxBytes) {
            this.imageMaxBytes = imageMaxBytes;
        }

        public long getImageTokenTtlSeconds() {
            return imageTokenTtlSeconds;
        }

        public void setImageTokenTtlSeconds(long imageTokenTtlSeconds) {
            this.imageTokenTtlSeconds = imageTokenTtlSeconds;
        }

        public String getImageStorageDir() {
            return imageStorageDir;
        }

        public void setImageStorageDir(String imageStorageDir) {
            this.imageStorageDir = imageStorageDir;
        }
    }

    public static class SmartEngineQueue {
        private String streamKey = "zhixue:smart-engine:tasks";
        private String dlqKey = "zhixue:smart-engine:tasks:dlq";
        private String consumerGroup = "smart-engine-python";
        private String cancelKeyPrefix = "zhixue:smart-engine:cancel:";
        private Duration cancelTtl = Duration.ofHours(24);

        public String getStreamKey() {
            return streamKey;
        }

        public void setStreamKey(String streamKey) {
            this.streamKey = streamKey;
        }

        public String getDlqKey() {
            return dlqKey;
        }

        public void setDlqKey(String dlqKey) {
            this.dlqKey = dlqKey;
        }

        public String getConsumerGroup() {
            return consumerGroup;
        }

        public void setConsumerGroup(String consumerGroup) {
            this.consumerGroup = consumerGroup;
        }

        public String getCancelKeyPrefix() {
            return cancelKeyPrefix;
        }

        public void setCancelKeyPrefix(String cancelKeyPrefix) {
            this.cancelKeyPrefix = cancelKeyPrefix;
        }

        public Duration getCancelTtl() {
            return cancelTtl;
        }

        public void setCancelTtl(Duration cancelTtl) {
            this.cancelTtl = cancelTtl;
        }
    }

    public static class Voice {
        private boolean enabled = true;
        private String provider = "bailian";
        private String apiKey = "";
        private String asrModel = "qwen3-asr-flash-realtime";
        private String asrWebsocketUrl = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime";
        private String ttsModel = "qwen3-tts-flash-realtime";
        private String ttsWebsocketUrl = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime";
        private String ttsVoice = "Cherry";
        private int sampleRate = 16000;
        private int maxAudioSeconds = 60;
        private long maxAudioBytes = 10 * 1024 * 1024L;
        private Duration connectTimeout = Duration.ofSeconds(5);
        private Duration requestTimeout = Duration.ofSeconds(90);
        private Duration sessionTtl = Duration.ofSeconds(300);
        private Duration asrPrewarmTtl = Duration.ofSeconds(60);
        private Duration ttsFirstAudioTimeout = Duration.ofSeconds(3);
        private int asrVadSilenceDurationMs = 1200;
        private double asrVadThreshold = 0.5D;
        private int providerMaxRetries = 1;
        private Duration providerRetryBackoff = Duration.ofMillis(300);
        private int maxConcurrentSessionsPerUser = 2;

        public boolean isEnabled() {
            return enabled;
        }

        public void setEnabled(boolean enabled) {
            this.enabled = enabled;
        }

        public String getProvider() {
            return provider;
        }

        public void setProvider(String provider) {
            this.provider = provider;
        }

        public String getApiKey() {
            return apiKey;
        }

        public void setApiKey(String apiKey) {
            this.apiKey = apiKey;
        }

        public String getAsrModel() {
            return asrModel;
        }

        public void setAsrModel(String asrModel) {
            this.asrModel = asrModel;
        }

        public String getAsrWebsocketUrl() {
            return asrWebsocketUrl;
        }

        public void setAsrWebsocketUrl(String asrWebsocketUrl) {
            this.asrWebsocketUrl = asrWebsocketUrl;
        }

        public String getTtsModel() {
            return ttsModel;
        }

        public void setTtsModel(String ttsModel) {
            this.ttsModel = ttsModel;
        }

        public String getTtsWebsocketUrl() {
            return ttsWebsocketUrl;
        }

        public void setTtsWebsocketUrl(String ttsWebsocketUrl) {
            this.ttsWebsocketUrl = ttsWebsocketUrl;
        }

        public String getTtsVoice() {
            return ttsVoice;
        }

        public void setTtsVoice(String ttsVoice) {
            this.ttsVoice = ttsVoice;
        }

        public int getSampleRate() {
            return sampleRate;
        }

        public void setSampleRate(int sampleRate) {
            this.sampleRate = sampleRate;
        }

        public int getMaxAudioSeconds() {
            return maxAudioSeconds;
        }

        public void setMaxAudioSeconds(int maxAudioSeconds) {
            this.maxAudioSeconds = maxAudioSeconds;
        }

        public long getMaxAudioBytes() {
            return maxAudioBytes;
        }

        public void setMaxAudioBytes(long maxAudioBytes) {
            this.maxAudioBytes = maxAudioBytes;
        }

        public Duration getConnectTimeout() {
            return connectTimeout;
        }

        public void setConnectTimeout(Duration connectTimeout) {
            this.connectTimeout = connectTimeout;
        }

        public Duration getRequestTimeout() {
            return requestTimeout;
        }

        public void setRequestTimeout(Duration requestTimeout) {
            this.requestTimeout = requestTimeout;
        }

        public Duration getSessionTtl() {
            return sessionTtl;
        }

        public void setSessionTtl(Duration sessionTtl) {
            this.sessionTtl = sessionTtl;
        }

        public Duration getAsrPrewarmTtl() {
            return asrPrewarmTtl;
        }

        public void setAsrPrewarmTtl(Duration asrPrewarmTtl) {
            this.asrPrewarmTtl = asrPrewarmTtl;
        }

        public Duration getTtsFirstAudioTimeout() {
            return ttsFirstAudioTimeout;
        }

        public void setTtsFirstAudioTimeout(Duration ttsFirstAudioTimeout) {
            this.ttsFirstAudioTimeout = ttsFirstAudioTimeout;
        }

        public int getAsrVadSilenceDurationMs() {
            return asrVadSilenceDurationMs;
        }

        public void setAsrVadSilenceDurationMs(int asrVadSilenceDurationMs) {
            this.asrVadSilenceDurationMs = asrVadSilenceDurationMs;
        }

        public double getAsrVadThreshold() {
            return asrVadThreshold;
        }

        public void setAsrVadThreshold(double asrVadThreshold) {
            this.asrVadThreshold = asrVadThreshold;
        }

        public int getProviderMaxRetries() {
            return providerMaxRetries;
        }

        public void setProviderMaxRetries(int providerMaxRetries) {
            this.providerMaxRetries = providerMaxRetries;
        }

        public Duration getProviderRetryBackoff() {
            return providerRetryBackoff;
        }

        public void setProviderRetryBackoff(Duration providerRetryBackoff) {
            this.providerRetryBackoff = providerRetryBackoff;
        }

        public int getMaxConcurrentSessionsPerUser() {
            return maxConcurrentSessionsPerUser;
        }

        public void setMaxConcurrentSessionsPerUser(int maxConcurrentSessionsPerUser) {
            this.maxConcurrentSessionsPerUser = maxConcurrentSessionsPerUser;
        }
    }
}
