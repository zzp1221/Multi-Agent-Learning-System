package com.project.application.resource;

import com.project.api.resource.dto.ResourceStatsResponse;
import com.project.api.resource.dto.ResourceTagResponse;

import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Supplier;

final class ResourceLibrarySummaryCache {

    private final Duration ttl;
    private final Map<UUID, CacheEntry<ResourceStatsResponse>> statsCache = new ConcurrentHashMap<>();
    private final Map<TagCacheKey, CacheEntry<List<ResourceTagResponse>>> tagsCache = new ConcurrentHashMap<>();

    ResourceLibrarySummaryCache(Duration ttl) {
        this.ttl = ttl;
    }

    ResourceStatsResponse stats(UUID userId, Supplier<ResourceStatsResponse> loader) {
        CacheEntry<ResourceStatsResponse> cached = statsCache.get(userId);
        if (cached != null && cached.isFresh()) {
            return cached.value();
        }
        ResourceStatsResponse response = loader.get();
        statsCache.put(userId, new CacheEntry<>(response, Instant.now().plus(ttl)));
        return response;
    }

    List<ResourceTagResponse> tags(UUID userId, int limit, Supplier<List<ResourceTagResponse>> loader) {
        TagCacheKey cacheKey = new TagCacheKey(userId, limit);
        CacheEntry<List<ResourceTagResponse>> cached = tagsCache.get(cacheKey);
        if (cached != null && cached.isFresh()) {
            return cached.value();
        }
        List<ResourceTagResponse> response = List.copyOf(loader.get());
        tagsCache.put(cacheKey, new CacheEntry<>(response, Instant.now().plus(ttl)));
        return response;
    }

    void evictUser(UUID userId) {
        if (userId == null) {
            return;
        }
        statsCache.remove(userId);
        tagsCache.keySet().removeIf(key -> userId.equals(key.userId()));
    }

    private record CacheEntry<T>(T value, Instant expiresAt) {
        boolean isFresh() {
            return Instant.now().isBefore(expiresAt);
        }
    }

    private record TagCacheKey(UUID userId, int limit) {
    }
}
