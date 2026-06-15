package com.project.security;

import com.project.config.AppProperties;
import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.io.Decoders;
import io.jsonwebtoken.security.Keys;
import org.springframework.stereotype.Component;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.Date;
import java.util.Map;
import java.util.UUID;

/**
 * 控制平面的 JWT 签发与解析服务。
 *
 * <p>将令牌创建逻辑与控制器隔离，便于后续扩展刷新令牌轮换、
 * 非对称签名等功能，无需重写端点逻辑。</p>
 */
@Component
public class JwtProvider {

    private static final String INSECURE_DEFAULT_SECRET = "change-this-development-secret-to-a-strong-32-byte-key";

    private final AppProperties appProperties;
    private final SecretKey signingKey;

    public JwtProvider(AppProperties appProperties) {
        this.appProperties = appProperties;
        this.signingKey = buildSigningKey(appProperties.getSecurity().getJwt().getSecret());
    }

    public String generateAccessToken(UUID userId, String loginId, String role) {
        Instant now = Instant.now();
        Instant expiresAt = now.plus(appProperties.getSecurity().getJwt().getAccessTokenTtl());

        return Jwts.builder()
            .issuer(appProperties.getSecurity().getJwt().getIssuer())
            .subject(userId.toString())
            .id(UUID.randomUUID().toString())
            .claims(Map.of(
                "loginId", loginId,
                "role", role
            ))
            .issuedAt(Date.from(now))
            .expiration(Date.from(expiresAt))
            .signWith(signingKey)
            .compact();
    }

    public JwtAuthenticatedUser parse(String token) {
        Claims claims = parseClaims(token);

        return new JwtAuthenticatedUser(
            UUID.fromString(claims.getSubject()),
            claims.get("loginId", String.class),
            claims.get("role", String.class)
        );
    }

    public String issueToken(String subject, Map<String, ?> claims, Instant expiresAt) {
        Instant now = Instant.now();
        var builder = Jwts.builder()
            .issuer(appProperties.getSecurity().getJwt().getIssuer())
            .subject(subject)
            .id(UUID.randomUUID().toString())
            .issuedAt(Date.from(now))
            .expiration(Date.from(expiresAt));
        if (claims != null && !claims.isEmpty()) {
            builder.claims(claims);
        }
        return builder
            .signWith(signingKey)
            .compact();
    }

    public Claims parseClaims(String token) {
        return Jwts.parser()
            .verifyWith(signingKey)
            .build()
            .parseSignedClaims(token)
            .getPayload();
    }

    public Duration remainingTtl(String token) {
        Date expiration = parseClaims(token).getExpiration();
        if (expiration == null) {
            return Duration.ZERO;
        }
        return Duration.between(Instant.now(), expiration.toInstant());
    }

    private SecretKey buildSigningKey(String configuredSecret) {
        String secret = configuredSecret == null ? "" : configuredSecret.trim();
        if (secret.isEmpty() || INSECURE_DEFAULT_SECRET.equals(secret)) {
            throw new IllegalStateException("APP_JWT_SECRET must be configured with a strong secret");
        }

        if (secret.matches("^[A-Za-z0-9+/=]+$") && secret.length() >= 44) {
            try {
                byte[] decoded = Decoders.BASE64.decode(secret);
                if (decoded.length >= 32) {
                    return Keys.hmacShaKeyFor(decoded);
                }
            } catch (IllegalArgumentException ignored) {
                // 回退到原始字节处理明文开发密钥。
            }
        }

        byte[] rawBytes = secret.getBytes(StandardCharsets.UTF_8);
        if (rawBytes.length < 32) {
            throw new IllegalStateException("APP_JWT_SECRET must be at least 32 bytes long");
        }
        return Keys.hmacShaKeyFor(rawBytes);
    }
}
