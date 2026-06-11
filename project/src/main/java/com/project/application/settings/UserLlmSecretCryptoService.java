package com.project.application.settings;

import com.project.application.common.ApplicationException;
import com.project.config.AppProperties;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Base64;

@Service
public class UserLlmSecretCryptoService {

    private static final int IV_BYTES = 12;
    private static final int GCM_TAG_BITS = 128;
    private static final String CIPHER = "AES/GCM/NoPadding";
    private static final String KEY_ALGORITHM = "AES";

    private final AppProperties appProperties;
    private final SecureRandom secureRandom = new SecureRandom();

    public UserLlmSecretCryptoService(AppProperties appProperties) {
        this.appProperties = appProperties;
    }

    public String encrypt(String plaintext) {
        String normalized = plaintext == null ? "" : plaintext.trim();
        if (normalized.isEmpty()) {
            return "";
        }
        try {
            byte[] iv = new byte[IV_BYTES];
            secureRandom.nextBytes(iv);
            Cipher cipher = Cipher.getInstance(CIPHER);
            cipher.init(Cipher.ENCRYPT_MODE, keySpec(), new GCMParameterSpec(GCM_TAG_BITS, iv));
            byte[] ciphertext = cipher.doFinal(normalized.getBytes(StandardCharsets.UTF_8));
            ByteBuffer buffer = ByteBuffer.allocate(iv.length + ciphertext.length);
            buffer.put(iv);
            buffer.put(ciphertext);
            return Base64.getEncoder().encodeToString(buffer.array());
        } catch (GeneralSecurityException ex) {
            throw new ApplicationException("LLM_SECRET_ENCRYPT_FAILED", "LLM 密钥加密失败", HttpStatus.INTERNAL_SERVER_ERROR);
        }
    }

    public String decrypt(String encrypted) {
        String normalized = encrypted == null ? "" : encrypted.trim();
        if (normalized.isEmpty()) {
            return "";
        }
        try {
            byte[] payload = Base64.getDecoder().decode(normalized);
            if (payload.length <= IV_BYTES) {
                return "";
            }
            byte[] iv = new byte[IV_BYTES];
            byte[] ciphertext = new byte[payload.length - IV_BYTES];
            System.arraycopy(payload, 0, iv, 0, IV_BYTES);
            System.arraycopy(payload, IV_BYTES, ciphertext, 0, ciphertext.length);
            Cipher cipher = Cipher.getInstance(CIPHER);
            cipher.init(Cipher.DECRYPT_MODE, keySpec(), new GCMParameterSpec(GCM_TAG_BITS, iv));
            return new String(cipher.doFinal(ciphertext), StandardCharsets.UTF_8);
        } catch (IllegalArgumentException | GeneralSecurityException ex) {
            throw new ApplicationException("LLM_SECRET_DECRYPT_FAILED", "LLM 密钥解密失败", HttpStatus.INTERNAL_SERVER_ERROR);
        }
    }

    private SecretKeySpec keySpec() {
        String rawKey = appProperties.getUserLlm().getEncryptionKey();
        String normalized = rawKey == null ? "" : rawKey.trim();
        if (normalized.isEmpty()) {
            throw new ApplicationException("LLM_SECRET_KEY_NOT_CONFIGURED", "未配置用户 LLM 密钥加密 Key", HttpStatus.PRECONDITION_FAILED);
        }
        byte[] keyBytes = decodeKey(normalized);
        if (keyBytes.length != 16 && keyBytes.length != 24 && keyBytes.length != 32) {
            keyBytes = sha256(keyBytes);
        }
        return new SecretKeySpec(keyBytes, KEY_ALGORITHM);
    }

    private byte[] decodeKey(String key) {
        if (key.matches("^[A-Za-z0-9+/=]+$") && key.length() >= 24) {
            try {
                byte[] decoded = Base64.getDecoder().decode(key);
                if (decoded.length >= 16) {
                    return decoded;
                }
            } catch (IllegalArgumentException ignored) {
                // Fall back to UTF-8 bytes below.
            }
        }
        return key.getBytes(StandardCharsets.UTF_8);
    }

    private byte[] sha256(byte[] value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(value);
        } catch (GeneralSecurityException ex) {
            throw new ApplicationException("LLM_SECRET_KEY_INVALID", "LLM 密钥加密 Key 不可用", HttpStatus.INTERNAL_SERVER_ERROR);
        }
    }
}
