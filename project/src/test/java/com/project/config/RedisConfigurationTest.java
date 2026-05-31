package com.project.config;

import com.project.application.smartengine.SmartEngineQueueService;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.boot.autoconfigure.data.redis.RedisProperties;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.core.StringRedisTemplate;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Verifies that SmartEngine queue dependencies are available at startup.
 */
class RedisConfigurationTest {

    private final ApplicationContextRunner contextRunner = new ApplicationContextRunner()
        .withBean(AppProperties.class)
        .withBean(ObjectMapper.class)
        .withUserConfiguration(RedisConfiguration.class, SmartEngineQueueService.class);

    @Test
    void createsRedisTemplateAndSmartEngineQueueByDefault() {
        contextRunner
            .withBean(RedisProperties.class)
            .run(context -> {
                assertThat(context).hasSingleBean(RedisConnectionFactory.class);
                assertThat(context).hasSingleBean(StringRedisTemplate.class);
                assertThat(context).hasSingleBean(SmartEngineQueueService.class);
            });
    }

    @Test
    void canDisableRedisInfrastructureForTests() {
        contextRunner
            .withPropertyValues("app.redis.enabled=false")
            .run(context -> {
                assertThat(context).doesNotHaveBean(RedisConnectionFactory.class);
                assertThat(context).doesNotHaveBean(StringRedisTemplate.class);
                assertThat(context).doesNotHaveBean(SmartEngineQueueService.class);
            });
    }
}
