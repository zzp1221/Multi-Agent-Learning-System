package com.project.application.resource;

import com.project.api.resource.dto.ResourceSemanticHitResponse;

import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

final class ResourceLibraryDisplaySanitizer {

    private static final double WIKI_BOUND_LEXICAL_CONFIDENCE_MIN = 0.60;
    private static final Set<String> INTERNAL_RESOURCE_TAGS = Set.of(
        "existing-web-match",
        "metadata-search-fallback",
        "resource_library_web_search_fallback",
        "wiki-bound-resource"
    );
    private static final List<String> INTERNAL_SUMMARY_PREFIXES = List.of(
        "Metadata-only URL candidate matched to wiki topic",
        "Metadata-only URL candidate"
    );
    private static final Set<String> PUBLIC_METADATA_KEYS = Set.of(
        "noteId"
    );
    private static final Pattern GENERIC_LEXICAL_SCORE_PATTERN = Pattern.compile(
        "generic lexical score\\s+([0-9]+(?:[.][0-9]+)?)",
        Pattern.CASE_INSENSITIVE
    );

    Set<String> internalResourceTags() {
        return INTERNAL_RESOURCE_TAGS;
    }

    String displayType(String resourceType, Map<String, Object> metadata) {
        String displayType = readString(metadata, "displayType");
        if (displayType != null && !displayType.isBlank()) {
            return displayType;
        }
        return switch (resourceType == null ? "" : resourceType) {
            case "VIDEO" -> "VIDEO";
            case "QUIZ", "PRACTICE" -> "QUIZ";
            case "CODE" -> "CASE";
            case "MINDMAP" -> "NOTE";
            case "SLIDES", "PPT" -> "COURSE";
            default -> "DOCUMENT";
        };
    }

    String displayTitle(String rawTitle, Map<String, Object> metadata) {
        String title = stripWrappingQuotes(rawTitle == null ? "" : rawTitle.trim());
        if (!isPlaceholderTitle(title)) {
            return title;
        }
        String metadataTitle = stripWrappingQuotes(readString(metadata, "title", "sourceTitle", "pageTitle"));
        if (metadataTitle != null && !isPlaceholderTitle(metadataTitle)) {
            return metadataTitle;
        }
        String derivedTitle = deriveTitleFromUrl(readString(metadata, "sourceUrl", "originalUrl", "url"));
        if (derivedTitle == null || derivedTitle.isBlank()) {
            return title.isBlank() ? "Learning resource" : title;
        }
        String sourceName = readString(metadata, "sourceName");
        if (sourceName != null && sourceName.toLowerCase(Locale.ROOT).contains("pytorch")
            && !derivedTitle.toLowerCase(Locale.ROOT).contains("pytorch")) {
            return "PyTorch: " + derivedTitle;
        }
        return derivedTitle;
    }

    String displaySummary(String rawSummary) {
        String summary = safeString(rawSummary);
        if (summary.isBlank()) {
            return "";
        }
        String normalized = summary.toLowerCase(Locale.ROOT);
        boolean internalSummary = INTERNAL_SUMMARY_PREFIXES.stream()
            .map(prefix -> prefix.toLowerCase(Locale.ROOT))
            .anyMatch(normalized::startsWith);
        return internalSummary ? "" : summary;
    }

    Map<String, Object> displayMetadata(Map<String, Object> metadata) {
        if (metadata.isEmpty()) {
            return metadata;
        }
        Map<String, Object> displayMetadata = new LinkedHashMap<>();
        for (Map.Entry<String, Object> entry : metadata.entrySet()) {
            if (PUBLIC_METADATA_KEYS.contains(entry.getKey())) {
                displayMetadata.put(entry.getKey(), entry.getValue());
            }
        }
        return displayMetadata;
    }

    List<String> displayTags(List<String> tags, Map<String, Object> metadata, String rawSummary) {
        if (tags.isEmpty()) {
            return tags;
        }
        boolean lowConfidenceWikiBound = isLowConfidenceWikiBound(metadata, rawSummary);
        Set<String> blockedTags = normalizedTagSet(INTERNAL_RESOURCE_TAGS);
        if (lowConfidenceWikiBound) {
            addBlockedTag(blockedTags, metadata.get("wikiTitle"));
            Object aliases = metadata.get("wikiAliases");
            if (aliases instanceof Iterable<?> values) {
                values.forEach(value -> addBlockedTag(blockedTags, value));
            }
        }
        return tags.stream()
            .filter(tag -> tag != null && !tag.isBlank())
            .filter(tag -> !blockedTags.contains(normalizeTagForDisplayFilter(tag)))
            .toList();
    }

    List<ResourceSemanticHitResponse> displaySemanticHits(List<ResourceSemanticHitResponse> hits) {
        if (hits == null || hits.isEmpty()) {
            return List.of();
        }
        return hits.stream()
            .map(hit -> new ResourceSemanticHitResponse(
                hit.chunkId(),
                hit.chunkNo(),
                hit.similarity(),
                displaySemanticHitContent(hit.content()),
                hit.sourceUrl()
            ))
            .toList();
    }

    String deriveTitleFromUrl(String url) {
        if (url == null || url.isBlank()) {
            return null;
        }
        try {
            String path = java.net.URI.create(url.trim()).getPath();
            if (path == null || path.isBlank()) {
                return null;
            }
            String segment = path.substring(path.lastIndexOf('/') + 1);
            if (segment.isBlank()) {
                return null;
            }
            int dotIndex = segment.lastIndexOf('.');
            if (dotIndex > 0) {
                segment = segment.substring(0, dotIndex);
            }
            String decoded = URLDecoder.decode(segment, StandardCharsets.UTF_8);
            return decoded.replace('-', ' ').replace('_', ' ').trim();
        } catch (IllegalArgumentException ex) {
            return null;
        }
    }

    String readString(Map<String, Object> map, String... keys) {
        for (String key : keys) {
            Object value = map.get(key);
            if (value != null && !String.valueOf(value).isBlank()) {
                return String.valueOf(value).trim();
            }
        }
        return null;
    }

    private String displaySemanticHitContent(String content) {
        if (content == null || content.isBlank()) {
            return "";
        }
        List<String> lines = content.lines()
            .map(this::displaySemanticHitLine)
            .filter(line -> line != null && !line.isBlank())
            .toList();
        return String.join("\n", lines);
    }

    private String displaySemanticHitLine(String line) {
        String trimmed = line.trim();
        String normalized = trimmed.toLowerCase(Locale.ROOT);
        if (normalized.startsWith("wiki slug:") || normalized.startsWith("aliases:")) {
            return "";
        }
        if (normalized.startsWith("wiki title:")) {
            return "Topic: " + stripWrappingQuotes(trimmed.substring("wiki title:".length()).trim());
        }
        if (normalized.startsWith("wiki summary:")) {
            return "Topic summary: " + trimmed.substring("wiki summary:".length()).trim();
        }
        if (normalized.startsWith("tags:")) {
            String cleanedTags = displayTagLine(trimmed.substring("tags:".length()));
            return cleanedTags.isBlank() ? "" : "Tags: " + cleanedTags;
        }
        return line;
    }

    private String displayTagLine(String rawTags) {
        if (rawTags == null || rawTags.isBlank()) {
            return "";
        }
        Set<String> blockedTags = normalizedTagSet(INTERNAL_RESOURCE_TAGS);
        List<String> cleaned = Arrays.stream(rawTags.split(","))
            .map(String::trim)
            .filter(tag -> !tag.isBlank())
            .filter(tag -> !blockedTags.contains(normalizeTagForDisplayFilter(tag)))
            .toList();
        return String.join(", ", cleaned);
    }

    private void addBlockedTag(Set<String> blockedTags, Object value) {
        if (value == null) {
            return;
        }
        String text = normalizeTagForDisplayFilter(String.valueOf(value));
        if (!text.isBlank()) {
            blockedTags.add(text);
        }
    }

    private Set<String> normalizedTagSet(Set<String> tags) {
        Set<String> normalized = new LinkedHashSet<>();
        for (String tag : tags) {
            String value = normalizeTagForDisplayFilter(tag);
            if (!value.isBlank()) {
                normalized.add(value);
            }
        }
        return normalized;
    }

    private String normalizeTagForDisplayFilter(String value) {
        String unwrapped = stripWrappingQuotes(value);
        return unwrapped == null ? "" : unwrapped.trim().toLowerCase(Locale.ROOT);
    }

    private boolean isLowConfidenceWikiBound(Map<String, Object> metadata, String rawSummary) {
        if ("LOW_CONFIDENCE_DROPPED".equals(readString(metadata, "wikiBindingStatus"))) {
            return true;
        }
        if (!"wiki_resource_importer".equals(readString(metadata, "ingestedBy"))) {
            return false;
        }
        Double score = genericLexicalScore(rawSummary);
        return score != null && score < WIKI_BOUND_LEXICAL_CONFIDENCE_MIN;
    }

    private Double genericLexicalScore(String rawSummary) {
        if (rawSummary == null || rawSummary.isBlank()) {
            return null;
        }
        Matcher matcher = GENERIC_LEXICAL_SCORE_PATTERN.matcher(rawSummary);
        if (!matcher.find()) {
            return null;
        }
        try {
            return Double.parseDouble(matcher.group(1));
        } catch (NumberFormatException ex) {
            return null;
        }
    }

    private String stripWrappingQuotes(String value) {
        if (value == null) {
            return null;
        }
        String current = value.trim();
        boolean changed = true;
        while (changed && current.length() >= 2) {
            changed = false;
            if ((current.startsWith("\"") && current.endsWith("\""))
                || (current.startsWith("'") && current.endsWith("'"))
                || (current.startsWith("\u201c") && current.endsWith("\u201d"))
                || (current.startsWith("\u300c") && current.endsWith("\u300d"))) {
                current = current.substring(1, current.length() - 1).trim();
                changed = true;
            }
        }
        return current;
    }

    private boolean isPlaceholderTitle(String title) {
        if (title == null || title.isBlank()) {
            return true;
        }
        String normalized = title.trim().toLowerCase(Locale.ROOT);
        return normalized.equals("redirecting...")
            || normalized.equals("redirecting\u2026")
            || normalized.equals("untitled resource");
    }

    private String safeString(String value) {
        return value == null ? "" : value.trim();
    }
}
