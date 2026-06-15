package com.project.application.learningpath;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.learningpath.dto.LearningPathCurrentResponse;
import com.project.api.smartengine.dto.TaskStatusResponse;
import com.project.application.resource.ResourceSemanticWarmupService;
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
    private static final String EMPTY_PATH_SUMMARY = "还没有生成学习路径，请先完成画像配置或发起一次个性化学习路径生成。";

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
    private final ResourceSemanticWarmupService resourceSemanticWarmupService;

    public LearningPathQueryService(
        NamedParameterJdbcTemplate jdbcTemplate,
        SmartEngineTaskRepository taskRepository,
        TaskStateMachineService taskStateMachineService,
        ObjectMapper objectMapper,
        ResourceSemanticWarmupService resourceSemanticWarmupService
    ) {
        this.jdbcTemplate = jdbcTemplate;
        this.taskRepository = taskRepository;
        this.taskStateMachineService = taskStateMachineService;
        this.objectMapper = objectMapper;
        this.resourceSemanticWarmupService = resourceSemanticWarmupService;
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

        LearningPathCurrentResponse response = currentPlan
            .map(plan -> {
                Map<String, Object> learningPath = normalizeLearningPath(plan.planJson().isEmpty()
                    ? fallbackLearningPath
                    : plan.planJson());
                Map<String, Object> currentResourcePushPlan = firstNonEmptyMap(resourcePushPlan, learningPath.get("resourcePushPlan"));
                Map<String, Object> alignedResourcePushPlan = alignResourcePushPlan(learningPath, currentResourcePushPlan, pushedResources);
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
                Map<String, Object> alignedResourcePushPlan = alignResourcePushPlan(learningPath, currentResourcePushPlan, pushedResources);
                boolean emptyPath = learningPath.isEmpty();
                return new LearningPathCurrentResponse(
                    null,
                    userId,
                    null,
                    emptyPath ? "EMPTY" : "ACTIVE",
                    learningPath,
                    resolveActiveStep(learningPath),
                    alignedResourcePushPlan,
                    flattenResourcePushPlan(alignedResourcePushPlan),
                    null,
                    emptyPath ? null : "TASK_RESPONSE_FALLBACK",
                    emptyPath ? EMPTY_PATH_SUMMARY : readString(latestCompletedSummary.get("summary")),
                    null,
                    refreshTaskResponse,
                    resourceRefreshTaskResponse
                );
            });
        triggerResourceWarmupIfPathReady(response);
        return response;
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

    private void triggerResourceWarmupIfPathReady(LearningPathCurrentResponse response) {
        if (response == null || response.learningPath() == null || response.learningPath().isEmpty()) {
            return;
        }
        resourceSemanticWarmupService.submitCurrentStageWarmup(response.userId());
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
        Map<String, Object> learningPath,
        Map<String, Object> existingPlan,
        List<Map<String, Object>> fallbackResources
    ) {
        List<Map<String, Object>> steps = safeListOfMaps(learningPath.get("steps"));
        if (steps.isEmpty()) {
            return sanitizeResourcePushPlan(existingPlan);
        }
        Map<String, Object> alignedPlan = new LinkedHashMap<>(safeMap(existingPlan));
        Map<String, List<Map<String, Object>>> existingByStep = existingStepResourcesByStep(existingPlan, fallbackResources, steps);
        List<Map<String, Object>> stepResources = new ArrayList<>();
        List<Map<String, Object>> coverageGaps = new ArrayList<>();

        for (int index = 0; index < steps.size(); index += 1) {
            Map<String, Object> step = steps.get(index);
            String stepId = readString(step.get("stepId"));
            if (stepId.isBlank()) {
                stepId = "step-" + (index + 1);
            }
            List<Map<String, Object>> resources = new ArrayList<>();
            for (Map<String, Object> resource : existingByStep.getOrDefault(stepId, List.of())) {
                Map<String, Object> sanitized = sanitizePathResource(resource);
                if (!sanitized.isEmpty()) {
                    resources.add(sanitized);
                }
            }
            if (resources.isEmpty()) {
                coverageGaps.add(Map.of(
                    "stepId", stepId,
                    "missingResourceTypes", List.of("DOCUMENT", "VIDEO", "PRACTICAL_CASE"),
                    "reason", "Tavily 暂未检索到足够匹配当前学习步骤的外部资源。"
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
        alignedPlan.put("source", "tavily");
        return alignedPlan;
    }

    private Map<String, Object> sanitizeResourcePushPlan(Map<String, Object> existingPlan) {
        Map<String, Object> sanitizedPlan = new LinkedHashMap<>(safeMap(existingPlan));
        List<Map<String, Object>> sanitizedSteps = new ArrayList<>();
        for (Map<String, Object> rawStep : safeListOfMaps(existingPlan.get("stepResources"))) {
            List<Map<String, Object>> resources = new ArrayList<>();
            for (Map<String, Object> resource : safeListOfMaps(rawStep.get("resources"))) {
                Map<String, Object> sanitized = sanitizePathResource(resource);
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

    private Map<String, List<Map<String, Object>>> existingStepResourcesByStep(
        Map<String, Object> existingPlan,
        List<Map<String, Object>> fallbackResources,
        List<Map<String, Object>> steps
    ) {
        Map<String, List<Map<String, Object>>> resourcesByStep = new LinkedHashMap<>();
        for (Map<String, Object> rawStep : safeListOfMaps(existingPlan.get("stepResources"))) {
            String stepId = readString(rawStep.get("stepId"));
            List<Map<String, Object>> resources = resourcesByStep.computeIfAbsent(stepId, ignored -> new ArrayList<>());
            for (Map<String, Object> resource : safeListOfMaps(rawStep.get("resources"))) {
                addResourceIfAbsent(resources, resource);
            }
        }
        List<String> validStepIds = normalizedStepIds(steps);
        String defaultStepId = activeOrFirstStepId(steps);
        for (Map<String, Object> resource : fallbackResources == null ? List.<Map<String, Object>>of() : fallbackResources) {
            String stepId = readString(resource.get("stepId"));
            if (stepId.isBlank() || !validStepIds.contains(stepId)) {
                stepId = defaultStepId;
            }
            if (stepId.isBlank()) {
                continue;
            }
            addResourceIfAbsent(resourcesByStep.computeIfAbsent(stepId, ignored -> new ArrayList<>()), resource);
        }
        return resourcesByStep;
    }

    private static void addResourceIfAbsent(List<Map<String, Object>> resources, Map<String, Object> resource) {
        String key = resourceIdentity(resource);
        boolean exists = resources.stream().anyMatch(existing -> resourceIdentity(existing).equals(key));
        if (!exists) {
            resources.add(resource);
        }
    }

    private static String resourceIdentity(Map<String, Object> resource) {
        String url = readString(firstNonBlank(resource.get("downloadUrl"), resource.get("url")));
        if (!url.isBlank()) {
            return "url:" + url.toLowerCase();
        }
        String title = readString(resource.get("title"));
        return title.isBlank() ? "" : "title:" + title.toLowerCase();
    }

    private static List<String> normalizedStepIds(List<Map<String, Object>> steps) {
        List<String> stepIds = new ArrayList<>();
        for (int index = 0; index < steps.size(); index += 1) {
            stepIds.add(normalizedStepId(steps.get(index), index));
        }
        return stepIds;
    }

    private static String activeOrFirstStepId(List<Map<String, Object>> steps) {
        for (int index = 0; index < steps.size(); index += 1) {
            if (isActiveStep(steps.get(index))) {
                return normalizedStepId(steps.get(index), index);
            }
        }
        return steps.isEmpty() ? "" : normalizedStepId(steps.getFirst(), 0);
    }

    private static String normalizedStepId(Map<String, Object> step, int index) {
        String stepId = readString(step.get("stepId"));
        return stepId.isBlank() ? "step-" + (index + 1) : stepId;
    }

    private Map<String, Object> sanitizePathResource(Map<String, Object> resource) {
        String url = readString(firstNonBlank(resource.get("downloadUrl"), resource.get("url")));
        if (!isHttpUrl(url)) {
            return new LinkedHashMap<>();
        }
        String type = normalizePathResourceType(firstNonBlank(resource.get("resourceType"), resource.get("type")));
        if (type.equals("QUIZ") || type.equals("PRACTICE")) {
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
