package com.project.application.study;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.learningpath.dto.LearningPathCurrentResponse;
import com.project.api.mistake.dto.MistakeRecordResponse;
import com.project.api.profile.dto.KnowledgeGraphResponse;
import com.project.api.profile.dto.KnowledgeGraphResponse.KnowledgeEdgeDto;
import com.project.api.profile.dto.KnowledgeGraphResponse.KnowledgeNodeDto;
import com.project.api.profile.dto.UserProfileResponse;
import com.project.api.resource.dto.ResourceItemResponse;
import com.project.api.study.dto.DailyStudyWorkbenchResponse;
import com.project.api.study.dto.DailyStudyWorkbenchResponse.DailyTaskItem;
import com.project.api.study.dto.DailyStudyWorkbenchResponse.WorkbenchSummary;
import com.project.api.study.dto.KnowledgeNodeDetailResponse;
import com.project.api.study.dto.MistakeTrainingCampResponse;
import com.project.api.study.dto.MistakeTrainingCampResponse.MistakeCampGroup;
import com.project.api.study.dto.MistakeTrainingCampResponse.TrainingCampSummary;
import com.project.api.study.dto.MistakeTrainingCampResponse.TrainingMicroPractice;
import com.project.application.common.ApplicationException;
import com.project.application.learningpath.LearningPathQueryService;
import com.project.application.profile.LearnerKnowledgeGraphService;
import com.project.application.profile.UserProfileQueryService;
import com.project.application.resource.ResourceLibraryService;
import com.project.security.JwtAuthenticatedUser;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;

@Service
public class StudyWorkbenchService {

    private static final int DUE_MISTAKE_LIMIT = 8;
    private static final int RECOMMENDED_RESOURCE_LIMIT = 6;
    private static final int RELATED_MISTAKE_LIMIT = 6;
    private static final int RELATED_RESOURCE_LIMIT = 6;
    private static final int CAMP_LIMIT = 12;
    private static final int CAMP_REPRESENTATIVE_LIMIT = 3;
    private static final TypeReference<List<String>> STRING_LIST = new TypeReference<>() {};
    private static final TypeReference<Map<String, Object>> STRING_OBJECT_MAP = new TypeReference<>() {};

    private static final String MISTAKE_SELECT = """
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

    private static final String DUE_MISTAKE_SQL = MISTAKE_SELECT + """
        WHERE r.user_id = :userId
          AND r.mastered IS FALSE
          AND COALESCE(r.next_review_at, r.created_at) <= now()
        ORDER BY r.next_review_at ASC, r.wrong_count DESC, r.last_wrong_at DESC
        LIMIT :limit
        """;

    private static final String RELATED_MISTAKE_SQL = MISTAKE_SELECT + """
        WHERE r.user_id = :userId
          AND EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(r.knowledge_tags) AS tag(topic)
            WHERE lower(tag.topic) LIKE :topicLike
               OR lower(tag.topic) = :topicExact
          )
        ORDER BY
          CASE WHEN r.mastered IS FALSE AND COALESCE(r.next_review_at, r.created_at) <= now() THEN 0
               WHEN r.mastered IS FALSE THEN 1
               ELSE 2
          END,
          r.wrong_count DESC,
          r.updated_at DESC
        LIMIT :limit
        """;

    private static final String CAMP_GROUP_SQL = """
        WITH typed_mistakes AS (
          SELECT
            r.id,
            r.mistake_type,
            COALESCE(NULLIF(r.mistake_type, ''), 'unclassified') AS normalized_type,
            COALESCE(primary_tag.topic, '未标注知识点') AS primary_tag,
            r.wrong_count,
            r.review_count,
            r.mastered,
            r.next_review_at
          FROM app.mistake_record r
          LEFT JOIN LATERAL (
            SELECT tag.topic
            FROM jsonb_array_elements_text(r.knowledge_tags) AS tag(topic)
            WHERE COALESCE(tag.topic, '') <> ''
            LIMIT 1
          ) primary_tag ON TRUE
          WHERE r.user_id = :userId
        )
        SELECT
          normalized_type,
          primary_tag,
          COUNT(*) AS mistake_count,
          COUNT(*) FILTER (WHERE mastered IS FALSE AND COALESCE(next_review_at, now()) <= now()) AS due_count,
          COUNT(*) FILTER (WHERE mastered IS TRUE) AS mastered_count,
          COALESCE(SUM(wrong_count), 0) AS total_wrong_count,
          COALESCE(SUM(review_count), 0) AS total_review_count,
          MIN(next_review_at) FILTER (WHERE mastered IS FALSE) AS next_review_at
        FROM typed_mistakes
        GROUP BY normalized_type, primary_tag
        ORDER BY due_count DESC, total_wrong_count DESC, mistake_count DESC, primary_tag ASC
        LIMIT :limit
        """;

    private static final String CAMP_REPRESENTATIVE_SQL = MISTAKE_SELECT + """
        WHERE r.user_id = :userId
          AND COALESCE(NULLIF(r.mistake_type, ''), 'unclassified') = :mistakeType
          AND COALESCE((
            SELECT tag.topic
            FROM jsonb_array_elements_text(r.knowledge_tags) AS tag(topic)
            WHERE COALESCE(tag.topic, '') <> ''
            LIMIT 1
          ), '未标注知识点') = :knowledgeTag
        ORDER BY
          CASE WHEN r.mastered IS FALSE AND COALESCE(r.next_review_at, r.created_at) <= now() THEN 0
               WHEN r.mastered IS FALSE THEN 1
               ELSE 2
          END,
          r.wrong_count DESC,
          r.updated_at DESC
        LIMIT :limit
        """;

    private final NamedParameterJdbcTemplate jdbcTemplate;
    private final ObjectMapper objectMapper;
    private final LearningPathQueryService learningPathQueryService;
    private final ResourceLibraryService resourceLibraryService;
    private final LearnerKnowledgeGraphService learnerKnowledgeGraphService;
    private final UserProfileQueryService userProfileQueryService;

    public StudyWorkbenchService(
        NamedParameterJdbcTemplate jdbcTemplate,
        ObjectMapper objectMapper,
        LearningPathQueryService learningPathQueryService,
        ResourceLibraryService resourceLibraryService,
        LearnerKnowledgeGraphService learnerKnowledgeGraphService,
        UserProfileQueryService userProfileQueryService
    ) {
        this.jdbcTemplate = jdbcTemplate;
        this.objectMapper = objectMapper;
        this.learningPathQueryService = learningPathQueryService;
        this.resourceLibraryService = resourceLibraryService;
        this.learnerKnowledgeGraphService = learnerKnowledgeGraphService;
        this.userProfileQueryService = userProfileQueryService;
    }

    @Transactional(readOnly = true)
    public DailyStudyWorkbenchResponse daily(JwtAuthenticatedUser currentUser) {
        UUID userId = currentUser.userId();
        OffsetDateTime now = OffsetDateTime.now();
        LearningPathCurrentResponse learningPath = learningPathQueryService.getCurrent(userId);
        @SuppressWarnings("unchecked")
        Map<String, Object> activeStep = learningPath.activeStep() == null
            ? null
            : new LinkedHashMap<>((Map<String, Object>) learningPath.activeStep());
        List<MistakeRecordResponse> dueMistakes = loadDueMistakes(userId, DUE_MISTAKE_LIMIT);
        List<ResourceItemResponse> resources = resourceLibraryService.recommendations(userId, RECOMMENDED_RESOURCE_LIMIT);
        KnowledgeGraphResponse graph = learnerKnowledgeGraphService.getGraph(currentUser, userId);
        UserProfileResponse profile = userProfileQueryService.getCurrentProfile(currentUser, userId);

        List<DailyTaskItem> tasks = buildDailyTasks(activeStep, dueMistakes, resources, graph);
        int completedTasks = (int) tasks.stream().filter(task -> "COMPLETED".equals(task.status())).count();
        int totalTasks = tasks.size();
        int weakKnowledgeCount = (int) graph.nodes().stream()
            .filter(node -> "WEAK".equals(node.status()) || "IN_PROGRESS".equals(node.status()))
            .count();
        WorkbenchSummary summary = new WorkbenchSummary(
            totalTasks,
            completedTasks,
            dueMistakes.size(),
            resources.size(),
            weakKnowledgeCount,
            totalTasks == 0 ? 0 : Math.round(completedTasks * 100f / totalTasks),
            nextAction(tasks),
            activeStep != null && !activeStep.isEmpty()
        );
        boolean dataAvailable = activeStep != null || !dueMistakes.isEmpty() || !resources.isEmpty() || !graph.nodes().isEmpty()
            || (profile.profile() != null && !profile.profile().isEmpty());
        return new DailyStudyWorkbenchResponse(
            userId,
            LocalDate.now(ZoneId.systemDefault()),
            now,
            summary,
            learningPath,
            activeStep,
            tasks,
            dueMistakes,
            resources,
            graph,
            profile,
            dataAvailable
        );
    }

    @Transactional(readOnly = true)
    public KnowledgeNodeDetailResponse knowledgeNodeDetail(JwtAuthenticatedUser currentUser, UUID requestedUserId, String nodeKey) {
        if (!currentUser.userId().equals(requestedUserId)) {
            throw new ApplicationException("FORBIDDEN", "无权访问该知识点详情", HttpStatus.FORBIDDEN);
        }
        String normalizedKey = safeString(nodeKey);
        if (normalizedKey.isBlank()) {
            throw new ApplicationException("INVALID_NODE_KEY", "知识点 key 不能为空", HttpStatus.BAD_REQUEST);
        }
        KnowledgeGraphResponse graph = learnerKnowledgeGraphService.getGraph(currentUser, requestedUserId);
        KnowledgeNodeDto node = graph.nodes().stream()
            .filter(item -> normalizedKey.equals(item.key()))
            .findFirst()
            .orElseThrow(() -> new ApplicationException("KNOWLEDGE_NODE_NOT_FOUND", "知识点不存在", HttpStatus.NOT_FOUND));

        List<KnowledgeEdgeDto> relatedEdges = graph.edges().stream()
            .filter(edge -> normalizedKey.equals(edge.from()) || normalizedKey.equals(edge.to()))
            .toList();
        Map<String, KnowledgeNodeDto> nodeByKey = new LinkedHashMap<>();
        graph.nodes().forEach(item -> nodeByKey.put(item.key(), item));
        List<KnowledgeNodeDto> prerequisites = relatedEdges.stream()
            .filter(edge -> "PREREQUISITE".equals(edge.type()) && normalizedKey.equals(edge.to()))
            .map(edge -> nodeByKey.get(edge.from()))
            .filter(Objects::nonNull)
            .toList();
        List<KnowledgeNodeDto> nextNodes = relatedEdges.stream()
            .filter(edge -> "PREREQUISITE".equals(edge.type()) && normalizedKey.equals(edge.from()))
            .map(edge -> nodeByKey.get(edge.to()))
            .filter(Objects::nonNull)
            .toList();
        List<KnowledgeNodeDto> relatedNodes = relatedEdges.stream()
            .filter(edge -> !"PREREQUISITE".equals(edge.type()))
            .map(edge -> normalizedKey.equals(edge.from()) ? nodeByKey.get(edge.to()) : nodeByKey.get(edge.from()))
            .filter(Objects::nonNull)
            .distinct()
            .toList();
        List<MistakeRecordResponse> mistakes = loadRelatedMistakes(requestedUserId, node.topic(), RELATED_MISTAKE_LIMIT);
        List<ResourceItemResponse> resources = resourceLibraryService
            .semanticSearch(requestedUserId, node.topic(), RELATED_RESOURCE_LIMIT)
            .results()
            .stream()
            .map(result -> result.resource())
            .filter(Objects::nonNull)
            .limit(RELATED_RESOURCE_LIMIT)
            .toList();
        return new KnowledgeNodeDetailResponse(
            requestedUserId,
            node,
            prerequisites,
            nextNodes,
            relatedNodes,
            relatedEdges,
            mistakes,
            resources,
            buildKnowledgeNextActions(node, mistakes, resources, prerequisites),
            Map.of(
                "topic", node.topic(),
                "knowledgeTags", List.of(node.topic()),
                "nodeKey", node.key(),
                "source", "KNOWLEDGE_GRAPH_DETAIL"
            )
        );
    }

    @Transactional(readOnly = true)
    public MistakeTrainingCampResponse mistakeTrainingCamps(JwtAuthenticatedUser currentUser) {
        UUID userId = currentUser.userId();
        List<Map<String, Object>> rows = jdbcTemplate.queryForList(
            CAMP_GROUP_SQL,
            new MapSqlParameterSource()
                .addValue("userId", userId)
                .addValue("limit", CAMP_LIMIT)
        );
        List<MistakeCampGroup> camps = new ArrayList<>();
        int activeMistakes = 0;
        int dueMistakes = 0;
        int masteredMistakes = 0;
        for (Map<String, Object> row : rows) {
            String mistakeType = safeString(row.get("normalized_type"));
            String knowledgeTag = safeString(row.get("primary_tag"));
            int mistakeCount = readInt(row.get("mistake_count"));
            int dueCount = readInt(row.get("due_count"));
            int masteredCount = readInt(row.get("mastered_count"));
            int totalWrongCount = readInt(row.get("total_wrong_count"));
            int totalReviewCount = readInt(row.get("total_review_count"));
            activeMistakes += Math.max(0, mistakeCount - masteredCount);
            dueMistakes += dueCount;
            masteredMistakes += masteredCount;
            List<MistakeRecordResponse> representatives = loadCampRepresentatives(
                userId,
                mistakeType,
                knowledgeTag,
                CAMP_REPRESENTATIVE_LIMIT
            );
            camps.add(new MistakeCampGroup(
                campId(mistakeType, knowledgeTag),
                campTitle(mistakeType, knowledgeTag),
                mistakeType,
                knowledgeTag,
                campExplanation(mistakeType, knowledgeTag),
                mistakeCount,
                dueCount,
                masteredCount,
                totalWrongCount,
                totalReviewCount,
                masteryChange(totalReviewCount, totalWrongCount, masteredCount),
                readOffset(row.get("next_review_at")),
                representatives,
                buildMicroPractices(mistakeType, knowledgeTag, representatives),
                Map.of(
                    "topic", knowledgeTag,
                    "knowledgeTags", List.of(knowledgeTag),
                    "mistakeType", mistakeType,
                    "source", "MISTAKE_TRAINING_CAMP"
                )
            ));
        }
        String topFocus = camps.isEmpty() ? "" : camps.getFirst().title();
        return new MistakeTrainingCampResponse(
            userId,
            OffsetDateTime.now(),
            new TrainingCampSummary(camps.size(), activeMistakes, dueMistakes, masteredMistakes, topFocus),
            camps
        );
    }

    public List<MistakeRecordResponse> loadDueMistakes(UUID userId, int limit) {
        return jdbcTemplate.query(
            DUE_MISTAKE_SQL,
            new MapSqlParameterSource()
                .addValue("userId", userId)
                .addValue("limit", Math.max(1, limit)),
            mistakeRowMapper()
        );
    }

    private List<MistakeRecordResponse> loadRelatedMistakes(UUID userId, String topic, int limit) {
        String normalized = safeString(topic).toLowerCase(Locale.ROOT);
        if (normalized.isBlank()) {
            return List.of();
        }
        return jdbcTemplate.query(
            RELATED_MISTAKE_SQL,
            new MapSqlParameterSource()
                .addValue("userId", userId)
                .addValue("topicLike", "%" + normalized + "%")
                .addValue("topicExact", normalized)
                .addValue("limit", Math.max(1, limit)),
            mistakeRowMapper()
        );
    }

    private List<MistakeRecordResponse> loadCampRepresentatives(
        UUID userId,
        String mistakeType,
        String knowledgeTag,
        int limit
    ) {
        return jdbcTemplate.query(
            CAMP_REPRESENTATIVE_SQL,
            new MapSqlParameterSource()
                .addValue("userId", userId)
                .addValue("mistakeType", mistakeType)
                .addValue("knowledgeTag", knowledgeTag)
                .addValue("limit", Math.max(1, limit)),
            mistakeRowMapper()
        );
    }

    private List<DailyTaskItem> buildDailyTasks(
        Map<String, Object> activeStep,
        List<MistakeRecordResponse> dueMistakes,
        List<ResourceItemResponse> resources,
        KnowledgeGraphResponse graph
    ) {
        List<DailyTaskItem> tasks = new ArrayList<>();
        if (activeStep != null && !activeStep.isEmpty()) {
            String stepId = safeString(activeStep.get("stepId"));
            String title = firstNonBlank(safeString(activeStep.get("title")), "继续当前学习阶段");
            int progress = Math.max(0, Math.min(100, readInt(firstValue(activeStep, "progress", "progressPercent"))));
            tasks.add(new DailyTaskItem(
                "stage:" + firstNonBlank(stepId, "current"),
                "STAGE",
                title,
                firstNonBlank(safeString(activeStep.get("checkpoint")), "完成本阶段资源学习后进行阶段检测。"),
                progress >= 100 ? "COMPLETED" : "IN_PROGRESS",
                progress,
                "打开学习路径",
                "/engine",
                activeStep,
                null
            ));
            tasks.add(new DailyTaskItem(
                "stage-test:" + firstNonBlank(stepId, "current"),
                "STAGE_TEST",
                "完成阶段检测",
                "用一组阶段题验证当前知识点是否达到进入下一阶段的标准。",
                progress >= 100 ? "READY" : "PENDING",
                null,
                "开始检测",
                "/engine",
                activeStep,
                null
            ));
        }
        if (!dueMistakes.isEmpty()) {
            tasks.add(new DailyTaskItem(
                "mistake-review",
                "MISTAKE_REVIEW",
                "复习到期错题",
                "今天有 " + dueMistakes.size() + " 道错题进入复习窗口。",
                "READY",
                null,
                "开始复习",
                "/mistakes",
                Map.of("status", "due", "limit", dueMistakes.size()),
                dueMistakes.getFirst().nextReviewAt()
            ));
        }
        if (!resources.isEmpty()) {
            long completed = resources.stream().filter(item -> Boolean.TRUE.equals(item.completed())).count();
            tasks.add(new DailyTaskItem(
                "recommended-resources",
                "RESOURCE",
                "学习推荐资源",
                "优先完成当前阶段匹配度最高的 " + resources.size() + " 个资源。",
                completed >= resources.size() ? "COMPLETED" : "READY",
                resources.isEmpty() ? 0 : Math.round(completed * 100f / resources.size()),
                "查看资源",
                "/resources",
                Map.of("sort", "comprehensive"),
                null
            ));
        }
        graph.nodes().stream()
            .filter(node -> "WEAK".equals(node.status()) || "IN_PROGRESS".equals(node.status()))
            .limit(1)
            .findFirst()
            .ifPresent(node -> tasks.add(new DailyTaskItem(
                "knowledge:" + node.key(),
                "KNOWLEDGE",
                "补强知识点：" + node.topic(),
                "当前掌握度 " + Math.round(node.mastery() * 100) + "%，建议先看关联资源再做微练习。",
                "READY",
                Math.round((float) node.mastery() * 100),
                "查看图谱",
                "/profile?node=" + node.key(),
                Map.of("nodeKey", node.key(), "topic", node.topic()),
                null
            )));
        return tasks;
    }

    private String nextAction(List<DailyTaskItem> tasks) {
        return tasks.stream()
            .filter(task -> !"COMPLETED".equals(task.status()))
            .map(DailyTaskItem::title)
            .findFirst()
            .orElse("今日学习任务已完成");
    }

    private List<String> buildKnowledgeNextActions(
        KnowledgeNodeDto node,
        List<MistakeRecordResponse> mistakes,
        List<ResourceItemResponse> resources,
        List<KnowledgeNodeDto> prerequisites
    ) {
        List<String> actions = new ArrayList<>();
        if (!prerequisites.isEmpty()) {
            actions.add("先补齐前置知识：" + prerequisites.stream().map(KnowledgeNodeDto::topic).limit(2).toList());
        }
        if (!resources.isEmpty()) {
            actions.add("学习关联资源：" + resources.getFirst().title());
        }
        if (!mistakes.isEmpty()) {
            actions.add("复盘相关错题：" + mistakes.getFirst().stem());
        }
        if (!"MASTERED".equals(node.status())) {
            actions.add("完成 3-5 道针对性微练习并回流画像");
        }
        return actions;
    }

    private List<TrainingMicroPractice> buildMicroPractices(
        String mistakeType,
        String knowledgeTag,
        List<MistakeRecordResponse> representatives
    ) {
        List<String> tags = List.of(knowledgeTag);
        String basePrompt = representatives.isEmpty()
            ? "围绕「" + knowledgeTag + "」设计一道新情境题，并解释每一步判断依据。"
            : "参考代表题：「" + representatives.getFirst().stem() + "」，换一个新情境重新作答。";
        return List.of(
            new TrainingMicroPractice(
                campId(mistakeType, knowledgeTag) + ":concept",
                "错因定位",
                "先说清旧思路哪里会导致错误，再给出修正后的判断规则。",
                "BASIC",
                tags,
                "请分析「" + knowledgeTag + "」中这类" + mistakeTypeLabel(mistakeType) + "的根因：" + basePrompt
            ),
            new TrainingMicroPractice(
                campId(mistakeType, knowledgeTag) + ":transfer",
                "迁移练习",
                "把同一知识点应用到未见过的新题，验证是否真正掌握。",
                "INTERMEDIATE",
                tags,
                "请生成一道关于「" + knowledgeTag + "」的新情境迁移题，并要求我先独立作答。"
            )
        );
    }

    private RowMapper<MistakeRecordResponse> mistakeRowMapper() {
        return (rs, rowNum) -> new MistakeRecordResponse(
            (UUID) rs.getObject("id"),
            (UUID) rs.getObject("practice_item_id"),
            (UUID) rs.getObject("last_submission_id"),
            rs.getString("question_type"),
            rs.getString("stem"),
            parseStringList(rs.getString("options_json")),
            parseMap(rs.getString("standard_answer")),
            readAnswer(parseMap(rs.getString("answer_json"))),
            parseMap(rs.getString("judge_result_json")),
            rs.getBigDecimal("score"),
            readOffset(rs, "submitted_at"),
            parseStringList(rs.getString("knowledge_tags")),
            rs.getString("difficulty_level"),
            rs.getString("mistake_type"),
            rs.getString("user_note"),
            rs.getInt("wrong_count"),
            rs.getInt("review_count"),
            readOffset(rs, "next_review_at"),
            rs.getBigDecimal("ease_factor"),
            rs.getInt("interval_days"),
            rs.getBoolean("mastered"),
            readOffset(rs, "first_wrong_at"),
            readOffset(rs, "last_wrong_at"),
            readOffset(rs, "created_at"),
            readOffset(rs, "updated_at")
        );
    }

    private String readAnswer(Map<String, Object> answerJson) {
        Object answer = firstValue(answerJson, "answer", "value", "text");
        if (answer == null) {
            return "";
        }
        if (answer instanceof String text) {
            return text;
        }
        return writeJson(answer);
    }

    private List<String> parseStringList(String raw) {
        if (raw == null || raw.isBlank()) {
            return List.of();
        }
        try {
            if (raw.trim().startsWith("[")) {
                return objectMapper.readValue(raw, STRING_LIST);
            }
            return List.of(raw);
        } catch (JsonProcessingException ex) {
            return List.of();
        }
    }

    private Map<String, Object> parseMap(String raw) {
        if (raw == null || raw.isBlank()) {
            return Map.of();
        }
        try {
            return objectMapper.readValue(raw, STRING_OBJECT_MAP);
        } catch (JsonProcessingException ex) {
            return Map.of();
        }
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException ex) {
            return "";
        }
    }

    private OffsetDateTime readOffset(ResultSet rs, String column) throws SQLException {
        return rs.getObject(column, OffsetDateTime.class);
    }

    private OffsetDateTime readOffset(Object value) {
        if (value instanceof OffsetDateTime offsetDateTime) {
            return offsetDateTime;
        }
        return null;
    }

    private int readInt(Object value) {
        if (value instanceof Number number) {
            return number.intValue();
        }
        if (value instanceof String text) {
            try {
                return Integer.parseInt(text);
            } catch (NumberFormatException ignored) {
                return 0;
            }
        }
        return 0;
    }

    private Object firstValue(Map<String, Object> map, String... keys) {
        for (String key : keys) {
            if (map.containsKey(key)) {
                return map.get(key);
            }
        }
        return null;
    }

    private String firstNonBlank(String... values) {
        for (String value : values) {
            if (value != null && !value.isBlank()) {
                return value;
            }
        }
        return "";
    }

    private String safeString(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private String campId(String mistakeType, String knowledgeTag) {
        return (mistakeType + ":" + knowledgeTag)
            .toLowerCase(Locale.ROOT)
            .replaceAll("[^\\p{IsHan}\\p{Alnum}:_-]+", "_");
    }

    private String campTitle(String mistakeType, String knowledgeTag) {
        return mistakeTypeLabel(mistakeType) + " · " + knowledgeTag;
    }

    private String mistakeTypeLabel(String mistakeType) {
        return switch (mistakeType) {
            case "conceptual" -> "概念理解";
            case "procedural" -> "步骤方法";
            case "careless" -> "粗心失误";
            default -> "未分类错因";
        };
    }

    private String campExplanation(String mistakeType, String knowledgeTag) {
        return switch (mistakeType) {
            case "conceptual" -> "围绕「" + knowledgeTag + "」的概念边界、反例和适用条件进行修正。";
            case "procedural" -> "围绕「" + knowledgeTag + "」的解题步骤、顺序和中间判断进行训练。";
            case "careless" -> "围绕「" + knowledgeTag + "」的审题、单位、条件遗漏和最终检查建立检查清单。";
            default -> "先补全错因分类，再用代表题定位最常见的错误模式。";
        };
    }

    private double masteryChange(int reviewCount, int wrongCount, int masteredCount) {
        if (wrongCount <= 0) {
            return masteredCount > 0 ? 1.0 : 0.0;
        }
        double reviewEffect = Math.min(1.0, reviewCount / (double) Math.max(1, wrongCount));
        double masteredEffect = Math.min(1.0, masteredCount / (double) Math.max(1, wrongCount));
        return BigDecimal.valueOf(reviewEffect * 0.65 + masteredEffect * 0.35)
            .setScale(3, java.math.RoundingMode.HALF_UP)
            .doubleValue();
    }
}
