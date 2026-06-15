package com.project.security;

import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.Instant;
import java.util.concurrent.ConcurrentHashMap;

@Service
@ConditionalOnMissingBean(name = "stringRedisTemplate")
public class InMemoryRevokedTokenStore implements RevokedTokenStore {

    private final ConcurrentHashMap<String, Instant> revokedTokenExpirations = new ConcurrentHashMap<>();

    @Override
    public void revoke(String token, Duration ttl) {
        if (ttl == null || ttl.isZero() || ttl.isNegative()) {
            return;
        }
        evictExpired();
        revokedTokenExpirations.put(TokenFingerprint.sha256(token), Instant.now().plus(ttl));
    }

    @Override
    public boolean isRevoked(String token) {
        evictExpired();
        Instant expiresAt = revokedTokenExpirations.get(TokenFingerprint.sha256(token));
        if (expiresAt == null) {
            return false;
        }
        if (Instant.now().isAfter(expiresAt)) {
            revokedTokenExpirations.remove(TokenFingerprint.sha256(token), expiresAt);
            return false;
        }
        return true;
    }

    private void evictExpired() {
        Instant now = Instant.now();
        revokedTokenExpirations.entrySet().removeIf(entry -> now.isAfter(entry.getValue()));
    }
}
