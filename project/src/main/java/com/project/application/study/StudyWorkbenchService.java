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
import com.project.api.study.dto.DailyStudyWorkbenchResponse.DailyExecutionPlan;
import com.project.api.study.dto.DailyStudyWorkbenchResponse.LearningSessionStep;
import com.project.api.study.dto.DailyStudyWorkbenchResponse.PlanSupportItem;
import com.project.api.study.dto.DailyStudyWorkbenchResponse.WorkbenchSummary;
import com.project.api.study.dto.KnowledgeNodeDetailResponse;
import com.project.api.study.dto.MistakeTrainingCampResponse;
import com.project.api.study.dto.MistakeTrainingCampResponse.MistakeCampGroup;
import com.project.api.study.dto.MistakeTrainingCampResponse.TrainingCampSummary;
import com.project.api.study.dto.MistakeTrainingCampResponse.TrainingMicroPractice;
import com.project.application.common.ApplicationException;
import com.project.application.learningpath.LearningPathQueryService;
import com.project.application.learningpath.PersonalizedLearningRefreshService;
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
    private static final Set<String> GENERIC_CJK_RESOURCE_TOKENS = Set.of(
        "知识", "基础", "概念", "原理", "应用", "学习", "方法", "技能", "专题", "系统"
    );
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
    private final PersonalizedLearningRefreshService refreshService;

    public StudyWorkbenchService(
        NamedParameterJdbcTemplate jdbcTemplate,
        ObjectMapper objectMapper,
        LearningPathQueryService learningPathQueryService,
        ResourceLibraryService resourceLibraryService,
        LearnerKnowledgeGraphService learnerKnowledgeGraphService,
        UserProfileQueryService userProfileQueryService,
        PersonalizedLearningRefreshService refreshService
    ) {
        this.jdbcTemplate = jdbcTemplate;
        this.objectMapper = objectMapper;
        this.learningPathQueryService = learningPathQueryService;
        this.resourceLibraryService = resourceLibraryService;
        this.learnerKnowledgeGraphService = learnerKnowledgeGraphService;
        this.userProfileQueryService = userProfileQueryService;
        this.refreshService = refreshService;
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
        List<ResourceItemResponse> resources = resourceLibraryService.recommendations(userId, RECOMMENDED_RESOURCE_LIMIT).stream()
            .map(this::compactWorkbenchResource)
            .toList();
        KnowledgeGraphResponse graph = learnerKnowledgeGraphService.getGraph(currentUser, userId);
        UserProfileResponse profile = userProfileQueryService.getCurrentProfile(currentUser, userId);

        List<DailyTaskItem> tasks = buildDailyTasks(activeStep, dueMistakes, resources, graph);
        DailyExecutionPlan executionPlan = buildExecutionPlan(tasks, activeStep, dueMistakes, resources, graph);
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
            executionPlan,
            tasks,
            dueMistakes,
            resources,
            graph,
            profile,
            dataAvailable
        );
    }

    @Transactional
    public DailyStudyWorkbenchResponse refreshDaily(JwtAuthenticatedUser currentUser) {
        UUID userId = currentUser.userId();
        LearningPathCurrentResponse currentPath = learningPathQueryService.getCurrent(userId);
        refreshService.triggerResourceRecommendationRefresh(
            userId,
            "刷新今日学习工作台推荐资源",
            currentPath.learningPath(),
            currentPath.pushedResources()
        );
        return daily(currentUser);
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
            .filter(result -> isKnowledgeResourceRelevant(node, result.resource(), result.score()))
            .map(result -> result.resource())
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
                "source", "KNOWLEDGE_GRAPH_DETAIL",
                "semanticScope", semanticScope(
                    node.topic(),
                    node.topic(),
                    List.of(node.topic()),
                    "KNOWLEDGE_GRAPH_DETAIL",
                    List.of("nodeKey=" + node.key(), "status=" + node.status())
                )
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
                    "source", "MISTAKE_TRAINING_CAMP",
                    "semanticScope", semanticScope(
                        knowledgeTag,
                        knowledgeTag,
                        List.of(knowledgeTag),
                        "MISTAKE_TRAINING_CAMP",
                        representatives.stream()
                            .map(MistakeRecordResponse::stem)
                            .filter(text -> text != null && !text.isBlank())
                            .limit(3)
                            .toList()
                    )
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

    private ResourceItemResponse compactWorkbenchResource(ResourceItemResponse resource) {
        return new ResourceItemResponse(
            resource.id(),
            resource.title(),
            resource.domain(),
            resource.resourceType(),
            resource.displayType(),
            resource.difficultyLevel(),
            resource.sourceKind(),
            resource.summaryText(),
            resource.tags(),
            resource.sourceUrl(),
            resource.sourceName(),
            resource.coverUrl(),
            resource.license(),
            resource.copyrightStatus(),
            resource.accessibilityStatus(),
            resource.httpStatus(),
            resource.lastCheckedAt(),
            resource.qualityScore(),
            resource.popularityScore(),
            resource.favoriteCount(),
            resource.viewCount(),
            resource.likeCount(),
            resource.durationMinutes(),
            resource.fileSizeBytes(),
            resource.favorite(),
            resource.progress(),
            resource.completed(),
            resource.lastStudyAt(),
            resource.createdAt(),
            resource.updatedAt(),
            resource.csCategory(),
            resource.csSubcategory(),
            Map.of()
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
        if (!isSparseGraph(graph)) {
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
                    knowledgeGraphRoute(node.key()),
                    Map.of("nodeKey", node.key(), "topic", node.topic()),
                    null
                )));
        }
        return tasks;
    }

    private DailyExecutionPlan buildExecutionPlan(
        List<DailyTaskItem> tasks,
        Map<String, Object> activeStep,
        List<MistakeRecordResponse> dueMistakes,
        List<ResourceItemResponse> resources,
        KnowledgeGraphResponse graph
    ) {
        DailyTaskItem primaryTask = choosePrimaryTask(tasks);
        List<LearningSessionStep> steps = buildSessionSteps(tasks, activeStep, graph);
        return new DailyExecutionPlan(
            primaryTask.title(),
            primarySubtitle(primaryTask),
            focusReason(primaryTask, dueMistakes, resources, graph),
            successCriteria(primaryTask),
            estimateMinutes(primaryTask),
            primaryTask,
            steps,
            buildSupportItems(activeStep, dueMistakes, resources, graph)
        );
    }

    private DailyTaskItem choosePrimaryTask(List<DailyTaskItem> tasks) {
        return firstTask(tasks, "MISTAKE_REVIEW", "READY")
            .or(() -> firstTask(tasks, "STAGE_TEST", "READY"))
            .or(() -> firstTask(tasks, "RESOURCE", "READY"))
            .or(() -> firstTask(tasks, "STAGE", "IN_PROGRESS"))
            .or(() -> firstTask(tasks, "KNOWLEDGE", "READY"))
            .or(() -> tasks.stream().filter(task -> !"COMPLETED".equals(task.status())).findFirst())
            .or(() -> tasks.stream().findFirst())
            .orElseGet(() -> new DailyTaskItem(
                "onboarding",
                "ONBOARDING",
                "先完成一次学习对话",
                "用一次问答、练习或画像初始化建立今日学习记录。",
                "READY",
                null,
                "开始学习",
                "/",
                Map.of("source", "DAILY_EXECUTION_PLAN"),
                null
            ));
    }

    private java.util.Optional<DailyTaskItem> firstTask(List<DailyTaskItem> tasks, String type, String status) {
        return tasks.stream()
            .filter(task -> type.equals(task.type()))
            .filter(task -> status.equals(task.status()))
            .findFirst();
    }

    private List<LearningSessionStep> buildSessionSteps(
        List<DailyTaskItem> tasks,
        Map<String, Object> activeStep,
        KnowledgeGraphResponse graph
    ) {
        DailyTaskItem mistakeTask = firstTask(tasks, "MISTAKE_REVIEW", "READY").orElse(null);
        DailyTaskItem resourceTask = firstTask(tasks, "RESOURCE", "READY").orElse(null);
        DailyTaskItem stageTask = firstTask(tasks, "STAGE", "IN_PROGRESS").orElse(null);
        DailyTaskItem stageTestTask = firstTask(tasks, "STAGE_TEST", "READY").orElse(null);
        DailyTaskItem knowledgeTask = firstTask(tasks, "KNOWLEDGE", "READY").orElse(null);
        String weakTopic = graph.nodes().stream()
            .filter(node -> "WEAK".equals(node.status()) || "IN_PROGRESS".equals(node.status()))
            .min(Comparator.comparingDouble(KnowledgeNodeDto::mastery))
            .map(KnowledgeNodeDto::topic)
            .orElse("上次薄弱点");
        String stageTitle = firstNonBlank(safeString(activeStep == null ? null : activeStep.get("title")), "当前阶段");
        DailyTaskItem strengthenTask = resourceTask != null ? resourceTask : stageTask != null ? stageTask : knowledgeTask;
        return List.of(
            stepFromTask(
                "warmup",
                "热身",
                mistakeTask,
                mistakeTask == null ? "回忆上次卡住的知识点" : "先复习到期错题",
                mistakeTask == null ? "用 3-5 分钟主动回忆「" + weakTopic + "」，不要先看答案。" : "先做提取练习，把遗忘风险最高的题清掉。",
                "READY",
                6,
                mistakeTask == null ? "查看画像" : null,
                mistakeTask == null ? "/profile" : null
            ),
            stepFromTask(
                "strengthen",
                "补强",
                strengthenTask,
                strengthenTask == null ? "补齐当前阶段输入" : strengthenTask.title(),
                strengthenTask == null ? "围绕「" + stageTitle + "」选择一个资源或让 AI 生成讲解。" : strengthenTask.description(),
                "PENDING",
                12,
                strengthenTask == null ? "打开学习引擎" : null,
                strengthenTask == null ? "/engine" : null
            ),
            stepFromTask(
                "check",
                "检测",
                stageTestTask,
                stageTestTask == null ? "准备阶段检测" : stageTestTask.title(),
                stageTestTask == null ? "先完成补强材料，进度达标后再开始阶段检测。" : stageTestTask.description(),
                stageTestTask == null ? "PENDING" : stageTestTask.status(),
                10,
                stageTestTask == null ? "继续补强" : null,
                stageTestTask == null ? "/engine" : null
            ),
            new LearningSessionStep(
                "reflect",
                "反思",
                "记录今天的变化",
                "把错因、掌握度变化和下一次复习点沉淀到笔记或画像里。",
                "PENDING",
                4,
                "写复盘",
                "/notes",
                null,
                null
            )
        );
    }

    private LearningSessionStep stepFromTask(
        String id,
        String phase,
        DailyTaskItem task,
        String fallbackTitle,
        String fallbackDescription,
        String fallbackStatus,
        int minutes,
        String fallbackActionLabel,
        String fallbackActionRoute
    ) {
        return new LearningSessionStep(
            id,
            phase,
            task == null ? fallbackTitle : task.title(),
            task == null ? fallbackDescription : task.description(),
            task == null ? fallbackStatus : task.status(),
            minutes,
            task == null ? fallbackActionLabel : task.actionLabel(),
            task == null ? fallbackActionRoute : task.actionRoute(),
            task == null ? null : task.id(),
            task == null ? null : task.type()
        );
    }

    private List<PlanSupportItem> buildSupportItems(
        Map<String, Object> activeStep,
        List<MistakeRecordResponse> dueMistakes,
        List<ResourceItemResponse> resources,
        KnowledgeGraphResponse graph
    ) {
        List<PlanSupportItem> items = new ArrayList<>();
        if (activeStep != null && !activeStep.isEmpty()) {
            items.add(new PlanSupportItem(
                "active-step",
                "STAGE",
                "当前阶段：" + firstNonBlank(safeString(activeStep.get("title")), "未命名阶段"),
                firstNonBlank(safeString(activeStep.get("checkpoint")), "阶段进度会由资源学习、检测和错题回流共同推动。"),
                "/engine"
            ));
        }
        if (!dueMistakes.isEmpty()) {
            items.add(new PlanSupportItem(
                "due-mistakes",
                "MISTAKE_REVIEW",
                "到期错题 " + dueMistakes.size() + " 道",
                "最早一题复习时间：" + formatSupportTime(dueMistakes.getFirst().nextReviewAt()),
                "/mistakes"
            ));
        }
        if (!resources.isEmpty()) {
            ResourceItemResponse resource = resources.stream()
                .filter(item -> !Boolean.TRUE.equals(item.completed()))
                .findFirst()
                .orElse(resources.getFirst());
            items.add(new PlanSupportItem(
                "resource:" + resource.id(),
                "RESOURCE",
                "推荐资源：" + resource.title(),
                "匹配当前阶段，可作为今天的主要输入材料。",
                "/resources"
            ));
        }
        graph.nodes().stream()
            .filter(node -> "WEAK".equals(node.status()) || "IN_PROGRESS".equals(node.status()))
            .sorted(Comparator.comparingDouble(KnowledgeNodeDto::mastery))
            .limit(3)
            .forEach(node -> items.add(new PlanSupportItem(
                "knowledge:" + node.key(),
                "KNOWLEDGE",
                "薄弱点：" + node.topic(),
                "当前掌握度约 " + Math.round(node.mastery() * 100) + "%，适合作为补强依据。",
                knowledgeGraphRoute(node.key())
            )));
        return items;
    }

    private String primarySubtitle(DailyTaskItem task) {
        return switch (task.type()) {
            case "MISTAKE_REVIEW" -> "先做提取练习，再决定今天补什么。";
            case "STAGE_TEST" -> "当前阶段已到检测点，先验证能否进入下一阶段。";
            case "RESOURCE" -> "先补齐输入材料，再用练习检查是否真的会用。";
            case "STAGE" -> "把当前学习路径推进成一轮可完成的行动。";
            case "KNOWLEDGE" -> "围绕薄弱点做一次定向补强。";
            default -> "从一个可完成的小任务开始建立学习记录。";
        };
    }

    private String focusReason(
        DailyTaskItem task,
        List<MistakeRecordResponse> dueMistakes,
        List<ResourceItemResponse> resources,
        KnowledgeGraphResponse graph
    ) {
        return switch (task.type()) {
            case "MISTAKE_REVIEW" -> "有 " + dueMistakes.size() + " 道错题进入复习窗口，先清掉遗忘风险最高的内容。";
            case "STAGE_TEST" -> "当前阶段已具备检测条件，检测结果会直接决定是否推进学习路径。";
            case "RESOURCE" -> "有 " + resources.size() + " 个资源匹配当前阶段，优先完成一个未学资源。";
            case "KNOWLEDGE" -> graph.nodes().stream()
                .filter(node -> "WEAK".equals(node.status()) || "IN_PROGRESS".equals(node.status()))
                .min(Comparator.comparingDouble(KnowledgeNodeDto::mastery))
                .map(node -> "知识图谱显示「" + node.topic() + "」掌握度约 " + Math.round(node.mastery() * 100) + "%。")
                .orElse("知识图谱提示需要做一次定向补强。");
            default -> "当前学习记录还少，先完成一次可回流的学习动作。";
        };
    }

    private String successCriteria(DailyTaskItem task) {
        return switch (task.type()) {
            case "MISTAKE_REVIEW" -> "完成到期错题复习，并记录至少一个错因。";
            case "STAGE_TEST" -> "完成 10 题阶段检测，结果能回流到画像或路径。";
            case "RESOURCE" -> "学习一个推荐资源并把进度标记为完成。";
            case "STAGE" -> "推进当前阶段，并明确下一次检测条件。";
            case "KNOWLEDGE" -> "完成薄弱点查看和一次针对练习。";
            default -> "完成一次问答、练习或资源学习，生成可追踪记录。";
        };
    }

    private int estimateMinutes(DailyTaskItem task) {
        return switch (task.type()) {
            case "MISTAKE_REVIEW" -> 18;
            case "STAGE_TEST" -> 16;
            case "RESOURCE" -> 22;
            case "STAGE" -> 25;
            case "KNOWLEDGE" -> 20;
            default -> 12;
        };
    }

    private String formatSupportTime(OffsetDateTime value) {
        if (value == null) {
            return "今天";
        }
        return value.toLocalDate().toString();
    }

    private boolean isKnowledgeResourceRelevant(KnowledgeNodeDto node, ResourceItemResponse resource, double score) {
        if (resource == null) {
            return false;
        }
        Set<String> topicTokens = resourceMatchTokens(node.topic());
        if (topicTokens.isEmpty()) {
            return score >= 0.85;
        }
        String searchableText = resourceSearchableText(resource);
        Set<String> matchedTokens = topicTokens.stream()
            .filter(searchableText::contains)
            .collect(java.util.stream.Collectors.toCollection(LinkedHashSet::new));
        boolean hasStrongMatch = matchedTokens.stream().anyMatch(this::isStrongResourceToken);
        if (hasStrongMatch) {
            return true;
        }
        return score >= 0.9 && matchedTokens.size() >= 2;
    }

    private Set<String> resourceMatchTokens(String topic) {
        String normalized = safeString(topic).toLowerCase(Locale.ROOT);
        if (normalized.isBlank()) {
            return Set.of();
        }
        Set<String> tokens = new LinkedHashSet<>();
        collectWordTokens(tokens, normalized);
        collectCjkFragments(tokens, normalized);
        return tokens;
    }

    private void collectWordTokens(Set<String> tokens, String text) {
        StringBuilder current = new StringBuilder();
        for (int index = 0; index < text.length(); index += 1) {
            char ch = text.charAt(index);
            if (Character.isLetterOrDigit(ch)) {
                current.append(ch);
            } else {
                addWordToken(tokens, current);
            }
        }
        addWordToken(tokens, current);
    }

    private void addWordToken(Set<String> tokens, StringBuilder current) {
        if (current.length() >= 3) {
            tokens.add(current.toString());
        }
        current.setLength(0);
    }

    private void collectCjkFragments(Set<String> tokens, String text) {
        StringBuilder current = new StringBuilder();
        for (int index = 0; index < text.length(); index += 1) {
            char ch = text.charAt(index);
            if (isCjk(ch)) {
                current.append(ch);
            } else {
                addCjkFragments(tokens, current);
            }
        }
        addCjkFragments(tokens, current);
    }

    private void addCjkFragments(Set<String> tokens, StringBuilder current) {
        if (current.length() >= 2) {
            tokens.add(current.toString());
            for (int width = 2; width <= Math.min(4, current.length()); width += 1) {
                for (int index = 0; index <= current.length() - width; index += 1) {
                    String token = current.substring(index, index + width);
                    if (!GENERIC_CJK_RESOURCE_TOKENS.contains(token)) {
                        tokens.add(token);
                    }
                }
            }
        }
        current.setLength(0);
    }

    private boolean isStrongResourceToken(String token) {
        return token.length() >= 3 || !GENERIC_CJK_RESOURCE_TOKENS.contains(token);
    }

    private boolean isCjk(char ch) {
        Character.UnicodeScript script = Character.UnicodeScript.of(ch);
        return script == Character.UnicodeScript.HAN;
    }

    private String resourceSearchableText(ResourceItemResponse resource) {
        StringBuilder text = new StringBuilder();
        appendSearchable(text, resource.title());
        appendSearchable(text, resource.summaryText());
        appendSearchable(text, resource.domain());
        appendSearchable(text, resource.resourceType());
        appendSearchable(text, resource.displayType());
        appendSearchable(text, resource.csCategory());
        appendSearchable(text, resource.csSubcategory());
        return text.toString().toLowerCase(Locale.ROOT);
    }

    private void appendSearchable(StringBuilder text, Object value) {
        if (value == null) {
            return;
        }
        if (value instanceof Iterable<?> iterable) {
            iterable.forEach(item -> appendSearchable(text, item));
            return;
        }
        if (value instanceof Map<?, ?> map) {
            map.values().forEach(item -> appendSearchable(text, item));
            return;
        }
        text.append(' ').append(value);
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

    private boolean isSparseGraph(KnowledgeGraphResponse graph) {
        return graph.metadata() != null && graph.metadata().sparseState();
    }

    private String knowledgeGraphRoute(String nodeKey) {
        return "/knowledge-graph?node=" + nodeKey;
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

    private Map<String, Object> semanticScope(
        String topic,
        String rawTopic,
        List<String> knowledgeTags,
        String source,
        List<String> evidence
    ) {
        Map<String, Object> scope = new LinkedHashMap<>();
        scope.put("topic", safeString(topic));
        scope.put("rawTopic", safeString(rawTopic));
        scope.put("knowledgeTags", knowledgeTags == null ? List.of() : knowledgeTags.stream()
            .filter(tag -> tag != null && !tag.isBlank())
            .distinct()
            .toList());
        scope.put("source", source);
        scope.put("evidence", evidence == null ? List.of() : evidence.stream()
            .filter(item -> item != null && !item.isBlank())
            .distinct()
            .limit(5)
            .toList());
        return scope;
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
