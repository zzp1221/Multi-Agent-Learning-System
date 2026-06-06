package com.project.application.learningpath;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.learningpath.dto.LearningPathCurrentResponse;
import com.project.api.smartengine.dto.TaskStatusResponse;
import com.project.application.smartengine.TaskStateMachineService;
import com.project.domain.task.ServiceType;
import com.project.domain.task.SmartEngineTask;
import com.project.domain.task.SmartEngineTaskRepository;
import com.project.domain.task.TaskStatus;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

/**
 * 读取当前持久化学习路径，资源计划来自最近一次个性化后台任务。
 */
@Service
public class LearningPathQueryService {

    private static final Duration LIVE_TASK_STALE_AFTER = Duration.ofMinutes(30);

    private static final String CURRENT_PLAN_SQL = """
        SELECT p.id,
               p.user_id,
               p.course_id,
               p.status,
               p.plan_json,
               p.updated_at,
               s.version,
               s.trigger_source,
               s.summary_text
        FROM app.learning_plan p
        LEFT JOIN LATERAL (
            SELECT version, trigger_source, summary_text
            FROM app.learning_plan_snapshot
            WHERE plan_id = p.id
            ORDER BY version DESC, created_at DESC
            LIMIT 1
        ) s ON TRUE
        WHERE p.user_id = :userId
          AND p.status = 'ACTIVE'
        ORDER BY p.updated_at DESC
        LIMIT 1
        """;

    private final NamedParameterJdbcTemplate jdbcTemplate;
    private final SmartEngineTaskRepository taskRepository;
    private final TaskStateMachineService taskStateMachineService;
    private final ObjectMapper objectMapper;

    public LearningPathQueryService(
        NamedParameterJdbcTemplate jdbcTemplate,
        SmartEngineTaskRepository taskRepository,
        TaskStateMachineService taskStateMachineService,
        ObjectMapper objectMapper
    ) {
        this.jdbcTemplate = jdbcTemplate;
        this.taskRepository = taskRepository;
        this.taskStateMachineService = taskStateMachineService;
        this.objectMapper = objectMapper;
    }

    @Transactional(readOnly = true)
    public LearningPathCurrentResponse getCurrent(UUID userId) {
        Optional<PlanRecord> currentPlan = loadCurrentPlan(userId);
        Optional<SmartEngineTask> refreshTask = taskRepository.findFirstByUserIdAndServiceTypeOrderByCreatedAtDesc(
            userId,
            ServiceType.PERSONALIZED_LEARNING
        );
        Optional<SmartEngineTask> resourceRefreshTask = taskRepository.findFirstByUserIdAndServiceTypeOrderByCreatedAtDesc(
            userId,
            ServiceType.RESOURCE_PUSH
        );
        TaskStatusResponse refreshTaskResponse = refreshTask
            .map(task -> taskStatusResponse(task, userId))
            .orElse(null);
        TaskStatusResponse resourceRefreshTaskResponse = resourceRefreshTask
            .map(task -> taskStatusResponse(task, userId))
            .orElse(null);
        Map<String, Object> latestCompletedSummary = taskRepository.findTop5ByUserIdAndServiceTypeOrderByCreatedAtDesc(
                userId,
                ServiceType.PERSONALIZED_LEARNING
            ).stream()
            .filter(task -> task.getTaskStatus() == com.project.domain.task.TaskStatus.COMPLETED)
            .map(task -> safeMap(task.getResponseSummary()))
            .filter(summary -> !summary.isEmpty())
            .findFirst()
            .orElseGet(LinkedHashMap::new);
        Map<String, Object> latestResourceSummary = taskRepository.findTop5ByUserIdAndServiceTypeOrderByCreatedAtDesc(
                userId,
                ServiceType.RESOURCE_PUSH
            ).stream()
            .filter(task -> task.getTaskStatus() == com.project.domain.task.TaskStatus.COMPLETED)
            .map(task -> safeMap(task.getResponseSummary()))
            .filter(summary -> !summary.isEmpty())
            .findFirst()
            .orElseGet(LinkedHashMap::new);
        Map<String, Object> fallbackLearningPath = safeMap(latestCompletedSummary.get("learningPath"));
        Map<String, Object> resourcePushPlan = firstNonEmptyMap(
            latestResourceSummary.get("resourcePushPlan"),
            latestCompletedSummary.get("resourcePushPlan")
        );
        List<Map<String, Object>> pushedResources = firstNonEmptyListOfMaps(
            latestResourceSummary.get("pushedResources"),
            latestCompletedSummary.get("pushedResources")
        );

        return currentPlan
            .map(plan -> {
                Map<String, Object> learningPath = normalizeLearningPath(plan.planJson().isEmpty()
                    ? fallbackLearningPath
                    : plan.planJson());
                Map<String, Object> currentResourcePushPlan = firstNonEmptyMap(resourcePushPlan, learningPath.get("resourcePushPlan"));
                Map<String, Object> alignedResourcePushPlan = alignResourcePushPlan(userId, learningPath, currentResourcePushPlan);
                return new LearningPathCurrentResponse(
                    plan.planId(),
                    plan.userId(),
                    plan.courseId(),
                    plan.status(),
                    learningPath,
                    resolveActiveStep(learningPath),
                    alignedResourcePushPlan,
                    flattenResourcePushPlan(alignedResourcePushPlan),
                    plan.version(),
                    plan.triggerSource(),
                    plan.summary(),
                    plan.updatedAt(),
                    refreshTaskResponse,
                    resourceRefreshTaskResponse
                );
            })
            .orElseGet(() -> {
                Map<String, Object> learningPath = normalizeLearningPath(fallbackLearningPath);
                Map<String, Object> currentResourcePushPlan = firstNonEmptyMap(resourcePushPlan, learningPath.get("resourcePushPlan"));
                Map<String, Object> alignedResourcePushPlan = alignResourcePushPlan(userId, learningPath, currentResourcePushPlan);
                return new LearningPathCurrentResponse(
                    null,
                    userId,
                    null,
                    learningPath.isEmpty() ? "EMPTY" : "ACTIVE",
                    learningPath,
                    resolveActiveStep(learningPath),
                    alignedResourcePushPlan,
                    flattenResourcePushPlan(alignedResourcePushPlan),
                    null,
                    learningPath.isEmpty() ? null : "TASK_RESPONSE_FALLBACK",
                    readString(latestCompletedSummary.get("summary")),
                    null,
                    refreshTaskResponse,
                    resourceRefreshTaskResponse
                );
            });
    }

    @Transactional(readOnly = true)
    public Map<String, Object> getCurrentLearningPathPayload(UUID userId) {
        LearningPathCurrentResponse current = getCurrent(userId);
        return safeMap(current.learningPath());
    }

    private Optional<PlanRecord> loadCurrentPlan(UUID userId) {
        List<PlanRecord> records = jdbcTemplate.query(
            CURRENT_PLAN_SQL,
            new MapSqlParameterSource("userId", userId),
            (rs, rowNum) -> mapPlanRecord(rs)
        );
        return records.stream().findFirst();
    }

    private TaskStatusResponse taskStatusResponse(SmartEngineTask task, UUID userId) {
        if (!isStaleLiveTask(task)) {
            return taskStateMachineService.getOwnedTaskStatus(task.getId(), userId);
        }
        return new TaskStatusResponse(
            task.getId(),
            task.getTraceId(),
            task.getServiceType(),
            TaskStatus.TIMEOUT,
            "timeout",
            task.getProgressPercent(),
            "TASK_STALE",
            "后台任务长时间未完成，请重新生成学习路径",
            task.getResponseSummary()
        );
    }

    private boolean isStaleLiveTask(SmartEngineTask task) {
        TaskStatus status = task.getTaskStatus();
        if (status != TaskStatus.PENDING && status != TaskStatus.RUNNING) {
            return false;
        }
        OffsetDateTime createdAt = task.getCreatedAt();
        return createdAt != null && createdAt.isBefore(OffsetDateTime.now().minus(LIVE_TASK_STALE_AFTER));
    }

    private Map<String, Object> alignResourcePushPlan(
        UUID userId,
        Map<String, Object> learningPath,
        Map<String, Object> existingPlan
    ) {
        List<Map<String, Object>> steps = safeListOfMaps(learningPath.get("steps"));
        if (steps.isEmpty()) {
            return sanitizeResourcePushPlan(existingPlan);
        }
        Map<String, Object> alignedPlan = new LinkedHashMap<>(safeMap(existingPlan));
        Map<String, List<Map<String, Object>>> existingByStep = existingStepResourcesByStep(existingPlan);
        List<Map<String, Object>> stepResources = new ArrayList<>();
        List<Map<String, Object>> coverageGaps = new ArrayList<>();
        String pathContext = learningContextText(learningPath);

        for (int index = 0; index < steps.size(); index += 1) {
            Map<String, Object> step = steps.get(index);
            String stepId = readString(step.get("stepId"));
            if (stepId.isBlank()) {
                stepId = "step-" + (index + 1);
            }
            String category = inferPreferredCategory(pathContext + " " + learningContextText(step));
            List<Map<String, Object>> resources = new ArrayList<>();
            for (Map<String, Object> resource : existingByStep.getOrDefault(stepId, List.of())) {
                Map<String, Object> sanitized = sanitizePathResource(resource, category);
                if (!sanitized.isEmpty()) {
                    resources.add(sanitized);
                }
            }
            if (resources.size() < 3) {
                resources.addAll(loadLibraryStepResources(userId, step, category, 3 - resources.size(), resources));
            }
            if (resources.isEmpty()) {
                coverageGaps.add(Map.of(
                    "stepId", stepId,
                    "missingResourceTypes", List.of("DOCUMENT", "VIDEO", "PRACTICAL_CASE"),
                    "reason", "当前学习阶段暂无真实可访问且方向匹配的资源"
                ));
            }
            stepResources.add(Map.of(
                "stepId", stepId,
                "stepTitle", readString(step.get("title")).isBlank() ? "学习阶段 " + (index + 1) : readString(step.get("title")),
                "targetKnowledgePoints", safeStringList(step.get("targetKnowledgePoints")),
                "resources", resources
            ));
        }
        alignedPlan.put("stepResources", stepResources);
        alignedPlan.put("coverageGaps", coverageGaps);
        alignedPlan.put("source", "learning_resource_alignment");
        return alignedPlan;
    }

    private Map<String, Object> sanitizeResourcePushPlan(Map<String, Object> existingPlan) {
        Map<String, Object> sanitizedPlan = new LinkedHashMap<>(safeMap(existingPlan));
        List<Map<String, Object>> sanitizedSteps = new ArrayList<>();
        for (Map<String, Object> rawStep : safeListOfMaps(existingPlan.get("stepResources"))) {
            List<Map<String, Object>> resources = new ArrayList<>();
            for (Map<String, Object> resource : safeListOfMaps(rawStep.get("resources"))) {
                Map<String, Object> sanitized = sanitizePathResource(resource, null);
                if (!sanitized.isEmpty()) {
                    resources.add(sanitized);
                }
            }
            Map<String, Object> step = new LinkedHashMap<>(rawStep);
            step.put("resources", resources);
            sanitizedSteps.add(step);
        }
        sanitizedPlan.put("stepResources", sanitizedSteps);
        sanitizedPlan.put("coverageGaps", List.of());
        return sanitizedPlan;
    }

    private Map<String, List<Map<String, Object>>> existingStepResourcesByStep(Map<String, Object> existingPlan) {
        Map<String, List<Map<String, Object>>> resourcesByStep = new LinkedHashMap<>();
        for (Map<String, Object> rawStep : safeListOfMaps(existingPlan.get("stepResources"))) {
            String stepId = readString(rawStep.get("stepId"));
            resourcesByStep.put(stepId, safeListOfMaps(rawStep.get("resources")));
        }
        return resourcesByStep;
    }

    private List<Map<String, Object>> loadLibraryStepResources(
        UUID userId,
        Map<String, Object> step,
        String category,
        int limit,
        List<Map<String, Object>> existingResources
    ) {
        if (limit <= 0) {
            return List.of();
        }
        MapSqlParameterSource params = new MapSqlParameterSource("userId", userId)
            .addValue("category", category)
            .addValue("limit", 60);
        List<Map<String, Object>> rows = new ArrayList<>(jdbcTemplate.queryForList(
            """
            SELECT lr.title,
                   lr.summary_text,
                   lr.resource_type::text AS resource_type,
                   COALESCE(NULLIF(upper(lr.metadata_json ->> 'displayType'), ''), lr.resource_type::text) AS display_type,
                   lr.metadata_json ->> 'sourceUrl' AS source_url,
                   lr.metadata_json ->> 'sourceName' AS source_name,
                   lr.metadata_json ->> 'csCategory' AS cs_category,
                   lr.tags::text AS tags_text,
                   COALESCE(
                     CASE WHEN lr.metadata_json ->> 'qualityScore' ~ '^-?[0-9]+([.][0-9]+)?$'
                       THEN (lr.metadata_json ->> 'qualityScore')::numeric
                     END,
                     0.5
                   ) AS quality_score
            FROM app.learning_resource lr
            WHERE lr.status = 'ACTIVE'
              AND lr.access_scope::text = 'GLOBAL'
              AND COALESCE(lr.metadata_json ->> 'sourceUrl', '') ~* '^https?://'
              AND COALESCE(lr.metadata_json ->> 'accessibilityStatus', '') = 'ACCESSIBLE'
              AND lr.resource_type::text NOT IN ('QUIZ', 'PRACTICE')
              AND COALESCE(NULLIF(upper(lr.metadata_json ->> 'displayType'), ''), lr.resource_type::text) NOT IN ('NOTE', 'QUIZ', 'PRACTICE')
              AND (:category IS NULL OR upper(COALESCE(NULLIF(lr.metadata_json ->> 'csCategory', ''), 'GENERAL_CS')) = :category)
            ORDER BY quality_score DESC, lr.updated_at DESC
            LIMIT :limit
            """,
            params
        ));
        List<String> usedUrls = existingResources.stream()
            .map(resource -> readString(resource.get("downloadUrl")).toLowerCase())
            .filter(url -> !url.isBlank())
            .toList();
        List<String> terms = stepSearchTerms(step);
        List<Map<String, Object>> resources = new ArrayList<>();
        rows.sort((left, right) -> Integer.compare(scoreLibraryResource(right, terms), scoreLibraryResource(left, terms)));
        for (Map<String, Object> row : rows) {
            String url = readString(row.get("source_url"));
            if (!isHttpUrl(url) || usedUrls.contains(url.toLowerCase())) {
                continue;
            }
            String displayType = normalizePathResourceType(readString(row.get("display_type")));
            if (displayType.equals("QUIZ") || displayType.equals("PRACTICE") || displayType.equals("NOTE")) {
                continue;
            }
            Map<String, Object> resource = new LinkedHashMap<>();
            resource.put("title", displayLibraryTitle(row));
            resource.put("resourceType", displayType.equals("COURSE") ? "DOCUMENT" : displayType);
            resource.put("source", "learning_resource");
            resource.put("sourceName", readString(row.get("source_name")));
            resource.put("downloadUrl", url);
            resource.put("summaryText", readString(row.get("summary_text")));
            resource.put("matchReason", "匹配当前学习阶段与 CS 方向");
            resource.put("csCategory", readString(row.get("cs_category")));
            resources.add(resource);
            if (resources.size() >= limit) {
                break;
            }
        }
        return resources;
    }

    private Map<String, Object> sanitizePathResource(Map<String, Object> resource, String category) {
        String url = readString(firstNonBlank(resource.get("downloadUrl"), resource.get("url")));
        if (!isHttpUrl(url)) {
            return new LinkedHashMap<>();
        }
        String type = normalizePathResourceType(firstNonBlank(resource.get("resourceType"), resource.get("type")));
        if (type.equals("QUIZ") || type.equals("PRACTICE")) {
            return new LinkedHashMap<>();
        }
        if (category != null && !category.isBlank() && !matchesCategoryText(resourceText(resource), category)) {
            return new LinkedHashMap<>();
        }
        Map<String, Object> sanitized = new LinkedHashMap<>(resource);
        sanitized.put("downloadUrl", url);
        sanitized.put("resourceType", type.equals("COURSE") ? "DOCUMENT" : type);
        return sanitized;
    }

    private static List<Map<String, Object>> flattenResourcePushPlan(Map<String, Object> plan) {
        List<Map<String, Object>> flattened = new ArrayList<>();
        for (Map<String, Object> step : safeListOfMaps(plan.get("stepResources"))) {
            String stepId = readString(step.get("stepId"));
            for (Map<String, Object> resource : safeListOfMaps(step.get("resources"))) {
                Map<String, Object> item = new LinkedHashMap<>(resource);
                item.putIfAbsent("stepId", stepId);
                flattened.add(item);
            }
        }
        return flattened;
    }

    private static String learningContextText(Map<String, Object> source) {
        List<String> parts = new ArrayList<>();
        for (String key : List.of("goal", "summary", "summaryText", "title", "objective", "checkpoint", "successCriteria")) {
            String value = readString(source.get(key));
            if (!value.isBlank()) {
                parts.add(value);
            }
        }
        parts.addAll(safeStringList(source.get("targetKnowledgePoints")));
        return String.join(" ", parts);
    }

    private static String inferPreferredCategory(String text) {
        String normalized = text.toLowerCase();
        if (containsAny(normalized, "深度学习", "机器学习", "神经网络", "反向传播", "损失函数", "优化器", "人工智能", "deep learning", "machine learning", "neural", "backprop", "optimizer", "pytorch", "tensorflow")
            || normalized.matches(".*\\b(ai|ml)\\b.*")) {
            return "AI_ML";
        }
        if (containsAny(normalized, "前端", "浏览器", "html", "css", "javascript", "typescript", "react", "vue", "dom", "web")) {
            return "FRONTEND_WEB";
        }
        if (containsAny(normalized, "数据库", "索引", "事务", "sql", "mysql", "postgres", "redis")) {
            return "DATABASES";
        }
        if (containsAny(normalized, "操作系统", "进程", "线程", "内存", "linux", "kernel")) {
            return "OPERATING_SYSTEMS";
        }
        if (containsAny(normalized, "网络", "tcp", "udp", "http", "dns", "computer network")) {
            return "COMPUTER_NETWORKS";
        }
        if (containsAny(normalized, "数据结构", "算法", "动态规划", "排序", "图论", "algorithm", "graph", "tree")) {
            return "DATA_STRUCTURES_ALGORITHMS";
        }
        return null;
    }

    private static boolean matchesCategoryText(String text, String category) {
        String inferred = inferPreferredCategory(text);
        return inferred == null || inferred.equals(category);
    }

    private static String resourceText(Map<String, Object> resource) {
        return String.join(" ",
            readString(resource.get("title")),
            readString(resource.get("summaryText")),
            readString(resource.get("matchReason")),
            readString(resource.get("sourceName")),
            readString(resource.get("source")),
            readString(firstNonBlank(resource.get("downloadUrl"), resource.get("url")))
        ).toLowerCase();
    }

    private static List<String> stepSearchTerms(Map<String, Object> step) {
        List<String> terms = new ArrayList<>();
        terms.add(readString(step.get("title")));
        terms.add(readString(step.get("objective")));
        terms.addAll(safeStringList(step.get("targetKnowledgePoints")));
        List<String> aliases = new ArrayList<>();
        for (String term : terms) {
            String normalized = term.toLowerCase();
            if (normalized.contains("深度学习")) {
                aliases.addAll(List.of("deep learning", "pytorch", "neural"));
            }
            if (normalized.contains("神经网络")) {
                aliases.addAll(List.of("neural", "torch.nn", "network"));
            }
            if (normalized.contains("损失函数")) {
                aliases.addAll(List.of("loss"));
            }
            if (normalized.contains("优化器")) {
                aliases.addAll(List.of("optimizer", "optimization"));
            }
            if (normalized.contains("反向传播")) {
                aliases.addAll(List.of("backprop", "gradient"));
            }
        }
        terms.addAll(aliases);
        return terms.stream().filter(term -> !term.isBlank()).distinct().toList();
    }

    private static int scoreLibraryResource(Map<String, Object> row, List<String> terms) {
        String haystack = String.join(" ",
            readString(row.get("title")),
            readString(row.get("summary_text")),
            readString(row.get("source_url")),
            readString(row.get("source_name")),
            readString(row.get("tags_text"))
        ).toLowerCase();
        int score = 0;
        for (String term : terms) {
            if (!term.isBlank() && haystack.contains(term.toLowerCase())) {
                score += 2;
            }
        }
        if (haystack.contains("pytorch") || haystack.contains("tensorflow")) {
            score += 1;
        }
        return score;
    }

    private static String displayLibraryTitle(Map<String, Object> row) {
        String title = readString(row.get("title"));
        if (!title.isBlank() && !title.toLowerCase().startsWith("redirecting")) {
            return title;
        }
        String derived = deriveTitleFromUrl(readString(row.get("source_url")));
        String sourceName = readString(row.get("source_name")).toLowerCase();
        if (!derived.isBlank() && sourceName.contains("pytorch") && !derived.toLowerCase().contains("pytorch")) {
            return "PyTorch: " + derived;
        }
        return derived.isBlank() ? "Learning resource" : derived;
    }

    private static String deriveTitleFromUrl(String url) {
        if (!isHttpUrl(url)) {
            return "";
        }
        String path = java.net.URI.create(url).getPath();
        if (path == null || path.isBlank()) {
            return "";
        }
        String segment = path.substring(path.lastIndexOf('/') + 1);
        int dotIndex = segment.lastIndexOf('.');
        if (dotIndex > 0) {
            segment = segment.substring(0, dotIndex);
        }
        return segment.replace('-', ' ').replace('_', ' ').trim();
    }

    private static boolean isHttpUrl(String value) {
        String url = readString(value).toLowerCase();
        return url.startsWith("http://") || url.startsWith("https://");
    }

    private static String normalizePathResourceType(Object value) {
        String type = readString(value).toUpperCase();
        if (type.equals("READING") || type.equals("COURSE")) {
            return "DOCUMENT";
        }
        if (type.equals("CODE") || type.equals("CODE_CASE")) {
            return "PRACTICAL_CASE";
        }
        return type.isBlank() ? "DOCUMENT" : type;
    }

    private static Object firstNonBlank(Object... values) {
        for (Object value : values) {
            if (!readString(value).isBlank()) {
                return value;
            }
        }
        return "";
    }

    private static boolean containsAny(String text, String... terms) {
        for (String term : terms) {
            if (text.contains(term)) {
                return true;
            }
        }
        return false;
    }

    private PlanRecord mapPlanRecord(ResultSet rs) throws SQLException {
        return new PlanRecord(
            rs.getObject("id", UUID.class),
            rs.getObject("user_id", UUID.class),
            rs.getObject("course_id", UUID.class),
            rs.getString("status"),
            readMap(rs.getObject("plan_json")),
            rs.getObject("version") == null ? null : rs.getInt("version"),
            rs.getString("trigger_source"),
            rs.getString("summary_text"),
            toOffsetDateTime(rs.getObject("updated_at"))
        );
    }

    private OffsetDateTime toOffsetDateTime(Object value) {
        if (value instanceof OffsetDateTime offsetDateTime) {
            return offsetDateTime;
        }
        if (value instanceof Timestamp timestamp) {
            return timestamp.toInstant().atZone(ZoneId.systemDefault()).toOffsetDateTime();
        }
        return null;
    }

    private static Map<String, Object> safeMap(Object value) {
        if (value instanceof Map<?, ?> rawMap) {
            Map<String, Object> normalized = new LinkedHashMap<>();
            for (Map.Entry<?, ?> entry : rawMap.entrySet()) {
                if (entry.getKey() != null) {
                    normalized.put(String.valueOf(entry.getKey()), entry.getValue());
                }
            }
            return normalized;
        }
        return new LinkedHashMap<>();
    }

    private Map<String, Object> readMap(Object value) {
        Map<String, Object> directMap = safeMap(value);
        if (!directMap.isEmpty() || value instanceof Map<?, ?>) {
            return directMap;
        }
        if (value == null) {
            return new LinkedHashMap<>();
        }
        String rawJson = String.valueOf(value).trim();
        if (rawJson.isEmpty()) {
            return new LinkedHashMap<>();
        }
        try {
            return objectMapper.readValue(rawJson, new TypeReference<LinkedHashMap<String, Object>>() {
            });
        } catch (Exception ex) {
            return new LinkedHashMap<>();
        }
    }

    private static List<Map<String, Object>> safeListOfMaps(Object value) {
        if (!(value instanceof List<?> rawList)) {
            return List.of();
        }
        List<Map<String, Object>> normalized = new ArrayList<>();
        for (Object item : rawList) {
            Map<String, Object> map = safeMap(item);
            if (!map.isEmpty()) {
                normalized.add(map);
            }
        }
        return normalized;
    }

    private static List<String> safeStringList(Object value) {
        if (!(value instanceof List<?> rawList)) {
            return List.of();
        }
        List<String> normalized = new ArrayList<>();
        for (Object item : rawList) {
            String text = readString(item);
            if (!text.isBlank()) {
                normalized.add(text);
            }
        }
        return normalized;
    }

    private static Map<String, Object> firstNonEmptyMap(Object... values) {
        for (Object value : values) {
            Map<String, Object> map = safeMap(value);
            if (!map.isEmpty()) {
                return map;
            }
        }
        return new LinkedHashMap<>();
    }

    private static List<Map<String, Object>> firstNonEmptyListOfMaps(Object... values) {
        for (Object value : values) {
            List<Map<String, Object>> list = safeListOfMaps(value);
            if (!list.isEmpty()) {
                return list;
            }
        }
        return List.of();
    }

    private static Map<String, Object> normalizeLearningPath(Map<String, Object> learningPath) {
        Map<String, Object> normalized = new LinkedHashMap<>(learningPath);
        if (!safeListOfMaps(normalized.get("steps")).isEmpty()) {
            return normalized;
        }
        List<Map<String, Object>> steps = normalizeStageSteps(normalized.get("stages"));
        if (!steps.isEmpty()) {
            normalized.put("steps", steps);
        }
        return normalized;
    }

    private static List<Map<String, Object>> normalizeStageSteps(Object stagesValue) {
        List<Map<String, Object>> steps = new ArrayList<>();
        List<Map<String, Object>> stages = safeListOfMaps(stagesValue);
        for (int stageIndex = 0; stageIndex < stages.size(); stageIndex += 1) {
            Map<String, Object> stage = stages.get(stageIndex);
            List<Map<String, Object>> rawSteps = safeListOfMaps(stage.get("steps"));
            if (rawSteps.isEmpty()) {
                rawSteps = List.of(stage);
            }
            for (Map<String, Object> rawStep : rawSteps) {
                Map<String, Object> step = new LinkedHashMap<>(rawStep);
                int order = steps.size() + 1;
                step.putIfAbsent("stepId", "step-" + order);
                step.putIfAbsent("order", order);
                step.putIfAbsent("title", firstNonBlank(rawStep.get("title"), stage.get("title")));
                step.putIfAbsent("objective", firstNonBlank(rawStep.get("objective"), rawStep.get("description"), stage.get("description")));
                step.putIfAbsent("stageTitle", firstNonBlank(stage.get("title"), "阶段 " + (stageIndex + 1)));
                steps.add(step);
            }
        }
        return steps;
    }

    private static Map<String, Object> resolveActiveStep(Map<String, Object> learningPath) {
        List<Map<String, Object>> steps = safeListOfMaps(learningPath.get("steps"));
        List<Map<String, Object>> normalizedSteps = new ArrayList<>();
        boolean hasExplicitStatus = false;
        for (int index = 0; index < steps.size(); index += 1) {
            Map<String, Object> step = new LinkedHashMap<>(steps.get(index));
            step.putIfAbsent("stepId", "step-" + (index + 1));
            normalizedSteps.add(step);
            if (!readString(step.get("status")).isBlank()) {
                hasExplicitStatus = true;
            }
        }
        Optional<Map<String, Object>> explicitActive = normalizedSteps.stream()
            .filter(LearningPathQueryService::isActiveStep)
            .findFirst()
            .map(LinkedHashMap::new);
        if (explicitActive.isPresent()) {
            return explicitActive.get();
        }
        if (!hasExplicitStatus) {
            return normalizedSteps.isEmpty() ? null : new LinkedHashMap<>(normalizedSteps.getFirst());
        }
        return normalizedSteps.stream()
            .filter(step -> !isCompletedStep(step))
            .filter(step -> !isInactiveOrPendingStep(step))
            .findFirst()
            .map(LinkedHashMap::new)
            .orElse(null);
    }

    private static boolean isActiveStep(Map<String, Object> step) {
        String normalized = readString(step.get("status")).toUpperCase()
            .replaceAll("[^A-Z0-9]+", "_")
            .replaceAll("^_+|_+$", "");
        if (normalized.isBlank()
            || normalized.startsWith("NOT_")
            || normalized.contains("INACTIVE")
            || normalized.equals("PENDING")
            || normalized.equals("COMPLETED")
            || normalized.equals("DONE")) {
            return false;
        }
        if (normalized.equals("IN_PROGRESS")) {
            return true;
        }
        return List.of(normalized.split("_+")).stream()
            .anyMatch(token -> token.equals("RUNNING")
                || token.equals("RUN")
                || token.equals("PROGRESS")
                || token.equals("ACTIVE"));
    }

    private static boolean isCompletedStep(Map<String, Object> step) {
        String normalized = normalizeStatus(step.get("status"));
        return normalized.equals("COMPLETED") || normalized.equals("DONE") || normalized.contains("MASTER");
    }

    private static boolean isInactiveOrPendingStep(Map<String, Object> step) {
        String normalized = normalizeStatus(step.get("status"));
        return normalized.startsWith("NOT_")
            || normalized.contains("INACTIVE")
            || normalized.equals("PENDING");
    }

    private static String normalizeStatus(Object value) {
        return readString(value).toUpperCase()
            .replaceAll("[^A-Z0-9]+", "_")
            .replaceAll("^_+|_+$", "");
    }

    private static String readString(Object value) {
        return value instanceof String text ? text.trim() : "";
    }

    private record PlanRecord(
        UUID planId,
        UUID userId,
        UUID courseId,
        String status,
        Map<String, Object> planJson,
        Integer version,
        String triggerSource,
        String summary,
        OffsetDateTime updatedAt
    ) {
    }
}
