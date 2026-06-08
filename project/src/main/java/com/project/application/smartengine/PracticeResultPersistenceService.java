package com.project.application.smartengine;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.domain.task.SmartEngineTask;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import java.util.stream.Collectors;

@Service
public class PracticeResultPersistenceService {

    private static final String DEFAULT_DIFFICULTY = "MIXED";
    private static final String DEFAULT_JUDGE_MODE = "HYBRID";

    private final NamedParameterJdbcTemplate jdbcTemplate;
    private final ObjectMapper objectMapper;

    public PracticeResultPersistenceService(
        NamedParameterJdbcTemplate jdbcTemplate,
        ObjectMapper objectMapper
    ) {
        this.jdbcTemplate = jdbcTemplate;
        this.objectMapper = objectMapper;
    }

    @Transactional
    public int persistCompletedPracticeJudgeResult(SmartEngineTask task) {
        if (task == null) {
            return 0;
        }

        Map<String, Object> params = taskParams(task);
        List<Map<String, Object>> resultItems = resultItems(task.getResponseSummary());
        if (resultItems.isEmpty()) {
            return 0;
        }

        Map<String, Map<String, Object>> questionsById = questionsById(params, task.getResponseSummary());
        UUID practiceSetId = ensurePracticeSet(task, params, resultItems.size());
        int itemNo = 1;
        int persisted = 0;
        for (Map<String, Object> resultItem : resultItems) {
            Map<String, Object> question = questionForResult(resultItem, questionsById);
            PracticeItemDraft draft = toPracticeItemDraft(resultItem, question, params, itemNo);
            if (draft == null) {
                itemNo++;
                continue;
            }
            UUID practiceItemId = upsertPracticeItem(practiceSetId, draft);
            upsertPracticeSubmission(task, practiceSetId, practiceItemId, resultItem, params);
            persisted++;
            itemNo++;
        }

        updatePracticeSetStatus(practiceSetId, persisted);
        return persisted;
    }

    private UUID ensurePracticeSet(SmartEngineTask task, Map<String, Object> params, int questionCount) {
        MapSqlParameterSource lookupParams = new MapSqlParameterSource()
            .addValue("taskId", task.getId())
            .addValue("userId", task.getUserId());
        List<UUID> existingIds = jdbcTemplate.query(
            """
            SELECT id
            FROM app.practice_set
            WHERE task_id = :taskId AND user_id = :userId
            ORDER BY created_at DESC
            LIMIT 1
            """,
            lookupParams,
            uuidRowMapper("id")
        );
        if (!existingIds.isEmpty()) {
            return existingIds.get(0);
        }

        Map<String, Object> requestPayload = task.getRequestPayload() == null ? Map.of() : task.getRequestPayload();
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("source", "smart_engine_practice_judge");
        metadata.put("topic", stringValue(params.get("topic")));
        metadata.put("query", stringValue(params.get("query")));
        metadata.put("conversationId", stringValue(requestPayload.get("conversationId")));

        return jdbcTemplate.queryForObject(
            """
            INSERT INTO app.practice_set(
                task_id, user_id, difficulty_level, question_count, set_status, metadata_json
            )
            VALUES (
                :taskId, :userId, CAST(:difficulty AS app.difficulty_level), :questionCount, 'JUDGED',
                CAST(:metadataJson AS jsonb)
            )
            RETURNING id
            """,
            new MapSqlParameterSource()
                .addValue("taskId", task.getId())
                .addValue("userId", task.getUserId())
                .addValue("difficulty", normalizeDifficulty(params.get("difficulty")))
                .addValue("questionCount", questionCount)
                .addValue("metadataJson", writeJson(metadata)),
            UUID.class
        );
    }

    private void updatePracticeSetStatus(UUID practiceSetId, int questionCount) {
        jdbcTemplate.update(
            """
            UPDATE app.practice_set
            SET question_count = :questionCount,
                set_status = 'JUDGED',
                updated_at = now()
            WHERE id = :practiceSetId
            """,
            new MapSqlParameterSource()
                .addValue("practiceSetId", practiceSetId)
                .addValue("questionCount", questionCount)
        );
    }

    private UUID upsertPracticeItem(UUID practiceSetId, PracticeItemDraft draft) {
        return jdbcTemplate.queryForObject(
            """
            INSERT INTO app.practice_item(
                practice_set_id, item_no, question_type, stem, options_json, standard_answer,
                rubric_json, knowledge_tags, difficulty_level
            )
            VALUES (
                :practiceSetId, :itemNo, :questionType, :stem, CAST(:optionsJson AS jsonb),
                CAST(:standardAnswerJson AS jsonb), CAST(:rubricJson AS jsonb),
                CAST(:knowledgeTagsJson AS jsonb), CAST(:difficulty AS app.difficulty_level)
            )
            ON CONFLICT (practice_set_id, item_no) DO UPDATE
            SET question_type = EXCLUDED.question_type,
                stem = EXCLUDED.stem,
                options_json = EXCLUDED.options_json,
                standard_answer = EXCLUDED.standard_answer,
                rubric_json = EXCLUDED.rubric_json,
                knowledge_tags = EXCLUDED.knowledge_tags,
                difficulty_level = EXCLUDED.difficulty_level
            RETURNING id
            """,
            new MapSqlParameterSource()
                .addValue("practiceSetId", practiceSetId)
                .addValue("itemNo", draft.itemNo())
                .addValue("questionType", draft.questionType())
                .addValue("stem", draft.stem())
                .addValue("optionsJson", writeJson(draft.options()))
                .addValue("standardAnswerJson", writeJson(Map.of("answer", draft.standardAnswer())))
                .addValue("rubricJson", writeJson(draft.rubric()))
                .addValue("knowledgeTagsJson", writeJson(draft.knowledgeTags()))
                .addValue("difficulty", draft.difficulty()),
            UUID.class
        );
    }

    private void upsertPracticeSubmission(
        SmartEngineTask task,
        UUID practiceSetId,
        UUID practiceItemId,
        Map<String, Object> resultItem,
        Map<String, Object> params
    ) {
        Map<String, Object> answerJson = Map.of("answer", learnerAnswer(resultItem, params));
        Object profileDelta = resultItem.get("profileDelta");
        Map<String, Object> profileDeltaJson = profileDelta instanceof Map<?, ?> map ? copyMap(map) : Map.of();

        jdbcTemplate.update(
            """
            INSERT INTO app.practice_submission(
                practice_set_id, practice_item_id, user_id, answer_json, score, is_correct,
                judge_mode, judge_result_json, profile_delta_json, submitted_at
            )
            VALUES (
                :practiceSetId, :practiceItemId, :userId, CAST(:answerJson AS jsonb), :score,
                :isCorrect, :judgeMode, CAST(:judgeResultJson AS jsonb),
                CAST(:profileDeltaJson AS jsonb), now()
            )
            ON CONFLICT (practice_item_id, user_id) DO UPDATE
            SET answer_json = EXCLUDED.answer_json,
                score = EXCLUDED.score,
                is_correct = EXCLUDED.is_correct,
                judge_mode = EXCLUDED.judge_mode,
                judge_result_json = EXCLUDED.judge_result_json,
                profile_delta_json = EXCLUDED.profile_delta_json,
                submitted_at = now()
            """,
            new MapSqlParameterSource()
                .addValue("practiceSetId", practiceSetId)
                .addValue("practiceItemId", practiceItemId)
                .addValue("userId", task.getUserId())
                .addValue("answerJson", writeJson(answerJson))
                .addValue("score", scoreValue(resultItem.get("score")))
                .addValue("isCorrect", booleanValue(resultItem.get("isCorrect")))
                .addValue("judgeMode", DEFAULT_JUDGE_MODE)
                .addValue("judgeResultJson", writeJson(resultItem))
                .addValue("profileDeltaJson", writeJson(profileDeltaJson))
        );
    }

    private PracticeItemDraft toPracticeItemDraft(
        Map<String, Object> resultItem,
        Map<String, Object> question,
        Map<String, Object> params,
        int itemNo
    ) {
        String stem = firstText(question.get("stem"), question.get("question"), resultItem.get("stem"));
        if (stem.isBlank()) {
            return null;
        }
        String questionType = normalizeQuestionType(firstText(resultItem.get("questionType"), question.get("questionType"), question.get("type")));
        Object answer = firstPresent(question.get("standardAnswer"), question.get("answer"), resultItem.get("correctAnswer"));
        String standardAnswer = stringValue(answer);
        if (standardAnswer.isBlank()) {
            standardAnswer = stringValue(resultItem.get("correctAnswer"));
        }

        return new PracticeItemDraft(
            itemNo,
            questionType,
            stem,
            listValue(firstPresent(question.get("options"), question.get("choices"), question.get("optionList"))),
            standardAnswer,
            mapValue(firstPresent(question.get("rubric"), question.get("rubricJson"))),
            knowledgeTags(resultItem, question, params),
            normalizeDifficulty(firstPresent(question.get("difficultyLevel"), question.get("difficulty"), params.get("difficulty")))
        );
    }

    private Map<String, Object> questionForResult(
        Map<String, Object> resultItem,
        Map<String, Map<String, Object>> questionsById
    ) {
        String questionId = stringValue(resultItem.get("questionId"));
        if (!questionId.isBlank()) {
            Map<String, Object> question = questionsById.get(questionId);
            if (question != null) {
                return question;
            }
        }
        return Map.of();
    }

    private Map<String, Map<String, Object>> questionsById(
        Map<String, Object> params,
        Map<String, Object> responseSummary
    ) {
        Map<String, Object> safeSummary = responseSummary == null ? Map.of() : responseSummary;
        List<Map<String, Object>> questions = new ArrayList<>();
        questions.addAll(mapList(params.get("practiceQuestions")));
        Map<String, Object> batch = mapValue(params.get("practiceQuestionBatch"));
        questions.addAll(mapList(batch.get("questions")));
        questions.addAll(mapList(safeSummary.get("questions")));

        return questions.stream()
            .filter(question -> !stringValue(question.get("questionId")).isBlank())
            .collect(Collectors.toMap(
                question -> stringValue(question.get("questionId")),
                question -> question,
                (left, right) -> left,
                LinkedHashMap::new
            ));
    }

    private List<Map<String, Object>> resultItems(Map<String, Object> responseSummary) {
        Map<String, Object> safeSummary = responseSummary == null ? Map.of() : responseSummary;
        List<Map<String, Object>> items = mapList(safeSummary.get("items"));
        if (!items.isEmpty()) {
            return items;
        }
        Map<String, Object> judgeResult = mapValue(safeSummary.get("judgeResult"));
        items = mapList(judgeResult.get("items"));
        if (!items.isEmpty()) {
            return items;
        }
        return mapList(safeSummary.get("results"));
    }

    private List<String> knowledgeTags(
        Map<String, Object> resultItem,
        Map<String, Object> question,
        Map<String, Object> params
    ) {
        List<String> tags = stringList(firstPresent(resultItem.get("knowledgeTags"), question.get("knowledgeTags"), question.get("tags")));
        if (!tags.isEmpty()) {
            return tags;
        }
        String topic = firstText(params.get("topic"), params.get("keyPoints"), params.get("query"));
        return topic.isBlank() ? List.of() : List.of(topic);
    }

    private Object learnerAnswer(Map<String, Object> resultItem, Map<String, Object> params) {
        Object learnerAnswer = resultItem.get("learnerAnswer");
        if (learnerAnswer != null) {
            return learnerAnswer;
        }
        Map<String, Object> answers = mapValue(params.get("answers"));
        String questionId = stringValue(resultItem.get("questionId"));
        return answers.getOrDefault(questionId, "");
    }

    private Map<String, Object> taskParams(SmartEngineTask task) {
        Map<String, Object> requestPayload = task.getRequestPayload() == null ? Map.of() : task.getRequestPayload();
        return mapValue(requestPayload.get("params"));
    }

    private RowMapper<UUID> uuidRowMapper(String columnName) {
        return (rs, rowNum) -> rs.getObject(columnName, UUID.class);
    }

    private String normalizeQuestionType(String rawValue) {
        String value = rawValue == null ? "" : rawValue.trim().toUpperCase(Locale.ROOT);
        if (value.contains("MULTIPLE")) {
            return "MULTIPLE_CHOICE";
        }
        if (value.contains("SINGLE") || value.equals("CHOICE") || value.contains("SELECT")) {
            return "SINGLE_CHOICE";
        }
        if (value.contains("BLANK") || value.contains("FILL")) {
            return "FILL_BLANK";
        }
        if (value.contains("CODE")) {
            return "CODING";
        }
        if (value.equals("SINGLE_CHOICE") || value.equals("MULTIPLE_CHOICE")
            || value.equals("FILL_BLANK") || value.equals("SHORT_ANSWER") || value.equals("CODING")) {
            return value;
        }
        return "SHORT_ANSWER";
    }

    private String normalizeDifficulty(Object rawValue) {
        String value = stringValue(rawValue).trim().toUpperCase(Locale.ROOT);
        if (value.contains("BASIC") || value.contains("EASY") || value.contains("FOUNDATION") || value.contains("基础")) {
            return "BASIC";
        }
        if (value.contains("INTERMEDIATE") || value.contains("MEDIUM") || value.contains("中")) {
            return "INTERMEDIATE";
        }
        if (value.contains("ADVANCED") || value.contains("HARD") || value.contains("高")) {
            return "ADVANCED";
        }
        return DEFAULT_DIFFICULTY;
    }

    private Boolean booleanValue(Object value) {
        if (value instanceof Boolean bool) {
            return bool;
        }
        if (value == null) {
            return null;
        }
        return Boolean.parseBoolean(String.valueOf(value));
    }

    private BigDecimal scoreValue(Object value) {
        if (value instanceof BigDecimal decimal) {
            return decimal;
        }
        if (value instanceof Number number) {
            return BigDecimal.valueOf(number.doubleValue());
        }
        String text = stringValue(value);
        if (text.isBlank()) {
            return null;
        }
        return new BigDecimal(text);
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException ex) {
            throw new IllegalArgumentException("Failed to serialize practice result JSON", ex);
        }
    }

    private Object firstPresent(Object... values) {
        for (Object value : values) {
            if (value != null) {
                return value;
            }
        }
        return null;
    }

    private String firstText(Object... values) {
        for (Object value : values) {
            String text = stringValue(value);
            if (!text.isBlank()) {
                return text;
            }
        }
        return "";
    }

    private String stringValue(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private List<Object> listValue(Object value) {
        if (value instanceof List<?> list) {
            return new ArrayList<>(list);
        }
        return List.of();
    }

    private List<String> stringList(Object value) {
        return listValue(value).stream()
            .map(this::stringValue)
            .filter(text -> !text.isBlank())
            .distinct()
            .toList();
    }

    private List<Map<String, Object>> mapList(Object value) {
        if (!(value instanceof List<?> list)) {
            return List.of();
        }
        return list.stream()
            .filter(Objects::nonNull)
            .filter(item -> item instanceof Map<?, ?>)
            .map(item -> copyMap((Map<?, ?>) item))
            .toList();
    }

    private Map<String, Object> mapValue(Object value) {
        if (value instanceof Map<?, ?> map) {
            return copyMap(map);
        }
        return Map.of();
    }

    private Map<String, Object> copyMap(Map<?, ?> source) {
        Map<String, Object> copy = new LinkedHashMap<>();
        source.forEach((key, value) -> copy.put(String.valueOf(key), value));
        return copy;
    }

    private record PracticeItemDraft(
        int itemNo,
        String questionType,
        String stem,
        List<Object> options,
        String standardAnswer,
        Map<String, Object> rubric,
        List<String> knowledgeTags,
        String difficulty
    ) {
    }
}
