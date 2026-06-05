package com.project.application.mistake;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.mistake.dto.CreateReviewSessionRequest;
import com.project.api.mistake.dto.MistakeListResponse;
import com.project.api.mistake.dto.MistakeRecordResponse;
import com.project.api.mistake.dto.MistakeReviewSessionResponse;
import com.project.api.mistake.dto.MistakeReviewSubmitItem;
import com.project.api.mistake.dto.MistakeStatsResponse;
import com.project.api.mistake.dto.MistakeUpdateRequest;
import com.project.api.mistake.dto.SubmitReviewSessionRequest;
import com.project.application.common.ApplicationException;
import com.project.application.learningpath.PersonalizedLearningRefreshService;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;

/**
 * 自动错题本的查询与复习工作流。
 */
@Service
public class MistakeBookService {

    private static final Set<String> ALLOWED_STATUSES = Set.of("due", "active", "mastered", "all");
    private static final Set<String> ALLOWED_MISTAKE_TYPES = Set.of("conceptual", "procedural", "careless");
    private static final int DEFAULT_PAGE_SIZE = 12;
    private static final int MAX_PAGE_SIZE = 50;
    private static final int DEFAULT_REVIEW_LIMIT = 10;

    private static final TypeReference<List<String>> STRING_LIST = new TypeReference<>() {
    };
    private static final TypeReference<Map<String, Object>> STRING_OBJECT_MAP = new TypeReference<>() {
    };

    private static final String RECORD_SELECT = """
        SELECT
            r.id,
            r.practice_item_id,
            r.last_submission_id,
            r.knowledge_tags,
            r.difficulty_level::text AS difficulty_level,
            r.mistake_type,
            r.user_note,
            r.wrong_count,
            r.review_count,
            r.next_review_at,
            r.ease_factor,
            r.interval_days,
            r.mastered,
            r.first_wrong_at,
            r.last_wrong_at,
            r.created_at,
            r.updated_at,
            i.question_type,
            i.stem,
            i.options_json,
            i.standard_answer,
            s.answer_json,
            s.judge_result_json,
            s.score,
            s.submitted_at
        FROM app.mistake_record r
        JOIN app.practice_item i ON i.id = r.practice_item_id
        JOIN app.practice_submission s ON s.id = r.last_submission_id
        """;

    private final NamedParameterJdbcTemplate jdbcTemplate;
    private final ObjectMapper objectMapper;
    private final PersonalizedLearningRefreshService personalizedLearningRefreshService;

    public MistakeBookService(
        NamedParameterJdbcTemplate jdbcTemplate,
        ObjectMapper objectMapper,
        PersonalizedLearningRefreshService personalizedLearningRefreshService
    ) {
        this.jdbcTemplate = jdbcTemplate;
        this.objectMapper = objectMapper;
        this.personalizedLearningRefreshService = personalizedLearningRefreshService;
    }

    @Transactional(readOnly = true)
    public MistakeListResponse listMistakes(
        UUID userId,
        String status,
        String knowledgeTag,
        String difficulty,
        Integer page,
        Integer size
    ) {
        String normalizedStatus = normalizeStatus(status);
        int safePage = Math.max(0, page == null ? 0 : page);
        int safeSize = Math.max(1, Math.min(MAX_PAGE_SIZE, size == null ? DEFAULT_PAGE_SIZE : size));
        MapSqlParameterSource params = baseParams(userId)
            .addValue("limit", safeSize)
            .addValue("offset", safePage * safeSize);

        List<String> conditions = buildRecordConditions(normalizedStatus, knowledgeTag, difficulty, params);
        String whereClause = " WHERE " + String.join(" AND ", conditions);
        String querySql = RECORD_SELECT + whereClause + "\n" + """
            ORDER BY
                CASE
                    WHEN NOT r.mastered AND COALESCE(r.next_review_at, r.created_at) <= now() THEN 0
                    WHEN NOT r.mastered THEN 1
                    ELSE 2
                END,
                r.next_review_at ASC,
                r.updated_at DESC
            LIMIT :limit OFFSET :offset
            """;
        String countSql = "SELECT COUNT(*) FROM app.mistake_record r" + whereClause;

        List<MistakeRecordResponse> items = jdbcTemplate.query(querySql, params, recordRowMapper());
        Long total = jdbcTemplate.queryForObject(countSql, params, Long.class);
        return new MistakeListResponse(
            items,
            total == null ? 0 : total,
            safePage,
            safeSize,
            loadStats(userId)
        );
    }

    @Transactional
    public MistakeRecordResponse updateMistake(UUID userId, UUID mistakeId, MistakeUpdateRequest request) {
        String normalizedType = normalizeMistakeType(request == null ? null : request.mistakeType());
        MapSqlParameterSource params = baseParams(userId)
            .addValue("id", mistakeId)
            .addValue("userNote", request == null ? null : request.userNote())
            .addValue("mistakeType", normalizedType)
            .addValue("mastered", request == null ? null : request.mastered());

        int updated = jdbcTemplate.update(
            """
            UPDATE app.mistake_record
            SET user_note = COALESCE(:userNote, user_note),
                mistake_type = COALESCE(:mistakeType, mistake_type),
                mastered = COALESCE(:mastered, mastered)
            WHERE id = :id AND user_id = :userId
            """,
            params
        );
        if (updated == 0) {
            throw new ApplicationException("MISTAKE_NOT_FOUND", "错题不存在", HttpStatus.NOT_FOUND);
        }
        return findRecord(userId, mistakeId)
            .orElseThrow(() -> new ApplicationException("MISTAKE_NOT_FOUND", "错题不存在", HttpStatus.NOT_FOUND));
    }

    @Transactional
    public MistakeReviewSessionResponse createReviewSession(UUID userId, CreateReviewSessionRequest request) {
        List<UUID> selectedIds = resolveReviewMistakeIds(userId, request);
        if (selectedIds.isEmpty()) {
            throw new ApplicationException("NO_DUE_MISTAKES", "当前没有可复习的错题", HttpStatus.BAD_REQUEST);
        }

        String idsCsv = selectedIds.stream().map(UUID::toString).collect(Collectors.joining(","));
        UUID sessionId = jdbcTemplate.queryForObject(
            """
            INSERT INTO app.mistake_review_session(user_id, mistake_ids)
            VALUES (:userId, string_to_array(:idsCsv, ',')::uuid[])
            RETURNING id
            """,
            baseParams(userId).addValue("idsCsv", idsCsv),
            UUID.class
        );
        if (sessionId == null) {
            throw new ApplicationException("REVIEW_SESSION_FAILED", "复习会话创建失败", HttpStatus.INTERNAL_SERVER_ERROR);
        }
        return getReviewSession(userId, sessionId);
    }

    private void triggerLearningPathRefreshAfterCommit(UUID userId, String reason) {
        Runnable trigger = () -> personalizedLearningRefreshService.triggerPracticeRefresh(userId, reason);
        if (!TransactionSynchronizationManager.isSynchronizationActive()) {
            trigger.run();
            return;
        }
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCommit() {
                trigger.run();
            }
        });
    }

    @Transactional(readOnly = true)
    public MistakeReviewSessionResponse getReviewSession(UUID userId, UUID sessionId) {
        SessionHeader header = findSessionHeader(userId, sessionId)
            .orElseThrow(() -> new ApplicationException("REVIEW_SESSION_NOT_FOUND", "复习会话不存在", HttpStatus.NOT_FOUND));
        List<MistakeRecordResponse> items = jdbcTemplate.query(
            RECORD_SELECT + """
            JOIN app.mistake_review_session mrs ON mrs.id = :sessionId
            WHERE r.user_id = :userId
              AND mrs.user_id = :userId
              AND r.id = ANY(mrs.mistake_ids)
            ORDER BY array_position(mrs.mistake_ids, r.id)
            """,
            baseParams(userId).addValue("sessionId", sessionId),
            recordRowMapper()
        );
        return new MistakeReviewSessionResponse(
            header.id(),
            header.status(),
            header.score(),
            items,
            header.createdAt(),
            header.completedAt()
        );
    }

    @Transactional
    public MistakeReviewSessionResponse submitReviewSession(
        UUID userId,
        UUID sessionId,
        SubmitReviewSessionRequest request
    ) {
        SessionHeader header = findSessionHeader(userId, sessionId)
            .orElseThrow(() -> new ApplicationException("REVIEW_SESSION_NOT_FOUND", "复习会话不存在", HttpStatus.NOT_FOUND));
        if (!"IN_PROGRESS".equals(header.status())) {
            throw new ApplicationException("REVIEW_SESSION_CLOSED", "复习会话已结束", HttpStatus.CONFLICT);
        }

        MistakeReviewSessionResponse session = getReviewSession(userId, sessionId);
        Map<UUID, MistakeRecordResponse> sessionItems = session.items().stream()
            .collect(Collectors.toMap(MistakeRecordResponse::id, item -> item, (left, right) -> left, LinkedHashMap::new));
        Map<UUID, MistakeReviewSubmitItem> submittedItems = normalizeSubmittedItems(request);
        if (!submittedItems.keySet().containsAll(sessionItems.keySet())) {
            throw new ApplicationException("REVIEW_INCOMPLETE", "请为本次复习的每道错题选择掌握评分", HttpStatus.BAD_REQUEST);
        }

        OffsetDateTime now = OffsetDateTime.now();
        int qualitySum = 0;
        for (MistakeRecordResponse record : sessionItems.values()) {
            MistakeReviewSubmitItem submitted = submittedItems.get(record.id());
            int quality = normalizeQuality(submitted.quality());
            qualitySum += quality;
            MistakeSchedule schedule = calculateNextSchedule(
                record.reviewCount(),
                record.easeFactor(),
                record.intervalDays(),
                quality,
                now
            );
            saveReviewResult(userId, sessionId, record.id(), submitted, quality);
            jdbcTemplate.update(
                """
                UPDATE app.mistake_record
                SET review_count = review_count + 1,
                    ease_factor = :easeFactor,
                    interval_days = :intervalDays,
                    next_review_at = :nextReviewAt,
                    mastered = :mastered
                WHERE id = :id AND user_id = :userId
                """,
                baseParams(userId)
                    .addValue("id", record.id())
                    .addValue("easeFactor", schedule.easeFactor())
                    .addValue("intervalDays", schedule.intervalDays())
                    .addValue("nextReviewAt", schedule.nextReviewAt())
                    .addValue("mastered", schedule.mastered())
            );
        }

        BigDecimal score = BigDecimal.valueOf(sessionItems.isEmpty() ? 0 : (qualitySum * 20.0 / sessionItems.size()))
            .setScale(2, RoundingMode.HALF_UP);
        jdbcTemplate.update(
            """
            UPDATE app.mistake_review_session
            SET status = 'DONE',
                score = :score,
                completed_at = :completedAt
            WHERE id = :sessionId AND user_id = :userId
            """,
            baseParams(userId)
                .addValue("sessionId", sessionId)
                .addValue("score", score)
                .addValue("completedAt", now)
        );
        triggerLearningPathRefreshAfterCommit(userId, "mistake_review_submitted");
        return getReviewSession(userId, sessionId);
    }

    public MistakeSchedule calculateNextSchedule(
        int reviewCount,
        BigDecimal easeFactor,
        int intervalDays,
        int quality,
        OffsetDateTime now
    ) {
        int normalizedQuality = normalizeQuality(quality);
        double previousEaseFactor = easeFactor == null ? 2.5 : easeFactor.doubleValue();
        double missDistance = 5 - normalizedQuality;
        double nextEaseFactor = Math.max(
            1.3,
            previousEaseFactor + (0.1 - missDistance * (0.08 + missDistance * 0.02))
        );
        int nextInterval;
        if (normalizedQuality < 3) {
            nextInterval = 1;
        } else if (reviewCount <= 0) {
            nextInterval = 1;
        } else if (reviewCount == 1) {
            nextInterval = 6;
        } else {
            nextInterval = Math.max(1, (int) Math.round(Math.max(1, intervalDays) * nextEaseFactor));
        }
        return new MistakeSchedule(
            BigDecimal.valueOf(nextEaseFactor).setScale(2, RoundingMode.HALF_UP),
            nextInterval,
            now.plusDays(nextInterval),
            normalizedQuality >= 4 && nextInterval >= 21
        );
    }

    private List<String> buildRecordConditions(
        String status,
        String knowledgeTag,
        String difficulty,
        MapSqlParameterSource params
    ) {
        List<String> conditions = new ArrayList<>();
        conditions.add("r.user_id = :userId");
        switch (status) {
            case "due" -> conditions.add("NOT r.mastered AND COALESCE(r.next_review_at, r.created_at) <= now()");
            case "active" -> conditions.add("NOT r.mastered");
            case "mastered" -> conditions.add("r.mastered");
            case "all" -> {
            }
            default -> throw new ApplicationException("INVALID_ARGUMENT", "错题状态参数无效", HttpStatus.BAD_REQUEST);
        }
        if (knowledgeTag != null && !knowledgeTag.isBlank()) {
            conditions.add("jsonb_exists(r.knowledge_tags, :knowledgeTag)");
            params.addValue("knowledgeTag", knowledgeTag.trim());
        }
        if (difficulty != null && !difficulty.isBlank()) {
            conditions.add("r.difficulty_level::text = :difficulty");
            params.addValue("difficulty", difficulty.trim().toUpperCase(Locale.ROOT));
        }
        return conditions;
    }

    private List<UUID> resolveReviewMistakeIds(UUID userId, CreateReviewSessionRequest request) {
        List<UUID> requestedIds = request == null || request.mistakeIds() == null
            ? List.of()
            : request.mistakeIds().stream()
                .filter(Objects::nonNull)
                .collect(Collectors.collectingAndThen(Collectors.toCollection(LinkedHashSet::new), ArrayList::new));
        if (!requestedIds.isEmpty()) {
            String idsCsv = requestedIds.stream().map(UUID::toString).collect(Collectors.joining(","));
            return jdbcTemplate.queryForList(
                """
                SELECT id
                FROM app.mistake_record
                WHERE user_id = :userId
                  AND id = ANY(string_to_array(:idsCsv, ',')::uuid[])
                ORDER BY array_position(string_to_array(:idsCsv, ',')::uuid[], id)
                """,
                baseParams(userId).addValue("idsCsv", idsCsv),
                UUID.class
            );
        }

        int limit = Math.max(1, Math.min(MAX_PAGE_SIZE, request == null || request.limit() == null ? DEFAULT_REVIEW_LIMIT : request.limit()));
        return jdbcTemplate.queryForList(
            """
            SELECT id
            FROM app.mistake_record
            WHERE user_id = :userId
              AND NOT mastered
              AND COALESCE(next_review_at, created_at) <= now()
            ORDER BY next_review_at ASC, last_wrong_at DESC
            LIMIT :limit
            """,
            baseParams(userId).addValue("limit", limit),
            UUID.class
        );
    }

    private Optional<MistakeRecordResponse> findRecord(UUID userId, UUID mistakeId) {
        List<MistakeRecordResponse> rows = jdbcTemplate.query(
            RECORD_SELECT + " WHERE r.user_id = :userId AND r.id = :id",
            baseParams(userId).addValue("id", mistakeId),
            recordRowMapper()
        );
        return rows.stream().findFirst();
    }

    private Optional<SessionHeader> findSessionHeader(UUID userId, UUID sessionId) {
        List<SessionHeader> rows = jdbcTemplate.query(
            """
            SELECT id, status, score, created_at, completed_at
            FROM app.mistake_review_session
            WHERE id = :sessionId AND user_id = :userId
            """,
            baseParams(userId).addValue("sessionId", sessionId),
            (rs, rowNum) -> new SessionHeader(
                rs.getObject("id", UUID.class),
                rs.getString("status"),
                rs.getBigDecimal("score"),
                readOffsetDateTime(rs, "created_at"),
                readOffsetDateTime(rs, "completed_at")
            )
        );
        return rows.stream().findFirst();
    }

    private MistakeStatsResponse loadStats(UUID userId) {
        return jdbcTemplate.queryForObject(
            """
            SELECT
                COUNT(*) FILTER (WHERE NOT mastered AND COALESCE(next_review_at, created_at) <= now()) AS due_count,
                COUNT(*) FILTER (WHERE NOT mastered) AS active_count,
                COUNT(*) FILTER (WHERE mastered) AS mastered_count
            FROM app.mistake_record
            WHERE user_id = :userId
            """,
            baseParams(userId),
            (rs, rowNum) -> new MistakeStatsResponse(
                rs.getLong("due_count"),
                rs.getLong("active_count"),
                rs.getLong("mastered_count")
            )
        );
    }

    private void saveReviewResult(
        UUID userId,
        UUID sessionId,
        UUID mistakeRecordId,
        MistakeReviewSubmitItem submitted,
        int quality
    ) {
        jdbcTemplate.update(
            """
            INSERT INTO app.mistake_review_result(user_id, session_id, mistake_record_id, quality, is_correct, answer_json)
            VALUES (:userId, :sessionId, :mistakeRecordId, :quality, :isCorrect, CAST(:answerJson AS jsonb))
            ON CONFLICT (session_id, mistake_record_id) DO UPDATE
            SET quality = EXCLUDED.quality,
                is_correct = EXCLUDED.is_correct,
                answer_json = EXCLUDED.answer_json,
                reviewed_at = now()
            """,
            baseParams(userId)
                .addValue("sessionId", sessionId)
                .addValue("mistakeRecordId", mistakeRecordId)
                .addValue("quality", quality)
                .addValue("isCorrect", submitted.isCorrect())
                .addValue("answerJson", writeJson(submitted.answer() == null ? Collections.emptyMap() : submitted.answer()))
        );
    }

    private Map<UUID, MistakeReviewSubmitItem> normalizeSubmittedItems(SubmitReviewSessionRequest request) {
        if (request == null || request.items() == null || request.items().isEmpty()) {
            throw new ApplicationException("INVALID_ARGUMENT", "复习提交不能为空", HttpStatus.BAD_REQUEST);
        }
        Map<UUID, MistakeReviewSubmitItem> submittedItems = new LinkedHashMap<>();
        for (MistakeReviewSubmitItem item : request.items()) {
            if (item == null || item.mistakeRecordId() == null) {
                throw new ApplicationException("INVALID_ARGUMENT", "复习提交包含无效错题", HttpStatus.BAD_REQUEST);
            }
            submittedItems.put(item.mistakeRecordId(), item);
        }
        return submittedItems;
    }

    private String normalizeStatus(String status) {
        String normalized = status == null || status.isBlank()
            ? "active"
            : status.trim().toLowerCase(Locale.ROOT);
        if (!ALLOWED_STATUSES.contains(normalized)) {
            throw new ApplicationException("INVALID_ARGUMENT", "错题状态参数无效", HttpStatus.BAD_REQUEST);
        }
        return normalized;
    }

    private String normalizeMistakeType(String mistakeType) {
        if (mistakeType == null || mistakeType.isBlank()) {
            return null;
        }
        String normalized = mistakeType.trim().toLowerCase(Locale.ROOT);
        if (!ALLOWED_MISTAKE_TYPES.contains(normalized)) {
            throw new ApplicationException("INVALID_ARGUMENT", "错因类型无效", HttpStatus.BAD_REQUEST);
        }
        return normalized;
    }

    private int normalizeQuality(Integer quality) {
        if (quality == null || quality < 0 || quality > 5) {
            throw new ApplicationException("INVALID_ARGUMENT", "复习评分必须在 0 到 5 之间", HttpStatus.BAD_REQUEST);
        }
        return quality;
    }

    private int normalizeQuality(int quality) {
        if (quality < 0 || quality > 5) {
            throw new ApplicationException("INVALID_ARGUMENT", "复习评分必须在 0 到 5 之间", HttpStatus.BAD_REQUEST);
        }
        return quality;
    }

    private MapSqlParameterSource baseParams(UUID userId) {
        return new MapSqlParameterSource("userId", userId);
    }

    private RowMapper<MistakeRecordResponse> recordRowMapper() {
        return (rs, rowNum) -> {
            Map<String, Object> answerJson = readMap(rs.getObject("answer_json"));
            return new MistakeRecordResponse(
                rs.getObject("id", UUID.class),
                rs.getObject("practice_item_id", UUID.class),
                rs.getObject("last_submission_id", UUID.class),
                rs.getString("question_type"),
                rs.getString("stem"),
                readStringList(rs.getObject("options_json")),
                readMap(rs.getObject("standard_answer")),
                String.valueOf(answerJson.getOrDefault("answer", "")),
                readMap(rs.getObject("judge_result_json")),
                rs.getBigDecimal("score"),
                readOffsetDateTime(rs, "submitted_at"),
                readStringList(rs.getObject("knowledge_tags")),
                rs.getString("difficulty_level"),
                rs.getString("mistake_type"),
                rs.getString("user_note"),
                rs.getInt("wrong_count"),
                rs.getInt("review_count"),
                readOffsetDateTime(rs, "next_review_at"),
                rs.getBigDecimal("ease_factor"),
                rs.getInt("interval_days"),
                rs.getBoolean("mastered"),
                readOffsetDateTime(rs, "first_wrong_at"),
                readOffsetDateTime(rs, "last_wrong_at"),
                readOffsetDateTime(rs, "created_at"),
                readOffsetDateTime(rs, "updated_at")
            );
        };
    }

    private OffsetDateTime readOffsetDateTime(ResultSet rs, String column) throws SQLException {
        return rs.getObject(column, OffsetDateTime.class);
    }

    private List<String> readStringList(Object raw) {
        try {
            List<String> parsed = objectMapper.readValue(rawJson(raw), STRING_LIST);
            return parsed == null ? List.of() : parsed;
        } catch (JsonProcessingException | IllegalArgumentException ex) {
            return List.of();
        }
    }

    private Map<String, Object> readMap(Object raw) {
        try {
            Map<String, Object> parsed = objectMapper.readValue(rawJson(raw), STRING_OBJECT_MAP);
            return parsed == null ? Map.of() : parsed;
        } catch (JsonProcessingException | IllegalArgumentException ex) {
            return Map.of();
        }
    }

    private String rawJson(Object raw) throws JsonProcessingException {
        if (raw == null) {
            return "{}";
        }
        if (raw instanceof String text) {
            return text;
        }
        if (raw instanceof Map<?, ?> || raw instanceof List<?>) {
            return objectMapper.writeValueAsString(raw);
        }
        return raw.toString();
    }

    private String writeJson(Map<String, Object> value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException ex) {
            throw new ApplicationException("INVALID_ARGUMENT", "复习答案格式无效", HttpStatus.BAD_REQUEST);
        }
    }

    private record SessionHeader(
        UUID id,
        String status,
        BigDecimal score,
        OffsetDateTime createdAt,
        OffsetDateTime completedAt
    ) {
    }

    public record MistakeSchedule(
        BigDecimal easeFactor,
        int intervalDays,
        OffsetDateTime nextReviewAt,
        boolean mastered
    ) {
    }
}
