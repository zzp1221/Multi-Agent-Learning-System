package com.project.application.smartengine;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.config.AppProperties;
import org.springframework.boot.autoconfigure.condition.ConditionalOnBean;
import org.springframework.data.redis.connection.stream.RecordId;
import org.springframework.data.redis.connection.stream.StreamRecords;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * Redis Streams producer for SmartEngine long-running tasks.
 */
@Service
@ConditionalOnBean(StringRedisTemplate.class)
public class SmartEngineQueueService {

    private final StringRedisTemplate redisTemplate;
    private final ObjectMapper objectMapper;
    private final AppProperties appProperties;

    public SmartEngineQueueService(
        StringRedisTemplate redisTemplate,
        ObjectMapper objectMapper,
        AppProperties appProperties
    ) {
        this.redisTemplate = redisTemplate;
        this.objectMapper = objectMapper;
        this.appProperties = appProperties;
    }

    public String enqueue(SmartEngineInvocation invocation) {
        Map<String, String> fields = new LinkedHashMap<>();
        fields.put("taskId", invocation.taskId().toString());
        fields.put("traceId", invocation.traceId());
        fields.put("userId", invocation.userId().toString());
        fields.put("conversationId", invocation.conversationId() == null ? "" : invocation.conversationId().toString());
        fields.put("serviceType", invocation.serviceType().value());
        fields.put("paramsJson", serializeParams(invocation.params()));
        fields.put("createdAt", OffsetDateTime.now().toString());

        RecordId recordId = redisTemplate.opsForStream().add(
            StreamRecords.mapBacked(fields).withStreamKey(appProperties.getSmartEngineQueue().getStreamKey())
        );
        if (recordId == null) {
            throw new IllegalStateException("Redis XADD returned no record id");
        }
        return recordId.getValue();
    }

    public void markCancelled(UUID taskId) {
        redisTemplate.opsForValue().set(
            cancelKey(taskId),
            "1",
            appProperties.getSmartEngineQueue().getCancelTtl()
        );
    }

    private String cancelKey(UUID taskId) {
        return appProperties.getSmartEngineQueue().getCancelKeyPrefix() + taskId;
    }

    private String serializeParams(Map<String, Object> params) {
        try {
            return objectMapper.writeValueAsString(params == null ? Map.of() : params);
        } catch (JsonProcessingException ex) {
            throw new IllegalArgumentException("Failed to serialize SmartEngine task params", ex);
        }
    }
}
