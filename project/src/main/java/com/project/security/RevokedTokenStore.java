package com.project.security;

import java.time.Duration;

public interface RevokedTokenStore {

    void revoke(String token, Duration ttl);

    boolean isRevoked(String token);
}
