package com.project.application.resource;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.resource.dto.ResourceExternalCandidateResponse;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.Locale;

final class ResourceLibraryExternalResourceUpserter {

    private static final UUID UUID_NAMESPACE_URL = UUID.fromString("6ba7b811-9dad-11d1-80b4-00c04fd430c8");
    private static final UUID EXTERNAL_RESOURCE_NAMESPACE = uuid5(UUID_NAMESPACE_URL, "zhixue-ai-resource-library");

    private final NamedParameterJdbcTemplate jdbcTemplate;
    private final ObjectMapper objectMapper;
    private final ResourceLibraryDisplaySanitizer displaySanitizer;

    ResourceLibraryExternalResourceUpserter(
        NamedParameterJdbcTemplate jdbcTemplate,
        ObjectMapper objectMapper,
        ResourceLibraryDisplaySanitizer displaySanitizer
    ) {
        this.jdbcTemplate = jdbcTemplate;
        this.objectMapper = objectMapper;
        this.displaySanitizer = displaySanitizer;
    }

    UUID upsert(ResourceExternalCandidateResponse externalResource) {
        String sourceUrl = normalizeUrl(externalResource.sourceUrl());
        UUID resourceId = resourceUuid(sourceUrl);
        String resourceType = normalizeExternalResourceType(externalResource.resourceType(), externalResource.displayType());
        String displayType = normalizeDisplayType(externalResource.displayType(), resourceType);
        String difficultyLevel = normalizeDifficulty(externalResource.difficultyLevel());
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("sourceUrl", sourceUrl);
        metadata.put("originalUrl", sourceUrl);
        metadata.put("sourceName", safeString(externalResource.sourceName()));
        metadata.put("displayType", displayType);
        metadata.put("accessibilityStatus", "ACCESSIBLE");
        metadata.put("copyrightStatus", "LINK_ONLY");
        metadata.put("license", "");
        metadata.put("qualityScore", clampScore(externalResource.qualityScore(), 0.6));
        metadata.put("popularityScore", clampScore(externalResource.popularityScore(), 0.0));
        metadata.put("coverUrl", safeString(externalResource.coverUrl()));
        metadata.put("discoveredBy", "web_search");
        metadata.put("ingestedBy", "resource_library_web_search_fallback");
        metadata.put("ragReady", false);
        metadata.put("ragStatus", "METADATA_ONLY");

        jdbcTemplate.update(
            """
            INSERT INTO app.learning_resource (
              id, title, domain, resource_type, difficulty_level, source_kind,
              access_scope, summary_text, tags, metadata_json, status
            )
            VALUES (
              :resourceId, :title, 'COMPUTER_SCIENCE', :resourceType::app.resource_type,
              :difficultyLevel::app.difficulty_level, 'WEB'::app.source_kind,
              'GLOBAL'::app.access_scope, :summaryText, :tags::jsonb, :metadata::jsonb, 'ACTIVE'
            )
            ON CONFLICT (id) DO UPDATE SET
              title = EXCLUDED.title,
              domain = EXCLUDED.domain,
              resource_type = EXCLUDED.resource_type,
              difficulty_level = EXCLUDED.difficulty_level,
              source_kind = EXCLUDED.source_kind,
              access_scope = EXCLUDED.access_scope,
              summary_text = EXCLUDED.summary_text,
              tags = EXCLUDED.tags,
              metadata_json = app.learning_resource.metadata_json || EXCLUDED.metadata_json,
              status = 'ACTIVE',
              updated_at = now()
            """,
            new MapSqlParameterSource()
                .addValue("resourceId", resourceId)
                .addValue("title", safeTitle(externalResource.title(), sourceUrl))
                .addValue("resourceType", resourceType)
                .addValue("difficultyLevel", difficultyLevel)
                .addValue("summaryText", safeString(externalResource.summaryText()))
                .addValue("tags", toJson(externalResource.tags() == null ? List.of() : externalResource.tags()))
                .addValue("metadata", toJson(metadata))
        );
        return resourceId;
    }

    private UUID resourceUuid(String sourceUrl) {
        return uuid5(EXTERNAL_RESOURCE_NAMESPACE, "resource:" + sourceUrl);
    }

    private static UUID uuid5(UUID namespace, String name) {
        try {
            MessageDigest sha1 = MessageDigest.getInstance("SHA-1");
            ByteBuffer namespaceBytes = ByteBuffer.allocate(16);
            namespaceBytes.putLong(namespace.getMostSignificantBits());
            namespaceBytes.putLong(namespace.getLeastSignificantBits());
            sha1.update(namespaceBytes.array());
            byte[] hash = sha1.digest(name.getBytes(StandardCharsets.UTF_8));
            hash[6] &= 0x0f;
            hash[6] |= 0x50;
            hash[8] &= 0x3f;
            hash[8] |= (byte) 0x80;
            ByteBuffer buffer = ByteBuffer.wrap(hash, 0, 16);
            return new UUID(buffer.getLong(), buffer.getLong());
        } catch (NoSuchAlgorithmException ex) {
            throw new IllegalStateException("SHA-1 digest is unavailable", ex);
        }
    }

    private String toJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException ex) {
            return "{}";
        }
    }

    private String normalizeUrl(String sourceUrl) {
        String normalized = safeString(sourceUrl);
        while (normalized.endsWith("/")) {
            normalized = normalized.substring(0, normalized.length() - 1);
        }
        return normalized;
    }

    private String normalizeExternalResourceType(String resourceType, String displayType) {
        String normalized = safeString(resourceType).toUpperCase(Locale.ROOT);
        if (normalized.equals("PRACTICAL_CASE") || normalized.equals("CODE_CASE") || safeString(displayType).equalsIgnoreCase("CASE")) {
            return "CODE";
        }
        if (normalized.equals("COURSE")) {
            return "READING";
        }
        if (List.of("DOCUMENT", "PPT", "QUIZ", "VIDEO", "AUDIO", "IMAGE", "CODE", "MINDMAP", "READING", "PRACTICE", "SLIDES", "VIDEO_SCRIPT")
            .contains(normalized)) {
            return normalized;
        }
        return "READING";
    }

    private String normalizeDisplayType(String displayType, String resourceType) {
        String normalized = safeString(displayType).toUpperCase(Locale.ROOT);
        if (!normalized.isBlank()) {
            return normalized;
        }
        return displaySanitizer.displayType(resourceType, Map.of());
    }

    private String normalizeDifficulty(String difficultyLevel) {
        String normalized = safeString(difficultyLevel).toUpperCase(Locale.ROOT);
        return List.of("BASIC", "INTERMEDIATE", "ADVANCED", "MIXED").contains(normalized) ? normalized : "MIXED";
    }

    private String safeTitle(String title, String sourceUrl) {
        String normalized = safeString(title);
        if (!normalized.isBlank()) {
            return normalized;
        }
        String derived = displaySanitizer.deriveTitleFromUrl(sourceUrl);
        return derived == null || derived.isBlank() ? "Learning resource" : derived;
    }

    private String safeString(String value) {
        return value == null ? "" : value.trim();
    }

    private double clampScore(Double value, double fallback) {
        double score = value == null ? fallback : value;
        if (Double.isNaN(score) || Double.isInfinite(score)) {
            return fallback;
        }
        return Math.max(0.0, Math.min(1.0, score));
    }
}
