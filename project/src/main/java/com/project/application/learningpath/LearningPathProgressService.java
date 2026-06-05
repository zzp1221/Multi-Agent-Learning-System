package com.project.application.learningpath;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.domain.task.ServiceType;
import com.project.domain.task.SmartEngineTask;
import com.project.domain.task.SmartEngineTaskRepository;
import com.project.domain.task.TaskStatus;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

/**
 * 根据阶段测试判题结果推进持久化学习路径；普通练习不会触发阶段流转。
 */
@Service
public class LearningPathProgressService {

    private static final Logger LOGGER = LoggerFactory.getLogger(LearningPathProgressService.class);
    private static final double PASS_THRESHOLD = 0.8;
    private static final String STAGE_TEST_PURPOSE = "STAGE_TEST";
    private static final String SNAPSHOT_TRIGGER_SOURCE = "PRACTICE_RESULT";

    private static final String CURRENT_PLAN_SQL = """
        SELECT id, user_id, course_id, plan_json
        FROM app.learning_plan
        WHERE user_id = :userId
          AND status = 'ACTIVE'
        ORDER BY updated_at DESC
        LIMIT 1
        FOR UPDATE
        """;

    private static final String UPDATE_PLAN_SQL = """
        UPDATE app.learning_plan
        SET plan_json = CAST(:planJson AS jsonb),
            updated_at = now()
        WHERE id = :planId
        """;

    private static final String NEXT_VERSION_SQL = """
        SELECT COALESCE(MAX(version), 0) + 1
        FROM app.learning_plan_snapshot
        WHERE plan_id = :planId
        """;

    private static final String INSERT_SNAPSHOT_SQL = """
        INSERT INTO app.learning_plan_snapshot(
            plan_id, user_id, course_id, version, trigger_source, plan_json, summary_text
        )
        VALUES (
            :planId, :userId, :courseId, :version, :triggerSource, CAST(:planJson AS jsonb), :summaryText
        )
        """;

    private final NamedParameterJdbcTemplate jdbcTemplate;
    private final SmartEngineTaskRepository taskRepository;
    private final ObjectMapper objectMapper;

    public LearningPathProgressService(
        NamedParameterJdbcTemplate jdbcTemplate,
        SmartEngineTaskRepository taskRepository,
        ObjectMapper objectMapper
    ) {
        this.jdbcTemplate = jdbcTemplate;
        this.taskRepository = taskRepository;
        this.objectMapper = objectMapper;
    }

    @Transactional
    public boolean handleStageTestResult(UUID userId, UUID taskId) {
        Optional<SmartEngineTask> maybeTask = taskRepository.findById(taskId);
        if (maybeTask.isEmpty()) {
            return false;
        }

        SmartEngineTask task = maybeTask.get();
        Map<String, Object> params = taskParams(task);
        if (!isStageTest(params)) {
            return false;
        }
        if (!userId.equals(task.getUserId())
            || task.getServiceType() != ServiceType.PRACTICE_JUDGE
            || task.getTaskStatus() != TaskStatus.COMPLETED) {
            return true;
        }

        String requestedStepId = readActiveStepId(params);
        Double score = readAccuracy(task.getResponseSummary());
        if (requestedStepId.isBlank() || score == null) {
            LOGGER.warn("Skip stage test progress taskId={} stepId='{}' score={}", taskId, requestedStepId, score);
            return true;
        }

        Optional<PlanRecord> maybePlan = loadCurrentPlan(userId);
        if (maybePlan.isEmpty()) {
            LOGGER.warn("Skip stage test progress because active learning plan is missing userId={} taskId={}", userId, taskId);
            return true;
        }

        PlanRecord plan = maybePlan.get();
        Map<String, Object> planJson = new LinkedHashMap<>(plan.planJson());
        List<Map<String, Object>> steps = normalizeSteps(planJson);
        Optional<String> currentActiveStepId = resolveActiveStepId(steps);
        if (currentActiveStepId.isEmpty() || !requestedStepId.equals(currentActiveStepId.get())) {
            LOGGER.warn(
                "Skip stage test progress because active step mismatched taskId={} requestedStepId={} currentActiveStepId={}",
                taskId,
                requestedStepId,
                currentActiveStepId.orElse("")
            );
            return true;
        }

        int currentIndex = findStepIndex(steps, requestedStepId);
        if (currentIndex < 0) {
            return true;
        }

        boolean passed = score >= PASS_THRESHOLD;
        String testedAt = OffsetDateTime.now(ZoneOffset.UTC).toString();
        applyStageTestResult(steps, currentIndex, score, testedAt, passed);
        planJson.put("steps", steps);
        planJson.put("stageProgressUpdatedAt", testedAt);
        planJson.put("lastStageTest", Map.of(
            "stepId", requestedStepId,
            "score", round(score),
            "passed", passed,
            "testedAt", testedAt
        ));

        String summary = buildSummary(steps.get(currentIndex), score, passed);
        persist(plan, planJson, summary);
        return true;
    }

    private Optional<PlanRecord> loadCurrentPlan(UUID userId) {
        List<PlanRecord> records = jdbcTemplate.query(
            CURRENT_PLAN_SQL,
            new MapSqlParameterSource("userId", userId),
            (RowMapper<PlanRecord>) this::mapPlanRecord
        );
        return records.stream().findFirst();
    }

    private PlanRecord mapPlanRecord(ResultSet rs, int rowNum) throws SQLException {
        return new PlanRecord(
            rs.getObject("id", UUID.class),
            rs.getObject("user_id", UUID.class),
            rs.getObject("course_id", UUID.class),
            readMap(rs.getObject("plan_json"))
        );
    }

    private void persist(PlanRecord plan, Map<String, Object> planJson, String summary) {
        String planJsonText = writeJson(planJson);
        MapSqlParameterSource updateParams = new MapSqlParameterSource()
            .addValue("planId", plan.planId())
            .addValue("planJson", planJsonText);
        jdbcTemplate.update(UPDATE_PLAN_SQL, updateParams);

        Integer nextVersion = jdbcTemplate.queryForObject(
            NEXT_VERSION_SQL,
            new MapSqlParameterSource("planId", plan.planId()),
            Integer.class
        );
        MapSqlParameterSource snapshotParams = new MapSqlParameterSource()
            .addValue("planId", plan.planId())
            .addValue("userId", plan.userId())
            .addValue("courseId", plan.courseId())
            .addValue("version", nextVersion == null ? 1 : nextVersion)
            .addValue("triggerSource", SNAPSHOT_TRIGGER_SOURCE)
            .addValue("planJson", planJsonText)
            .addValue("summaryText", summary);
        jdbcTemplate.update(INSERT_SNAPSHOT_SQL, snapshotParams);
    }

    private void applyStageTestResult(
        List<Map<String, Object>> steps,
        int currentIndex,
        double score,
        String testedAt,
        boolean passed
    ) {
        Map<String, Object> currentStep = steps.get(currentIndex);
        currentStep.put("lastTestScore", round(score));
        currentStep.put("lastTestAt", testedAt);
        currentStep.put("testAttempts", readInt(currentStep.get("testAttempts")) + 1);
        if (!passed) {
            currentStep.put("status", "IN_PROGRESS");
            currentStep.putIfAbsent("progress", 0);
            return;
        }

        currentStep.put("status", "COMPLETED");
        currentStep.put("progress", 100);
        currentStep.put("completedAt", testedAt);
        if (currentIndex + 1 < steps.size()) {
            Map<String, Object> nextStep = steps.get(currentIndex + 1);
            nextStep.put("status", "IN_PROGRESS");
            nextStep.put("progress", 0);
        }
        for (int index = currentIndex + 2; index < steps.size(); index += 1) {
            Map<String, Object> step = steps.get(index);
            if (!isCompletedStep(step)) {
                step.put("status", "NOT_STARTED");
                step.put("progress", 0);
            }
        }
    }

    private List<Map<String, Object>> normalizeSteps(Map<String, Object> planJson) {
        Object rawSteps = planJson.get("steps");
        if (!(rawSteps instanceof List<?> rawList)) {
            return List.of();
        }
        List<Map<String, Object>> steps = new ArrayList<>();
        boolean hasStatus = false;
        for (int index = 0; index < rawList.size(); index += 1) {
            Map<String, Object> step = safeMap(rawList.get(index));
            if (step.isEmpty()) {
                continue;
            }
            step.putIfAbsent("stepId", "step-" + (index + 1));
            step.putIfAbsent("order", index + 1);
            if (!readString(step.get("status")).isBlank()) {
                hasStatus = true;
            }
            steps.add(step);
        }
        if (!hasStatus && !steps.isEmpty()) {
            for (int index = 0; index < steps.size(); index += 1) {
                steps.get(index).put("status", index == 0 ? "IN_PROGRESS" : "NOT_STARTED");
                steps.get(index).putIfAbsent("progress", 0);
            }
        }
        return steps;
    }

    private Optional<String> resolveActiveStepId(List<Map<String, Object>> steps) {
        Optional<String> explicitActive = steps.stream()
            .filter(this::isActiveStep)
            .map(step -> readString(step.get("stepId")))
            .filter(value -> !value.isBlank())
            .findFirst();
        if (explicitActive.isPresent()) {
            return explicitActive;
        }

        boolean hasStatus = steps.stream().anyMatch(step -> !readString(step.get("status")).isBlank());
        if (!hasStatus) {
            return steps.isEmpty() ? Optional.empty() : Optional.of(readString(steps.getFirst().get("stepId")));
        }

        return steps.stream()
            .filter(step -> !isCompletedStep(step))
            .filter(step -> !isInactiveOrPendingStep(step))
            .map(step -> readString(step.get("stepId")))
            .filter(value -> !value.isBlank())
            .findFirst();
    }

    private int findStepIndex(List<Map<String, Object>> steps, String stepId) {
        for (int index = 0; index < steps.size(); index += 1) {
            if (stepId.equals(readString(steps.get(index).get("stepId")))) {
                return index;
            }
        }
        return -1;
    }

    private boolean isStageTest(Map<String, Object> params) {
        return STAGE_TEST_PURPOSE.equalsIgnoreCase(readString(params.get("purpose")));
    }

    private String readActiveStepId(Map<String, Object> params) {
        Map<String, Object> learningContext = safeMap(params.get("learningContext"));
        String stepId = readString(learningContext.get("activeLearningStepId"));
        return stepId.isBlank() ? readString(params.get("activeLearningStepId")) : stepId;
    }

    private Double readAccuracy(Map<String, Object> responseSummary) {
        Map<String, Object> summary = safeMap(responseSummary);
        Map<String, Object> judgeResult = safeMap(summary.get("judgeResult"));
        Double nestedAccuracy = readDoubleOrNull(judgeResult.get("accuracy"));
        Double accuracy = nestedAccuracy == null ? readDoubleOrNull(summary.get("accuracy")) : nestedAccuracy;
        if (accuracy != null) {
            return accuracy;
        }
        Double nestedScore = readDoubleOrNull(judgeResult.get("totalScore"));
        Double score = nestedScore == null ? readDoubleOrNull(summary.get("totalScore")) : nestedScore;
        if (score == null) {
            return null;
        }
        return score > 1.0 ? score / 100.0 : score;
    }

    private Map<String, Object> taskParams(SmartEngineTask task) {
        return safeMap(safeMap(task.getRequestPayload()).get("params"));
    }

    private boolean isActiveStep(Map<String, Object> step) {
        String status = normalizeStatus(step.get("status"));
        if (status.isBlank()
            || status.startsWith("NOT_")
            || status.contains("INACTIVE")
            || status.equals("PENDING")
            || status.equals("COMPLETED")
            || status.equals("DONE")) {
            return false;
        }
        return status.equals("IN_PROGRESS")
            || List.of(status.split("_+")).stream()
                .anyMatch(token -> token.equals("RUNNING")
                    || token.equals("RUN")
                    || token.equals("PROGRESS")
                    || token.equals("ACTIVE"));
    }

    private boolean isCompletedStep(Map<String, Object> step) {
        String status = normalizeStatus(step.get("status"));
        return status.equals("COMPLETED") || status.equals("DONE") || status.contains("MASTER");
    }

    private boolean isInactiveOrPendingStep(Map<String, Object> step) {
        String status = normalizeStatus(step.get("status"));
        return status.startsWith("NOT_")
            || status.contains("INACTIVE")
            || status.equals("PENDING");
    }

    private String normalizeStatus(Object value) {
        return readString(value).toUpperCase()
            .replaceAll("[^A-Z0-9]+", "_")
            .replaceAll("^_+|_+$", "");
    }

    private String buildSummary(Map<String, Object> step, double score, boolean passed) {
        String title = readString(step.get("title"));
        String subject = title.isBlank() ? readString(step.get("stepId")) : title;
        String result = passed ? "通过" : "未通过";
        return "阶段测试" + result + "：" + subject + "，正确率 " + Math.round(score * 100) + "%";
    }

    private Map<String, Object> readMap(Object value) {
        Map<String, Object> direct = safeMap(value);
        if (!direct.isEmpty() || value instanceof Map<?, ?>) {
            return direct;
        }
        String rawJson = value == null ? "" : String.valueOf(value).trim();
        if (rawJson.isBlank()) {
            return new LinkedHashMap<>();
        }
        try {
            return objectMapper.readValue(rawJson, new TypeReference<LinkedHashMap<String, Object>>() {
            });
        } catch (JsonProcessingException ex) {
            return new LinkedHashMap<>();
        }
    }

    private String writeJson(Map<String, Object> value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException ex) {
            throw new IllegalStateException("Failed to serialize learning plan JSON", ex);
        }
    }

    private static Map<String, Object> safeMap(Object value) {
        if (!(value instanceof Map<?, ?> rawMap)) {
            return new LinkedHashMap<>();
        }
        Map<String, Object> normalized = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : rawMap.entrySet()) {
            if (entry.getKey() != null) {
                normalized.put(String.valueOf(entry.getKey()), entry.getValue());
            }
        }
        return normalized;
    }

    private static String readString(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static int readInt(Object value) {
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

    private static Double readDoubleOrNull(Object value) {
        if (value instanceof Number number) {
            return number.doubleValue();
        }
        try {
            String raw = readString(value);
            return raw.isBlank() ? null : Double.parseDouble(raw);
        } catch (NumberFormatException ex) {
            return null;
        }
    }

    private static double round(double value) {
        return Math.round(value * 10_000.0) / 10_000.0;
    }

    private record PlanRecord(
        UUID planId,
        UUID userId,
        UUID courseId,
        Map<String, Object> planJson
    ) {
    }
}
