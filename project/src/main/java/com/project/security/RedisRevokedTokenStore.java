package com.project.security;

import org.springframework.boot.autoconfigure.condition.ConditionalOnBean;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.time.Duration;

@Service
@ConditionalOnBean(StringRedisTemplate.class)
public class RedisRevokedTokenStore implements RevokedTokenStore {

    private static final String KEY_PREFIX = "auth:revoked-token:";

    private final StringRedisTemplate redisTemplate;

    public RedisRevokedTokenStore(StringRedisTemplate redisTemplate) {
        this.redisTemplate = redisTemplate;
    }

    @Override
    public void revoke(String token, Duration ttl) {
        if (ttl == null || ttl.isZero() || ttl.isNegative()) {
            return;
        }
        redisTemplate.opsForValue().set(composeKey(token), "1", ttl);
    }

    @Override
    public boolean isRevoked(String token) {
        return Boolean.TRUE.equals(redisTemplate.hasKey(composeKey(token)));
    }

    private String composeKey(String token) {
        return KEY_PREFIX + TokenFingerprint.sha256(token);
    }
}
