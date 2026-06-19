package com.project.application.smartengine;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.config.AppProperties;
import com.project.domain.task.ServiceType;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.connection.stream.MapRecord;
import org.springframework.data.redis.connection.stream.RecordId;
import org.springframework.data.redis.core.StreamOperations;
import org.springframework.data.redis.core.StringRedisTemplate;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentCaptor.forClass;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class SmartEngineQueueServiceTest {

    @Test
    void enqueueTrimsMainStreamWithConfiguredApproximateMaxLength() {
        StringRedisTemplate redisTemplate = mock(StringRedisTemplate.class);
        @SuppressWarnings("unchecked")
        StreamOperations<String, Object, Object> streamOperations = mock(StreamOperations.class);
        when(redisTemplate.opsForStream()).thenReturn(streamOperations);
        when(streamOperations.add(org.mockito.ArgumentMatchers.any(MapRecord.class))).thenReturn(RecordId.of("0-1"));

        AppProperties properties = new AppProperties();
        properties.getSmartEngineQueue().setStreamKey("test:stream");
        properties.getSmartEngineQueue().setMaxStreamLength(25);

        SmartEngineQueueService service = new SmartEngineQueueService(redisTemplate, new ObjectMapper(), properties);
        String recordId = service.enqueue(new SmartEngineInvocation(
            UUID.fromString("30000000-0000-0000-0000-000000000102"),
            UUID.fromString("30000000-0000-0000-0000-000000000101"),
            "trace-queue",
            null,
            ServiceType.RESOURCE_GENERATION,
            Map.of("resourceType", "DOCUMENT")
        ));

        assertThat(recordId).isEqualTo("0-1");
        verify(streamOperations).trim("test:stream", 25, true);
    }

    @Test
    void enqueuePreservesChineseParamsInParamsJson() throws Exception {
        StringRedisTemplate redisTemplate = mock(StringRedisTemplate.class);
        @SuppressWarnings("unchecked")
        StreamOperations<String, Object, Object> streamOperations = mock(StreamOperations.class);
        when(redisTemplate.opsForStream()).thenReturn(streamOperations);
        when(streamOperations.add(any(MapRecord.class))).thenReturn(RecordId.of("0-2"));

        AppProperties properties = new AppProperties();
        properties.getSmartEngineQueue().setStreamKey("test:stream");
        properties.getSmartEngineQueue().setMaxStreamLength(0);
        ObjectMapper objectMapper = new ObjectMapper();
        SmartEngineQueueService service = new SmartEngineQueueService(redisTemplate, objectMapper, properties);
        Map<String, Object> params = new LinkedHashMap<>();
        params.put("course", "\u6570\u636e\u5e93\u7cfb\u7edf");
        params.put("topic", "\u8054\u5408\u7d22\u5f15\u7684\u6700\u5de6\u5339\u914d");
        params.put("query", "\u8bf7\u751f\u6210\u8054\u5408\u7d22\u5f15\u5bfc\u5b66\u6587\u6863");

        service.enqueue(new SmartEngineInvocation(
            UUID.fromString("30000000-0000-0000-0000-000000000202"),
            UUID.fromString("30000000-0000-0000-0000-000000000201"),
            "trace-queue-chinese",
            null,
            ServiceType.RESOURCE_GENERATION,
            params
        ));

        @SuppressWarnings("unchecked")
        org.mockito.ArgumentCaptor<MapRecord<String, Object, Object>> captor = forClass(MapRecord.class);
        verify(streamOperations).add(captor.capture());
        Object paramsJson = captor.getValue().getValue().get("paramsJson");
        @SuppressWarnings("unchecked")
        Map<String, Object> decoded = objectMapper.readValue(String.valueOf(paramsJson), Map.class);
        assertThat(decoded)
            .containsEntry("course", "\u6570\u636e\u5e93\u7cfb\u7edf")
            .containsEntry("topic", "\u8054\u5408\u7d22\u5f15\u7684\u6700\u5de6\u5339\u914d")
            .containsEntry("query", "\u8bf7\u751f\u6210\u8054\u5408\u7d22\u5f15\u5bfc\u5b66\u6587\u6863");
        assertThat(String.valueOf(paramsJson)).doesNotContain("????");
    }
}
