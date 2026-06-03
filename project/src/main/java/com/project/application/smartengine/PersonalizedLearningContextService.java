package com.project.application.smartengine;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.domain.profile.UserProfileCurrent;
import com.project.domain.profile.UserProfileCurrentRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.sql.Timestamp;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

/**
 * Builds server-side context for personalized learning tasks from persisted learner signals.
 */
@Service
public class PersonalizedLearningContextService {

    private static final Logger log = LoggerFactory.getLogger(PersonalizedLearningContextService.class);
    private static final int CONTEXT_DAYS = 30;
    private static final int KNOWLEDGE_NODE_LIMIT = 12;
    private static final int RECENT_MISTAKE_LIMIT = 5;
    private static final int MISTAKE_TAG_LIMIT = 8;

    private static final String KNOWLEDGE_NODE_SQL = """
        SELECT canonical_key, topic, mastery_score, node_status, source, updated_at
        FROM app.learner_knowledge_node
        WHERE user_id = :userId
        ORDER BY CASE node_status
                   WHEN 'WEAK' THEN 0
                   WHEN 'IN_PROGRESS' THEN 1
                   WHEN 'NOT_STARTED' THEN 2
                   WHEN 'MASTERED' THEN 3
                   ELSE 4
                 END,
                 mastery_score ASC,
                 updated_at DESC
        LIMIT :limit
        """;

    private static final String PRACTICE_SUMMARY_SQL = """
        SELECT COUNT(*) AS submission_count,
               COALESCE(SUM(CASE WHEN is_correct IS TRUE THEN 1 ELSE 0 END), 0) AS correct_count,
               COALESCE(SUM(CASE WHEN is_correct IS FALSE THEN 1 ELSE 0 END), 0) AS incorrect_count,
               MAX(submitted_at) AS last_submitted_at
        FROM app.practice_submission
        WHERE user_id = :userId
          AND submitted_at >= :fromAt
          AND submitted_at < :toAt
        """;

    private static final String EVALUATION_TASK_SQL = """
        SELECT service_type::text AS service_type,
               task_status::text AS task_status,
               COUNT(*) AS task_count
        FROM app.smart_engine_task
        WHERE user_id = :userId
          AND created_at >= :fromAt
          AND created_at < :toAt
          AND service_type IN ('PRACTICE_JUDGE', 'EVALUATION', 'LEARNING_EVALUATION')
        GROUP BY service_type::text, task_status::text
        ORDER BY service_type::text, task_status::text
        """;

    private static final String MISTAKE_SUMMARY_SQL = """
        SELECT COUNT(*) FILTER (WHERE mastered IS FALSE) AS unmastered_count,
               COUNT(*) FILTER (WHERE mastered IS FALSE AND next_review_at <= now()) AS due_review_count,
               COUNT(*) AS total_mistake_count,
               COALESCE(SUM(wrong_count), 0) AS wrong_count,
               COALESCE(SUM(review_count), 0) AS review_count,
               MAX(last_wrong_at) AS last_wrong_at
        FROM app.mistake_record
        WHERE user_id = :userId
        """;

    private static final String RECENT_MISTAKE_SQL = """
        SELECT id::text AS id,
               knowledge_tags::text AS knowledge_tags,
               difficulty_level::text AS difficulty_level,
               mistake_type,
               wrong_count,
               review_count,
               mastered,
               next_review_at,
               last_wrong_at
        FROM app.mistake_record
        WHERE user_id = :userId
          AND mastered IS FALSE
        ORDER BY next_review_at ASC, wrong_count DESC, last_wrong_at DESC
        LIMIT :limit
        """;

    private static final String TOP_MISTAKE_TAG_SQL = """
        SELECT tag.topic AS topic,
               COUNT(*) AS mistake_count,
               COALESCE(SUM(r.wrong_count), 0) AS wrong_count
        FROM app.mistake_record r
        CROSS JOIN LATERAL jsonb_array_elements_text(r.knowledge_tags) AS tag(topic)
        WHERE r.user_id = :userId
        GROUP BY tag.topic
        ORDER BY wrong_count DESC, mistake_count DESC, tag.topic ASC
        LIMIT :limit
        """;

    private static final String REVIEW_SUMMARY_SQL = """
        SELECT COUNT(*) AS review_count,
               COALESCE(SUM(CASE WHEN is_correct IS TRUE THEN 1 ELSE 0 END), 0) AS correct_review_count,
               AVG(quality) AS average_quality,
               MAX(reviewed_at) AS last_reviewed_at
        FROM app.mistake_review_result
        WHERE user_id = :userId
          AND reviewed_at >= :fromAt
          AND reviewed_at < :toAt
        """;

    private static final String RESOURCE_REQUEST_SQL = """
        SELECT resource_type,
               COUNT(*) AS request_count,
               MAX(created_at) AS last_used_at
        FROM (
            SELECT CASE
                     WHEN service_type = 'VIDEO_GENERATION' THEN 'VIDEO'
                     WHEN service_type = 'PRACTICE_JUDGE' THEN 'QUIZ'
                     ELSE COALESCE(
                       NULLIF(request_payload #>> '{params,resourceType}', ''),
                       NULLIF(request_payload #>> '{params,preferredType}', ''),
                       NULLIF(request_payload #>> '{params,resource_type}', ''),
                       NULLIF(request_payload #>> '{params,preferred_type}', ''),
                       NULLIF(request_payload ->> 'resourceType', ''),
                       NULLIF(request_payload ->> 'preferredType', ''),
                       NULLIF(request_payload ->> 'resource_type', ''),
                       NULLIF(request_payload ->> 'preferred_type', '')
                     )
                   END AS resource_type,
                   created_at
            FROM app.smart_engine_task
            WHERE user_id = :userId
              AND created_at >= :fromAt
              AND created_at < :toAt
        ) preference_source
        WHERE resource_type IS NOT NULL
          AND resource_type <> ''
        GROUP BY resource_type
        """;

    private static final String RESOURCE_ARTIFACT_SQL = """
        SELECT resource_type::text AS resource_type,
               COUNT(*) AS generated_count,
               COALESCE(SUM(download_count), 0) AS download_count,
               MAX(created_at) AS last_used_at
        FROM app.generated_artifact
        WHERE user_id = :userId
          AND created_at >= :fromAt
          AND created_at < :toAt
        GROUP BY resource_type::text
        """;

    private final NamedParameterJdbcTemplate jdbcTemplate;
    private final UserProfileCurrentRepository userProfileCurrentRepository;
    private final ObjectMapper objectMapper;

    public PersonalizedLearningContextService(
        NamedParameterJdbcTemplate jdbcTemplate,
        UserProfileCurrentRepository userProfileCurrentRepository,
        ObjectMapper objectMapper
    ) {
        this.jdbcTemplate = jdbcTemplate;
        this.userProfileCurrentRepository = userProfileCurrentRepository;
        this.objectMapper = objectMapper;
    }

    @Transactional(readOnly = true)
    public Map<String, Object> buildContext(UUID userId) {
        Optional<UserProfileCurrent> currentProfile = userProfileCurrentRepository.findById(userId);
        Map<String, Object> profile = currentProfile
            .map(UserProfileCurrent::getProfileJson)
            .map(this::copyMap)
            .orElseGet(LinkedHashMap::new);

        OffsetDateTime now = OffsetDateTime.now();
        OffsetDateTime fromAt = now.minusDays(CONTEXT_DAYS);
        MapSqlParameterSource windowParams = new MapSqlParameterSource()
            .addValue("userId", userId)
            .addValue("fromAt", fromAt)
            .addValue("toAt", now);

        Map<String, Object> context = new LinkedHashMap<>();
        context.put("profile", profile);
        context.put("profileSummary", currentProfile.map(UserProfileCurrent::getSummaryText).orElse(""));
        currentProfile.map(UserProfileCurrent::getUpdatedAt)
            .ifPresent(updatedAt -> context.put("profileUpdatedAt", updatedAt));
        context.put("learningProgress", buildLearningProgress(userId, profile, windowParams));
        context.put("practiceSignals", buildPracticeSignals(userId, windowParams));
        context.put("resourceSignals", buildResourceSignals(userId, profile, windowParams));
        return context;
    }

    private Map<String, Object> buildLearningProgress(
        UUID userId,
        Map<String, Object> profile,
        MapSqlParameterSource windowParams
    ) {
        Map<String, Object> progress = new LinkedHashMap<>();
        progress.put("source", "SERVER_AUTO_CONTEXT");
        progress.put("windowDays", CONTEXT_DAYS);
        progress.put("profileSkillMastery", buildProfileSkillMastery(profile));
        progress.put("profileFocusAreas", readProfileFocusAreas(profile));
        progress.put("knowledgeMasterySummary", buildKnowledgeMasterySummary(userId));
        progress.put("recentLearningTasks", buildRecentLearningTasks(windowParams));
        progress.put("dataAvailable", hasProfileLearningSignal(profile)
            || readBoolean(progress.get("knowledgeMasterySummary"), "dataAvailable")
            || !((List<?>) progress.get("recentLearningTasks")).isEmpty());
        return progress;
    }

    private Map<String, Object> buildKnowledgeMasterySummary(UUID userId) {
        MapSqlParameterSource params = new MapSqlParameterSource()
            .addValue("userId", userId)
            .addValue("limit", KNOWLEDGE_NODE_LIMIT);
        List<Map<String, Object>> rows = safeQuery("learner_knowledge_node", KNOWLEDGE_NODE_SQL, params);

        Map<String, Integer> statusCounts = new LinkedHashMap<>();
        statusCounts.put("WEAK", 0);
        statusCounts.put("IN_PROGRESS", 0);
        statusCounts.put("NOT_STARTED", 0);
        statusCounts.put("MASTERED", 0);

        List<Map<String, Object>> priorityKnowledge = new ArrayList<>();
        double masteryTotal = 0.0;
        for (Map<String, Object> row : rows) {
            String status = readString(row.get("node_status"));
            statusCounts.computeIfPresent(status, (key, count) -> count + 1);
            double mastery = readDouble(row.get("mastery_score"));
            masteryTotal += mastery;

            Map<String, Object> node = new LinkedHashMap<>();
            node.put("key", readString(row.get("canonical_key")));
            node.put("topic", readString(row.get("topic")));
            node.put("masteryScore", round(mastery, 3));
            node.put("status", status);
            node.put("source", readString(row.get("source")));
            putIfPresent(node, "updatedAt", readIsoOffsetDateTime(row.get("updated_at")));
            priorityKnowledge.add(node);
        }

        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("dataAvailable", !rows.isEmpty());
        summary.put("statusCounts", statusCounts);
        summary.put("averageMasteryScore", rows.isEmpty() ? null : round(masteryTotal / rows.size(), 3));
        summary.put("priorityKnowledge", priorityKnowledge);
        summary.put("nextRecommended", priorityKnowledge.stream()
            .filter(item -> !"MASTERED".equals(item.get("status")))
            .limit(5)
            .map(item -> item.get("key"))
            .toList());
        return summary;
    }

    private List<Map<String, Object>> buildRecentLearningTasks(MapSqlParameterSource params) {
        List<Map<String, Object>> rows = safeQuery("smart_engine_task.evaluation", EVALUATION_TASK_SQL, params);
        List<Map<String, Object>> tasks = new ArrayList<>();
        for (Map<String, Object> row : rows) {
            Map<String, Object> task = new LinkedHashMap<>();
            task.put("serviceType", readString(row.get("service_type")));
            task.put("taskStatus", readString(row.get("task_status")));
            task.put("count", readInt(row.get("task_count")));
            tasks.add(task);
        }
        return tasks;
    }

    private Map<String, Object> buildPracticeSignals(UUID userId, MapSqlParameterSource windowParams) {
        Map<String, Object> practice = new LinkedHashMap<>();
        practice.put("windowDays", CONTEXT_DAYS);
        practice.put("practiceSummary", buildPracticeSummary(windowParams));
        practice.put("evaluationTaskSummary", buildRecentLearningTasks(windowParams));
        practice.put("mistakeBookSummary", buildMistakeBookSummary(userId));
        practice.put("topMistakeKnowledgeTags", buildTopMistakeTags(userId));
        practice.put("recentMistakes", buildRecentMistakes(userId));
        practice.put("reviewSummary", buildReviewSummary(windowParams));
        practice.put("dataAvailable", readBoolean(practice.get("practiceSummary"), "dataAvailable")
            || readBoolean(practice.get("mistakeBookSummary"), "dataAvailable")
            || readBoolean(practice.get("reviewSummary"), "dataAvailable")
            || !((List<?>) practice.get("evaluationTaskSummary")).isEmpty());
        return practice;
    }

    private Map<String, Object> buildPracticeSummary(MapSqlParameterSource params) {
        Map<String, Object> row = firstRow(safeQuery("practice_submission", PRACTICE_SUMMARY_SQL, params));
        int submissions = readInt(row.get("submission_count"));
        int correct = readInt(row.get("correct_count"));
        int incorrect = readInt(row.get("incorrect_count"));

        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("dataAvailable", submissions > 0);
        summary.put("submissionCount", submissions);
        summary.put("correctCount", correct);
        summary.put("incorrectCount", incorrect);
        summary.put("accuracyPercent", submissions == 0 ? null : round(correct * 100.0 / submissions, 1));
        putIfPresent(summary, "lastSubmittedAt", readIsoOffsetDateTime(row.get("last_submitted_at")));
        return summary;
    }

    private Map<String, Object> buildMistakeBookSummary(UUID userId) {
        MapSqlParameterSource params = new MapSqlParameterSource("userId", userId);
        Map<String, Object> row = firstRow(safeQuery("mistake_record.summary", MISTAKE_SUMMARY_SQL, params));

        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("dataAvailable", readInt(row.get("total_mistake_count")) > 0);
        summary.put("totalMistakeCount", readInt(row.get("total_mistake_count")));
        summary.put("unmasteredCount", readInt(row.get("unmastered_count")));
        summary.put("dueReviewCount", readInt(row.get("due_review_count")));
        summary.put("wrongCount", readInt(row.get("wrong_count")));
        summary.put("reviewCount", readInt(row.get("review_count")));
        putIfPresent(summary, "lastWrongAt", readIsoOffsetDateTime(row.get("last_wrong_at")));
        return summary;
    }

    private List<Map<String, Object>> buildRecentMistakes(UUID userId) {
        MapSqlParameterSource params = new MapSqlParameterSource()
            .addValue("userId", userId)
            .addValue("limit", RECENT_MISTAKE_LIMIT);
        List<Map<String, Object>> mistakes = new ArrayList<>();
        for (Map<String, Object> row : safeQuery("mistake_record.recent", RECENT_MISTAKE_SQL, params)) {
            Map<String, Object> mistake = new LinkedHashMap<>();
            mistake.put("id", readString(row.get("id")));
            mistake.put("knowledgeTags", readKnowledgeTags(row.get("knowledge_tags")));
            mistake.put("difficultyLevel", readString(row.get("difficulty_level")));
            mistake.put("mistakeType", readString(row.get("mistake_type")));
            mistake.put("wrongCount", readInt(row.get("wrong_count")));
            mistake.put("reviewCount", readInt(row.get("review_count")));
            mistake.put("mastered", Boolean.TRUE.equals(row.get("mastered")));
            putIfPresent(mistake, "nextReviewAt", readIsoOffsetDateTime(row.get("next_review_at")));
            putIfPresent(mistake, "lastWrongAt", readIsoOffsetDateTime(row.get("last_wrong_at")));
            mistakes.add(mistake);
        }
        return mistakes;
    }

    private List<Map<String, Object>> buildTopMistakeTags(UUID userId) {
        MapSqlParameterSource params = new MapSqlParameterSource()
            .addValue("userId", userId)
            .addValue("limit", MISTAKE_TAG_LIMIT);
        List<Map<String, Object>> tags = new ArrayList<>();
        for (Map<String, Object> row : safeQuery("mistake_record.tags", TOP_MISTAKE_TAG_SQL, params)) {
            Map<String, Object> tag = new LinkedHashMap<>();
            tag.put("topic", readString(row.get("topic")));
            tag.put("mistakeCount", readInt(row.get("mistake_count")));
            tag.put("wrongCount", readInt(row.get("wrong_count")));
            tags.add(tag);
        }
        return tags;
    }

    private Map<String, Object> buildReviewSummary(MapSqlParameterSource params) {
        Map<String, Object> row = firstRow(safeQuery("mistake_review_result", REVIEW_SUMMARY_SQL, params));
        int reviews = readInt(row.get("review_count"));
        int correctReviews = readInt(row.get("correct_review_count"));

        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("dataAvailable", reviews > 0);
        summary.put("reviewCount", reviews);
        summary.put("correctReviewCount", correctReviews);
        summary.put("correctReviewRatePercent", reviews == 0 ? null : round(correctReviews * 100.0 / reviews, 1));
        summary.put("averageQuality", row.get("average_quality") == null ? null : round(readDouble(row.get("average_quality")), 2));
        putIfPresent(summary, "lastReviewedAt", readIsoOffsetDateTime(row.get("last_reviewed_at")));
        return summary;
    }

    private Map<String, Object> buildResourceSignals(
        UUID userId,
        Map<String, Object> profile,
        MapSqlParameterSource windowParams
    ) {
        List<String> profilePreferredTypes = readPreferredResourceTypes(profile);
        Map<String, ResourcePreferenceBuilder> preferenceByType = new LinkedHashMap<>();
        for (String type : profilePreferredTypes) {
            preferenceByType.computeIfAbsent(type, ResourcePreferenceBuilder::new).profileMentioned = true;
        }

        for (Map<String, Object> row : safeQuery("smart_engine_task.resource_preference", RESOURCE_REQUEST_SQL, windowParams)) {
            String type = normalizeResourceType(row.get("resource_type"));
            if (type == null) {
                continue;
            }
            ResourcePreferenceBuilder builder = preferenceByType.computeIfAbsent(type, ResourcePreferenceBuilder::new);
            builder.requestCount += readInt(row.get("request_count"));
            builder.lastUsedAt = latest(builder.lastUsedAt, readOffsetDateTime(row.get("last_used_at")));
        }

        for (Map<String, Object> row : safeQuery("generated_artifact.resource_preference", RESOURCE_ARTIFACT_SQL, windowParams)) {
            String type = normalizeResourceType(row.get("resource_type"));
            if (type == null) {
                continue;
            }
            ResourcePreferenceBuilder builder = preferenceByType.computeIfAbsent(type, ResourcePreferenceBuilder::new);
            builder.generatedCount += readInt(row.get("generated_count"));
            builder.downloadCount += readInt(row.get("download_count"));
            builder.lastUsedAt = latest(builder.lastUsedAt, readOffsetDateTime(row.get("last_used_at")));
        }

        List<Map<String, Object>> preferences = preferenceByType.values()
            .stream()
            .sorted(Comparator
                .comparing(ResourcePreferenceBuilder::identified).reversed()
                .thenComparing(ResourcePreferenceBuilder::usageCount, Comparator.reverseOrder())
                .thenComparing(builder -> builder.type))
            .map(ResourcePreferenceBuilder::toMap)
            .toList();

        Map<String, Object> signals = new LinkedHashMap<>();
        signals.put("windowDays", CONTEXT_DAYS);
        signals.put("preferredResourceTypesFromProfile", profilePreferredTypes);
        putIfPresent(signals, "explanationPreference", firstValue(
            profile,
            "explanationPreference",
            "explanation_preference"
        ));
        signals.put("resourcePreferences", preferences);
        signals.put("dataAvailable", !profilePreferredTypes.isEmpty()
            || preferences.stream().anyMatch(item -> Boolean.TRUE.equals(item.get("identified"))));
        return signals;
    }

    private Map<String, Object> buildProfileSkillMastery(Map<String, Object> profile) {
        Map<String, Object> skillMastery = readRecord(firstValue(profile, "skillMastery", "skill_mastery"));
        List<Map<String, Object>> skills = new ArrayList<>();
        for (Map.Entry<String, Object> entry : skillMastery.entrySet()) {
            Integer masteryPercent = readMasteryPercent(entry.getValue());
            if (masteryPercent == null) {
                continue;
            }
            Map<String, Object> skill = new LinkedHashMap<>();
            skill.put("topic", entry.getKey());
            skill.put("masteryPercent", masteryPercent);
            skills.add(skill);
        }
        skills.sort(Comparator
            .comparingInt((Map<String, Object> item) -> (Integer) item.get("masteryPercent"))
            .reversed()
            .thenComparing(item -> String.valueOf(item.get("topic"))));

        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("dataAvailable", !skills.isEmpty());
        summary.put("strongestSkills", skills.stream().limit(3).toList());
        summary.put("weakSkills", skills.stream()
            .sorted(Comparator
                .comparingInt((Map<String, Object> item) -> (Integer) item.get("masteryPercent"))
                .thenComparing(item -> String.valueOf(item.get("topic"))))
            .limit(5)
            .toList());
        return summary;
    }

    private List<String> readProfileFocusAreas(Map<String, Object> profile) {
        List<String> focusAreas = new ArrayList<>();
        Object weakPointDetails = firstValue(profile, "weakPointDetails", "weak_point_details");
        if (weakPointDetails instanceof Iterable<?> iterable) {
            for (Object item : iterable) {
                if (item instanceof Map<?, ?> map) {
                    String topic = readString(firstValue(readRecord(map), "topic", "name", "knowledge"));
                    if (!topic.isBlank()) {
                        focusAreas.add(topic);
                    }
                    continue;
                }
                String topic = readString(item);
                if (!topic.isBlank()) {
                    focusAreas.add(topic);
                }
            }
        }
        focusAreas.addAll(readStringList(firstValue(profile, "weakPoints", "weak_points", "knowledgeGaps", "knowledge_gaps")));
        return focusAreas.stream().distinct().limit(8).toList();
    }

    private boolean hasProfileLearningSignal(Map<String, Object> profile) {
        return readBoolean(buildProfileSkillMastery(profile), "dataAvailable")
            || !readProfileFocusAreas(profile).isEmpty()
            || firstValue(profile, "learningGoal", "learning_goal", "targetLevel", "target_level") != null;
    }

    private List<String> readPreferredResourceTypes(Map<String, Object> profile) {
        List<String> values = readStringList(firstValue(
            profile,
            "preferredResourceTypes",
            "preferred_resource_types",
            "preferredResources",
            "preferred_resources",
            "resourcePreferences",
            "resource_preferences"
        ));
        return values.stream()
            .map(this::normalizeResourceType)
            .filter(type -> type != null)
            .distinct()
            .toList();
    }

    private List<Map<String, Object>> safeQuery(
        String sourceName,
        String sql,
        MapSqlParameterSource params
    ) {
        try {
            return jdbcTemplate.queryForList(sql, params);
        } catch (DataAccessException ex) {
            log.warn("Personalized learning context source {} is unavailable: {}", sourceName, ex.getMessage());
            return List.of();
        }
    }

    private Map<String, Object> firstRow(List<Map<String, Object>> rows) {
        return rows.isEmpty() ? Map.of() : rows.get(0);
    }

    private Map<String, Object> copyMap(Map<String, Object> source) {
        return source == null ? new LinkedHashMap<>() : new LinkedHashMap<>(source);
    }

    private Map<String, Object> readRecord(Map<?, ?> source) {
        Map<String, Object> record = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : source.entrySet()) {
            if (entry.getKey() != null) {
                record.put(String.valueOf(entry.getKey()), entry.getValue());
            }
        }
        return record;
    }

    private Map<String, Object> readRecord(Object value) {
        if (value instanceof Map<?, ?> map) {
            return readRecord(map);
        }
        return Map.of();
    }

    private Object firstValue(Map<String, Object> record, String... keys) {
        for (String key : keys) {
            if (record.containsKey(key)) {
                return record.get(key);
            }
        }
        return null;
    }

    private List<String> readStringList(Object value) {
        if (value == null) {
            return List.of();
        }
        List<String> items = new ArrayList<>();
        if (value instanceof Iterable<?> iterable) {
            for (Object item : iterable) {
                if (item instanceof Map<?, ?> map) {
                    Object typedValue = firstValue(readRecord(map), "type", "resourceType", "topic", "name");
                    String text = readString(typedValue);
                    if (!text.isBlank()) {
                        items.add(text);
                    }
                    continue;
                }
                String text = readString(item);
                if (!text.isBlank()) {
                    items.add(text);
                }
            }
            return items;
        }
        if (value instanceof Map<?, ?> map) {
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                if (entry.getValue() == null || Boolean.FALSE.equals(entry.getValue())) {
                    continue;
                }
                String text = readString(entry.getKey());
                if (!text.isBlank()) {
                    items.add(text);
                }
            }
            return items;
        }
        String raw = readString(value);
        if (raw.isBlank()) {
            return List.of();
        }
        for (String item : raw.split("[,;，；]")) {
            String trimmed = item.trim();
            if (!trimmed.isBlank()) {
                items.add(trimmed);
            }
        }
        return items;
    }

    private List<String> readKnowledgeTags(Object value) {
        if (value == null) {
            return List.of();
        }
        if (value instanceof Iterable<?> || value instanceof Map<?, ?>) {
            return readStringList(value);
        }
        String raw = readString(value);
        if (raw.startsWith("[") && raw.endsWith("]")) {
            try {
                List<Object> parsed = objectMapper.readValue(raw, new TypeReference<>() {
                });
                return readStringList(parsed);
            } catch (JsonProcessingException ex) {
                log.warn("Failed to parse mistake knowledge tags: {}", ex.getMessage());
            }
        }
        return raw.isBlank() ? List.of() : List.of(raw);
    }

    private String normalizeResourceType(Object value) {
        String raw = readString(value);
        if (raw.isBlank()) {
            return null;
        }
        return raw.trim().replace('-', '_').toUpperCase(Locale.ROOT);
    }

    private Integer readMasteryPercent(Object value) {
        Number number;
        if (value instanceof Number typedNumber) {
            number = typedNumber;
        } else {
            try {
                number = Double.parseDouble(readString(value));
            } catch (NumberFormatException ex) {
                return null;
            }
        }
        double raw = number.doubleValue();
        double percent = raw <= 1.0 ? raw * 100.0 : raw;
        return (int) Math.round(Math.max(0.0, Math.min(100.0, percent)));
    }

    private String readString(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private int readInt(Object value) {
        if (value instanceof Number number) {
            return number.intValue();
        }
        try {
            String raw = readString(value);
            return raw.isBlank() ? 0 : Integer.parseInt(raw);
        } catch (NumberFormatException ex) {
            return 0;
        }
    }

    private double readDouble(Object value) {
        if (value instanceof Number number) {
            return number.doubleValue();
        }
        try {
            String raw = readString(value);
            return raw.isBlank() ? 0.0 : Double.parseDouble(raw);
        } catch (NumberFormatException ex) {
            return 0.0;
        }
    }

    private boolean readBoolean(Object value, String key) {
        if (value instanceof Map<?, ?> map) {
            return Boolean.TRUE.equals(map.get(key));
        }
        return false;
    }

    private OffsetDateTime readOffsetDateTime(Object value) {
        if (value instanceof OffsetDateTime offsetDateTime) {
            return offsetDateTime;
        }
        if (value instanceof Timestamp timestamp) {
            return timestamp.toInstant().atOffset(ZoneOffset.UTC);
        }
        String raw = readString(value);
        if (raw.isBlank()) {
            return null;
        }
        try {
            return OffsetDateTime.parse(raw);
        } catch (RuntimeException ex) {
            return null;
        }
    }

    private String readIsoOffsetDateTime(Object value) {
        OffsetDateTime offsetDateTime = readOffsetDateTime(value);
        return offsetDateTime == null ? null : offsetDateTime.toString();
    }

    private OffsetDateTime latest(OffsetDateTime left, OffsetDateTime right) {
        if (left == null) {
            return right;
        }
        if (right == null) {
            return left;
        }
        return left.isAfter(right) ? left : right;
    }

    private double round(double value, int scale) {
        double factor = Math.pow(10, scale);
        return Math.round(value * factor) / factor;
    }

    private void putIfPresent(Map<String, Object> target, String key, Object value) {
        if (value != null) {
            target.put(key, value);
        }
    }

    private static final class ResourcePreferenceBuilder {
        private final String type;
        private boolean profileMentioned;
        private int requestCount;
        private int generatedCount;
        private int downloadCount;
        private OffsetDateTime lastUsedAt;

        private ResourcePreferenceBuilder(String type) {
            this.type = type;
        }

        private boolean identified() {
            return profileMentioned || usageCount() > 0;
        }

        private int usageCount() {
            return requestCount + generatedCount + downloadCount;
        }

        private Map<String, Object> toMap() {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("type", type);
            item.put("identified", identified());
            item.put("profileMentioned", profileMentioned);
            item.put("requestCount", requestCount);
            item.put("generatedCount", generatedCount);
            item.put("downloadCount", downloadCount);
            if (lastUsedAt != null) {
                item.put("lastUsedAt", lastUsedAt.toString());
            }
            return item;
        }
    }
}
