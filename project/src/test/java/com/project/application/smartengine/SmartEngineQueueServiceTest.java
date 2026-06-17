package com.project.application.smartengine;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.config.AppProperties;
import com.project.domain.task.ServiceType;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.connection.stream.MapRecord;
import org.springframework.data.redis.connection.stream.RecordId;
import org.springframework.data.redis.core.StreamOperations;
import org.springframework.data.redis.core.StringRedisTemplate;

import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
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
}
