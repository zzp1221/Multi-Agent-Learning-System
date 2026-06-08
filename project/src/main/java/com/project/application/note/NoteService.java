package com.project.application.note;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.note.dto.CreateNoteFolderRequest;
import com.project.api.note.dto.CreateNoteRequest;
import com.project.api.note.dto.NoteAnalysisResponse;
import com.project.api.note.dto.NoteDetailResponse;
import com.project.api.note.dto.NoteFolderResponse;
import com.project.api.note.dto.NoteListItemResponse;
import com.project.api.note.dto.NoteListResponse;
import com.project.api.note.dto.NoteSemanticHitResponse;
import com.project.api.note.dto.NoteSemanticResultResponse;
import com.project.api.note.dto.NoteSemanticSearchResponse;
import com.project.api.note.dto.NoteTagResponse;
import com.project.api.note.dto.NoteTodoResponse;
import com.project.api.note.dto.NoteVersionResponse;
import com.project.api.note.dto.UpdateNoteFolderRequest;
import com.project.api.note.dto.UpdateNoteRequest;
import com.project.api.note.dto.UpdateNoteTagsRequest;
import com.project.api.resource.dto.ResourceSemanticResultResponse;
import com.project.api.resource.dto.ResourceSemanticSearchResponse;
import com.project.application.common.ApplicationException;
import com.project.application.resource.ResourceLibraryService;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.core.task.TaskExecutor;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.regex.Pattern;

@Service
public class NoteService {

    private static final Logger LOGGER = LoggerFactory.getLogger(NoteService.class);
    private static final int DEFAULT_PAGE_SIZE = 20;
    private static final int MAX_PAGE_SIZE = 80;
    private static final int MAX_TAGS_PER_NOTE = 12;
    private static final TypeReference<List<Map<String, Object>>> TAG_LIST_TYPE = new TypeReference<>() {
    };
    private static final TypeReference<Map<String, Object>> STRING_OBJECT_MAP = new TypeReference<>() {
    };
    private static final Pattern MARKDOWN_SYNTAX = Pattern.compile(
        "(?m)^#{1,6}\\s+|^\\s{0,3}>\\s?|^\\s*[-+*]\\s+|```[\\s\\S]*?```|`([^`]+)`|!\\[[^]]*]\\([^)]*\\)|\\[([^]]+)]\\([^)]*\\)|(?<![\\p{L}\\p{N}_])[*_~]+|[*_~]+(?![\\p{L}\\p{N}_])"
    );

    private final NamedParameterJdbcTemplate jdbcTemplate;
    private final ObjectMapper objectMapper;
    private final NotePythonClient notePythonClient;
    private final ResourceLibraryService resourceLibraryService;
    private final TaskExecutor noteIndexTaskExecutor;

    public NoteService(
        NamedParameterJdbcTemplate jdbcTemplate,
        ObjectMapper objectMapper,
        NotePythonClient notePythonClient,
        ResourceLibraryService resourceLibraryService,
        @Qualifier("noteIndexTaskExecutor") TaskExecutor noteIndexTaskExecutor
    ) {
        this.jdbcTemplate = jdbcTemplate;
        this.objectMapper = objectMapper;
        this.notePythonClient = notePythonClient;
        this.resourceLibraryService = resourceLibraryService;
        this.noteIndexTaskExecutor = noteIndexTaskExecutor;
    }

    @Transactional(readOnly = true)
    public NoteListResponse listNotes(
        UUID userId,
        String keyword,
        UUID folderId,
        String tag,
        Integer page,
        Integer size
    ) {
        int safePage = Math.max(0, page == null ? 0 : page);
        int safeSize = Math.max(1, Math.min(MAX_PAGE_SIZE, size == null ? DEFAULT_PAGE_SIZE : size));
        MapSqlParameterSource params = baseParams(userId)
            .addValue("limit", safeSize)
            .addValue("offset", safePage * safeSize);

        List<String> conditions = noteConditions(keyword, folderId, tag, params);
        String where = " WHERE " + String.join(" AND ", conditions);
        List<NoteListItemResponse> items = jdbcTemplate.query(
            noteListSelectSql() + where + "\nORDER BY n.updated_at DESC\nLIMIT :limit OFFSET :offset",
            params,
            noteListRowMapper()
        );
        Long total = jdbcTemplate.queryForObject(
            """
            SELECT COUNT(*)
            FROM app.note n
            """ + where,
            params,
            Long.class
        );
        return new NoteListResponse(items, total == null ? 0 : total, safePage, safeSize);
    }

    @Transactional(readOnly = true)
    public NoteDetailResponse getNote(UUID userId, UUID noteId) {
        NoteDetailResponse note = findNoteDetailOrNull(userId, noteId);
        if (note == null) {
            throw new ApplicationException("NOTE_NOT_FOUND", "笔记不存在", HttpStatus.NOT_FOUND);
        }
        return note;
    }

    @Transactional
    public NoteDetailResponse createNote(UUID userId, CreateNoteRequest request) {
        NormalizedNote normalized = normalizeNoteInput(request.title(), request.markdownContent());
        UUID folderId = normalizeOwnedFolder(userId, request.folderId());
        UUID noteId = UUID.randomUUID();
        UUID ragResourceId = UUID.randomUUID();
        upsertNoteResource(userId, noteId, ragResourceId, normalized, request.tags());
        jdbcTemplate.update(
            """
            INSERT INTO app.note(
              id, user_id, folder_id, title, markdown_content, plain_text,
              content_hash, word_count, reading_minutes, rag_resource_id
            )
            VALUES (
              :noteId, :userId, :folderId, :title, :markdownContent, :plainText,
              :contentHash, :wordCount, :readingMinutes, :ragResourceId
            )
            """,
            baseParams(userId)
                .addValue("noteId", noteId)
                .addValue("folderId", folderId)
                .addValue("title", normalized.title())
                .addValue("markdownContent", normalized.markdownContent())
                .addValue("plainText", normalized.plainText())
                .addValue("contentHash", normalized.contentHash())
                .addValue("wordCount", normalized.wordCount())
                .addValue("readingMinutes", normalized.readingMinutes())
                .addValue("ragResourceId", ragResourceId)
        );
        updateNoteTags(userId, noteId, request.tags());
        createVersion(userId, noteId, normalized, "创建笔记");
        indexNoteForRagAfterCommit(userId, noteId);
        return getNote(userId, noteId);
    }

    @Transactional
    public NoteDetailResponse updateNote(UUID userId, UUID noteId, UpdateNoteRequest request) {
        NoteDetailResponse current = getNote(userId, noteId);
        String nextTitle = request.title() == null ? current.title() : request.title();
        String nextMarkdown = request.markdownContent() == null ? current.markdownContent() : request.markdownContent();
        NormalizedNote normalized = normalizeNoteInput(nextTitle, nextMarkdown);
        UUID folderId = Boolean.TRUE.equals(request.clearFolder())
            ? null
            : request.folderId() == null ? current.folderId() : normalizeOwnedFolder(userId, request.folderId());
        boolean contentChanged = !current.contentHash().equals(normalized.contentHash());
        jdbcTemplate.update(
            """
            UPDATE app.note
            SET folder_id = :folderId,
                title = :title,
                markdown_content = :markdownContent,
                plain_text = :plainText,
                content_hash = :contentHash,
                word_count = :wordCount,
                reading_minutes = :readingMinutes,
                last_saved_at = now()
            WHERE id = :noteId AND user_id = :userId AND status = 'ACTIVE'
            """,
            baseParams(userId)
                .addValue("noteId", noteId)
                .addValue("folderId", folderId)
                .addValue("title", normalized.title())
                .addValue("markdownContent", normalized.markdownContent())
                .addValue("plainText", normalized.plainText())
                .addValue("contentHash", normalized.contentHash())
                .addValue("wordCount", normalized.wordCount())
                .addValue("readingMinutes", normalized.readingMinutes())
        );
        if (request.tags() != null) {
            updateNoteTags(userId, noteId, request.tags());
        }
        upsertNoteResource(userId, noteId, ensureRagResourceId(userId, noteId), normalized, tagNamesForNote(noteId));
        if (contentChanged) {
            createVersion(userId, noteId, normalized, "自动保存");
            deleteStaleAnalysis(noteId);
        }
        indexNoteForRagAfterCommit(userId, noteId);
        return getNote(userId, noteId);
    }

    @Transactional
    public void deleteNote(UUID userId, UUID noteId) {
        int updated = jdbcTemplate.update(
            """
            UPDATE app.note
            SET status = 'DELETED', last_saved_at = now()
            WHERE id = :noteId AND user_id = :userId AND status = 'ACTIVE'
            """,
            baseParams(userId).addValue("noteId", noteId)
        );
        if (updated == 0) {
            throw new ApplicationException("NOTE_NOT_FOUND", "笔记不存在", HttpStatus.NOT_FOUND);
        }
        UUID resourceId = findRagResourceId(userId, noteId);
        if (resourceId != null) {
            jdbcTemplate.update(
                "UPDATE app.learning_resource SET status = 'DISABLED' WHERE id = :resourceId AND owner_user_id = :userId",
                baseParams(userId).addValue("resourceId", resourceId)
            );
        }
    }

    @Transactional(readOnly = true)
    public List<NoteFolderResponse> folders(UUID userId) {
        return jdbcTemplate.query(
            """
            SELECT f.id,
                   f.parent_id,
                   f.name,
                   f.sort_order,
                   f.created_at,
                   f.updated_at,
                   COUNT(n.id) FILTER (WHERE n.status = 'ACTIVE') AS note_count
            FROM app.note_folder f
            LEFT JOIN app.note n ON n.folder_id = f.id AND n.user_id = f.user_id
            WHERE f.user_id = :userId
            GROUP BY f.id, f.parent_id, f.name, f.sort_order, f.created_at, f.updated_at
            ORDER BY f.sort_order ASC, f.updated_at DESC
            """,
            baseParams(userId),
            (rs, rowNum) -> new NoteFolderResponse(
                (UUID) rs.getObject("id"),
                (UUID) rs.getObject("parent_id"),
                rs.getString("name"),
                rs.getInt("sort_order"),
                rs.getLong("note_count"),
                readOffsetDateTime(rs, "created_at"),
                readOffsetDateTime(rs, "updated_at")
            )
        );
    }

    @Transactional
    public NoteFolderResponse createFolder(UUID userId, CreateNoteFolderRequest request) {
        UUID parentId = normalizeOwnedFolder(userId, request.parentId());
        UUID folderId = UUID.randomUUID();
        try {
            jdbcTemplate.update(
                """
                INSERT INTO app.note_folder(id, user_id, parent_id, name, sort_order)
                VALUES (:folderId, :userId, :parentId, :name, :sortOrder)
                """,
                baseParams(userId)
                    .addValue("folderId", folderId)
                    .addValue("parentId", parentId)
                    .addValue("name", request.name().trim())
                    .addValue("sortOrder", request.sortOrder() == null ? 0 : request.sortOrder())
            );
        } catch (DuplicateKeyException ex) {
            throw new ApplicationException("FOLDER_DUPLICATED", "目录名称已存在", HttpStatus.CONFLICT);
        }
        return getFolder(userId, folderId);
    }

    @Transactional
    public NoteFolderResponse updateFolder(UUID userId, UUID folderId, UpdateNoteFolderRequest request) {
        getFolder(userId, folderId);
        UUID parentId = request.parentId() == null ? null : normalizeOwnedFolder(userId, request.parentId());
        if (folderId.equals(parentId)) {
            throw new ApplicationException("INVALID_FOLDER", "目录不能移动到自身下面", HttpStatus.BAD_REQUEST);
        }
        jdbcTemplate.update(
            """
            UPDATE app.note_folder
            SET name = COALESCE(:name, name),
                parent_id = :parentId,
                sort_order = COALESCE(:sortOrder, sort_order)
            WHERE id = :folderId AND user_id = :userId
            """,
            baseParams(userId)
                .addValue("folderId", folderId)
                .addValue("name", request.name() == null ? null : request.name().trim())
                .addValue("parentId", parentId)
                .addValue("sortOrder", request.sortOrder())
        );
        return getFolder(userId, folderId);
    }

    @Transactional
    public void deleteFolder(UUID userId, UUID folderId) {
        getFolder(userId, folderId);
        jdbcTemplate.update(
            "UPDATE app.note SET folder_id = NULL WHERE user_id = :userId AND folder_id = :folderId",
            baseParams(userId).addValue("folderId", folderId)
        );
        jdbcTemplate.update(
            "DELETE FROM app.note_folder WHERE user_id = :userId AND id = :folderId",
            baseParams(userId).addValue("folderId", folderId)
        );
    }

    @Transactional(readOnly = true)
    public List<NoteTagResponse> tags(UUID userId) {
        return jdbcTemplate.query(
            """
            SELECT t.id,
                   t.name,
                   t.color,
                   COUNT(n.id) AS count
            FROM app.note_tag t
            LEFT JOIN app.note_tag_link l ON l.tag_id = t.id
            LEFT JOIN app.note n ON n.id = l.note_id AND n.status = 'ACTIVE'
            WHERE t.user_id = :userId
            GROUP BY t.id, t.name, t.color
            ORDER BY count DESC, t.name ASC
            """,
            baseParams(userId),
            tagRowMapper()
        );
    }

    @Transactional
    public NoteDetailResponse updateTags(UUID userId, UUID noteId, UpdateNoteTagsRequest request) {
        getNote(userId, noteId);
        updateNoteTags(userId, noteId, request.tags());
        NoteDetailResponse note = getNote(userId, noteId);
        upsertNoteResource(userId, noteId, ensureRagResourceId(userId, noteId), normalizeNoteInput(note.title(), note.markdownContent()), tagNamesForNote(noteId));
        indexNoteForRagAfterCommit(userId, noteId);
        return getNote(userId, noteId);
    }

    @Transactional(readOnly = true)
    public List<NoteVersionResponse> versions(UUID userId, UUID noteId) {
        getNote(userId, noteId);
        return jdbcTemplate.query(
            """
            SELECT id, version_no, title, markdown_content, plain_text, content_hash, change_summary, created_at
            FROM app.note_version
            WHERE note_id = :noteId AND user_id = :userId
            ORDER BY version_no DESC
            """,
            baseParams(userId).addValue("noteId", noteId),
            versionRowMapper()
        );
    }

    @Transactional
    public NoteDetailResponse restoreVersion(UUID userId, UUID noteId, UUID versionId) {
        NoteVersionResponse version = findVersion(userId, noteId, versionId);
        NormalizedNote normalized = normalizeNoteInput(version.title(), version.markdownContent());
        jdbcTemplate.update(
            """
            UPDATE app.note
            SET title = :title,
                markdown_content = :markdownContent,
                plain_text = :plainText,
                content_hash = :contentHash,
                word_count = :wordCount,
                reading_minutes = :readingMinutes,
                last_saved_at = now()
            WHERE id = :noteId AND user_id = :userId AND status = 'ACTIVE'
            """,
            baseParams(userId)
                .addValue("noteId", noteId)
                .addValue("title", normalized.title())
                .addValue("markdownContent", normalized.markdownContent())
                .addValue("plainText", normalized.plainText())
                .addValue("contentHash", normalized.contentHash())
                .addValue("wordCount", normalized.wordCount())
                .addValue("readingMinutes", normalized.readingMinutes())
        );
        createVersion(userId, noteId, normalized, "恢复版本 " + version.versionNo());
        deleteStaleAnalysis(noteId);
        upsertNoteResource(userId, noteId, ensureRagResourceId(userId, noteId), normalized, tagNamesForNote(noteId));
        indexNoteForRagAfterCommit(userId, noteId);
        return getNote(userId, noteId);
    }

    @Transactional
    public NoteAnalysisResponse analyze(UUID userId, UUID noteId, boolean force) {
        NoteDetailResponse note = getNote(userId, noteId);
        if (!force) {
            NoteAnalysisResponse cached = findCachedAnalysis(noteId, note.contentHash());
            if (cached != null) {
                return cached;
            }
        }
        NoteAiAnalysisResult result = notePythonClient.analyze(note.title(), note.markdownContent(), note.plainText());
        Map<String, Object> resultJson = new LinkedHashMap<>();
        resultJson.put("summary", result.summary());
        resultJson.put("keywords", result.keywords());
        resultJson.put("todos", result.todos());
        writeAnalysisArtifact(userId, noteId, "SUMMARY", note.contentHash(), resultJson, result.provider(), result.model());
        return new NoteAnalysisResponse(
            note.contentHash(),
            result.summary(),
            result.keywords() == null ? List.of() : result.keywords(),
            result.todos() == null ? List.of() : result.todos().stream()
                .map(todo -> new NoteTodoResponse(todo.title(), todo.priority(), todo.completed()))
                .toList(),
            result.provider(),
            result.model(),
            OffsetDateTime.now(ZoneOffset.UTC),
            false
        );
    }

    @Transactional
    public ResourceSemanticSearchResponse relatedResources(UUID userId, UUID noteId, Integer topK) {
        NoteDetailResponse note = getNote(userId, noteId);
        String query = buildRelatedResourceQuery(note);
        ResourceSemanticSearchResponse response = resourceLibraryService.semanticSearch(userId, query, topK);
        persistRelatedResourceLinks(noteId, response.results());
        return response;
    }

    @Transactional(readOnly = true)
    public NoteSemanticSearchResponse semanticSearch(UUID userId, String query, Integer topK) {
        String normalizedQuery = query == null ? "" : query.trim();
        if (normalizedQuery.isBlank()) {
            throw new ApplicationException("INVALID_QUERY", "搜索内容不能为空", HttpStatus.BAD_REQUEST);
        }
        int limit = Math.max(1, Math.min(20, topK == null ? 8 : topK));
        try {
            NoteSemanticSearchResult raw = notePythonClient.semanticSearch(userId, normalizedQuery, limit);
            List<NoteSemanticResultResponse> hydrated = raw.results().stream()
                .map(result -> new NoteSemanticResultResponse(
                    findNoteListItemOrNull(userId, result.noteId()),
                    result.score(),
                    result.reason(),
                    result.hits().stream()
                        .map(hit -> new NoteSemanticHitResponse(hit.chunkId(), hit.chunkNo(), hit.similarity(), hit.content()))
                        .toList()
                ))
                .filter(result -> result.note() != null)
                .toList();
            return new NoteSemanticSearchResponse(raw.query(), raw.available(), raw.message(), hydrated);
        } catch (RuntimeException ex) {
            return new NoteSemanticSearchResponse(
                normalizedQuery,
                false,
                "笔记语义搜索暂不可用：" + ex.getMessage(),
                List.of()
            );
        }
    }

    private void indexNoteForRagAfterCommit(UUID userId, UUID noteId) {
        if (!TransactionSynchronizationManager.isSynchronizationActive()) {
            submitNoteIndex(userId, noteId);
            return;
        }
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCommit() {
                submitNoteIndex(userId, noteId);
            }
        });
    }

    private void submitNoteIndex(UUID userId, UUID noteId) {
        noteIndexTaskExecutor.execute(() -> safeIndexNoteForRag(userId, noteId));
    }

    private void safeIndexNoteForRag(UUID userId, UUID noteId) {
        try {
            indexNoteForRag(userId, noteId);
        } catch (RuntimeException ex) {
            LOGGER.warn("Note RAG indexing failed noteId={}: {}", noteId, ex.getMessage());
        }
    }

    private void indexNoteForRag(UUID userId, UUID noteId) {
        NoteDetailResponse note = getNote(userId, noteId);
        UUID resourceId = note.ragResourceId() == null ? ensureRagResourceId(userId, noteId) : note.ragResourceId();
        NoteRagIndexResult result = notePythonClient.index(new NoteRagIndexRequest(
            userId,
            noteId,
            resourceId,
            note.title(),
            note.markdownContent(),
            note.plainText(),
            note.contentHash(),
            note.tags().stream().map(NoteTagResponse::name).toList()
        ));
        if (!result.indexed()) {
            throw new IllegalStateException(result.message());
        }
    }

    private void upsertNoteResource(UUID userId, UUID noteId, UUID resourceId, NormalizedNote note, List<String> tags) {
        try {
            jdbcTemplate.update(
                """
                INSERT INTO app.learning_resource(
                  id, title, domain, resource_type, difficulty_level, source_kind,
                  access_scope, owner_user_id, summary_text, tags, metadata_json, status
                )
                VALUES (
                  :resourceId, :title, 'COMPUTER_SCIENCE', 'READING'::app.resource_type,
                  'MIXED'::app.difficulty_level, 'MANUAL'::app.source_kind,
                  'USER'::app.access_scope, :userId, :summaryText, CAST(:tags AS jsonb), CAST(:metadata AS jsonb), 'ACTIVE'
                )
                ON CONFLICT (id) DO UPDATE SET
                  title = EXCLUDED.title,
                  summary_text = EXCLUDED.summary_text,
                  tags = EXCLUDED.tags,
                  metadata_json = app.learning_resource.metadata_json || EXCLUDED.metadata_json,
                  status = 'ACTIVE',
                  updated_at = now()
                """,
                baseParams(userId)
                    .addValue("resourceId", resourceId)
                    .addValue("title", note.title())
                    .addValue("summaryText", preview(note.plainText(), 500))
                    .addValue("tags", writeJson(normalizeTags(tags)))
                    .addValue("metadata", writeJson(Map.of(
                        "displayType", "NOTE",
                        "noteId", noteId.toString(),
                        "sourceUrl", "/notes?noteId=" + noteId,
                        "noteContentHash", note.contentHash(),
                        "sourceName", "AI 笔记本"
                    )))
            );
        } catch (RuntimeException ex) {
            throw new ApplicationException("NOTE_RESOURCE_SYNC_FAILED", "笔记资源同步失败：" + ex.getMessage(), HttpStatus.BAD_GATEWAY);
        }
    }

    private UUID ensureRagResourceId(UUID userId, UUID noteId) {
        UUID existing = findRagResourceId(userId, noteId);
        if (existing != null) {
            return existing;
        }
        NoteDetailResponse note = getNote(userId, noteId);
        NormalizedNote normalized = normalizeNoteInput(note.title(), note.markdownContent());
        UUID resourceId = UUID.randomUUID();
        upsertNoteResource(userId, noteId, resourceId, normalized, tagNamesForNote(noteId));
        jdbcTemplate.update(
            "UPDATE app.note SET rag_resource_id = :resourceId WHERE id = :noteId AND user_id = :userId",
            baseParams(userId).addValue("noteId", noteId).addValue("resourceId", resourceId)
        );
        return resourceId;
    }

    private UUID findRagResourceId(UUID userId, UUID noteId) {
        try {
            return jdbcTemplate.queryForObject(
                "SELECT rag_resource_id FROM app.note WHERE id = :noteId AND user_id = :userId",
                baseParams(userId).addValue("noteId", noteId),
                UUID.class
            );
        } catch (EmptyResultDataAccessException ex) {
            return null;
        }
    }

    private void createVersion(UUID userId, UUID noteId, NormalizedNote note, String summary) {
        Integer nextVersion = jdbcTemplate.queryForObject(
            "SELECT COALESCE(MAX(version_no), 0) + 1 FROM app.note_version WHERE note_id = :noteId AND user_id = :userId",
            baseParams(userId).addValue("noteId", noteId),
            Integer.class
        );
        jdbcTemplate.update(
            """
            INSERT INTO app.note_version(
              note_id, user_id, version_no, title, markdown_content, plain_text, content_hash, change_summary
            )
            VALUES (:noteId, :userId, :versionNo, :title, :markdownContent, :plainText, :contentHash, :summary)
            """,
            baseParams(userId)
                .addValue("noteId", noteId)
                .addValue("versionNo", nextVersion == null ? 1 : nextVersion)
                .addValue("title", note.title())
                .addValue("markdownContent", note.markdownContent())
                .addValue("plainText", note.plainText())
                .addValue("contentHash", note.contentHash())
                .addValue("summary", summary)
        );
    }

    private void updateNoteTags(UUID userId, UUID noteId, List<String> rawTags) {
        List<String> tags = normalizeTags(rawTags);
        jdbcTemplate.update("DELETE FROM app.note_tag_link WHERE note_id = :noteId", new MapSqlParameterSource("noteId", noteId));
        for (String tag : tags) {
            UUID tagId = upsertTag(userId, tag);
            jdbcTemplate.update(
                """
                INSERT INTO app.note_tag_link(note_id, tag_id)
                VALUES (:noteId, :tagId)
                ON CONFLICT DO NOTHING
                """,
                new MapSqlParameterSource("noteId", noteId).addValue("tagId", tagId)
            );
        }
    }

    private UUID upsertTag(UUID userId, String tag) {
        return jdbcTemplate.queryForObject(
            """
            INSERT INTO app.note_tag(user_id, name, color)
            VALUES (:userId, :name, :color)
            ON CONFLICT (user_id, name) DO UPDATE SET updated_at = now()
            RETURNING id
            """,
            baseParams(userId).addValue("name", tag).addValue("color", colorForTag(tag)),
            UUID.class
        );
    }

    private NoteFolderResponse getFolder(UUID userId, UUID folderId) {
        try {
            return jdbcTemplate.queryForObject(
                """
                SELECT f.id,
                       f.parent_id,
                       f.name,
                       f.sort_order,
                       f.created_at,
                       f.updated_at,
                       COUNT(n.id) FILTER (WHERE n.status = 'ACTIVE') AS note_count
                FROM app.note_folder f
                LEFT JOIN app.note n ON n.folder_id = f.id AND n.user_id = f.user_id
                WHERE f.user_id = :userId AND f.id = :folderId
                GROUP BY f.id, f.parent_id, f.name, f.sort_order, f.created_at, f.updated_at
                """,
                baseParams(userId).addValue("folderId", folderId),
                (rs, rowNum) -> new NoteFolderResponse(
                    (UUID) rs.getObject("id"),
                    (UUID) rs.getObject("parent_id"),
                    rs.getString("name"),
                    rs.getInt("sort_order"),
                    rs.getLong("note_count"),
                    readOffsetDateTime(rs, "created_at"),
                    readOffsetDateTime(rs, "updated_at")
                )
            );
        } catch (EmptyResultDataAccessException ex) {
            throw new ApplicationException("FOLDER_NOT_FOUND", "目录不存在", HttpStatus.NOT_FOUND);
        }
    }

    private UUID normalizeOwnedFolder(UUID userId, UUID folderId) {
        if (folderId == null) {
            return null;
        }
        Boolean exists = jdbcTemplate.queryForObject(
            "SELECT EXISTS(SELECT 1 FROM app.note_folder WHERE id = :folderId AND user_id = :userId)",
            baseParams(userId).addValue("folderId", folderId),
            Boolean.class
        );
        if (!Boolean.TRUE.equals(exists)) {
            throw new ApplicationException("FOLDER_NOT_FOUND", "目录不存在", HttpStatus.NOT_FOUND);
        }
        return folderId;
    }

    private NoteDetailResponse findNoteDetailOrNull(UUID userId, UUID noteId) {
        List<NoteDetailResponse> rows = jdbcTemplate.query(
            """
            SELECT n.id,
                   n.folder_id,
                   n.title,
                   n.markdown_content,
                   n.plain_text,
                   n.content_hash,
                   n.word_count,
                   n.reading_minutes,
                   n.last_saved_at,
                   n.created_at,
                   n.updated_at,
                   n.rag_resource_id,
                   EXISTS(SELECT 1 FROM rag.resource_chunk rc WHERE rc.resource_id = n.rag_resource_id) AS rag_indexed,
                   COALESCE(tags.tags_json, '[]') AS tags_json
            FROM app.note n
            LEFT JOIN LATERAL (
              SELECT json_agg(json_build_object('id', t.id, 'name', t.name, 'color', t.color, 'count', 0) ORDER BY t.name) AS tags_json
              FROM app.note_tag_link l
              JOIN app.note_tag t ON t.id = l.tag_id
              WHERE l.note_id = n.id
            ) tags ON true
            WHERE n.id = :noteId AND n.user_id = :userId AND n.status = 'ACTIVE'
            """,
            baseParams(userId).addValue("noteId", noteId),
            noteDetailRowMapper()
        );
        return rows.isEmpty() ? null : rows.get(0);
    }

    private NoteListItemResponse findNoteListItemOrNull(UUID userId, UUID noteId) {
        List<NoteListItemResponse> rows = jdbcTemplate.query(
            noteListSelectSql() + """
            WHERE n.id = :noteId AND n.user_id = :userId AND n.status = 'ACTIVE'
            """,
            baseParams(userId).addValue("noteId", noteId),
            noteListRowMapper()
        );
        return rows.isEmpty() ? null : rows.get(0);
    }

    private NoteVersionResponse findVersion(UUID userId, UUID noteId, UUID versionId) {
        try {
            return jdbcTemplate.queryForObject(
                """
                SELECT id, version_no, title, markdown_content, plain_text, content_hash, change_summary, created_at
                FROM app.note_version
                WHERE id = :versionId AND note_id = :noteId AND user_id = :userId
                """,
                baseParams(userId).addValue("noteId", noteId).addValue("versionId", versionId),
                versionRowMapper()
            );
        } catch (EmptyResultDataAccessException ex) {
            throw new ApplicationException("VERSION_NOT_FOUND", "版本不存在", HttpStatus.NOT_FOUND);
        }
    }

    private NoteAnalysisResponse findCachedAnalysis(UUID noteId, String contentHash) {
        List<Map<String, Object>> rows = jdbcTemplate.queryForList(
            """
            SELECT result_json::text AS result_json, provider, model, created_at
            FROM app.note_ai_artifact
            WHERE note_id = :noteId AND input_hash = :contentHash AND artifact_type = 'SUMMARY'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            new MapSqlParameterSource("noteId", noteId).addValue("contentHash", contentHash)
        );
        if (rows.isEmpty()) {
            return null;
        }
        Map<String, Object> row = rows.get(0);
        Map<String, Object> result = parseMap(String.valueOf(row.get("result_json")));
        List<String> keywords = readStringList(result.get("keywords"));
        List<NoteTodoResponse> todos = readTodos(result.get("todos"));
        return new NoteAnalysisResponse(
            contentHash,
            readString(result.get("summary")),
            keywords,
            todos,
            readString(row.get("provider")),
            readString(row.get("model")),
            readOffsetDateTime(row.get("created_at")),
            true
        );
    }

    private void writeAnalysisArtifact(
        UUID userId,
        UUID noteId,
        String artifactType,
        String inputHash,
        Map<String, Object> resultJson,
        String provider,
        String model
    ) {
        jdbcTemplate.update(
            """
            INSERT INTO app.note_ai_artifact(note_id, user_id, artifact_type, input_hash, result_json, provider, model)
            VALUES (:noteId, :userId, :artifactType, :inputHash, CAST(:resultJson AS jsonb), :provider, :model)
            ON CONFLICT (note_id, artifact_type, input_hash) DO UPDATE SET
              result_json = EXCLUDED.result_json,
              provider = EXCLUDED.provider,
              model = EXCLUDED.model,
              created_at = now()
            """,
            baseParams(userId)
                .addValue("noteId", noteId)
                .addValue("artifactType", artifactType)
                .addValue("inputHash", inputHash)
                .addValue("resultJson", writeJson(resultJson))
                .addValue("provider", provider == null ? "" : provider)
                .addValue("model", model == null ? "" : model)
        );
    }

    private void deleteStaleAnalysis(UUID noteId) {
        jdbcTemplate.update("DELETE FROM app.note_ai_artifact WHERE note_id = :noteId", new MapSqlParameterSource("noteId", noteId));
    }

    private void persistRelatedResourceLinks(UUID noteId, List<ResourceSemanticResultResponse> results) {
        for (ResourceSemanticResultResponse result : results) {
            if (result.resourceId() == null) {
                continue;
            }
            jdbcTemplate.update(
                """
                INSERT INTO app.note_resource_link(note_id, resource_id, relation_type, score, reason)
                VALUES (:noteId, :resourceId, 'RELATED', :score, :reason)
                ON CONFLICT (note_id, resource_id, relation_type) DO UPDATE SET
                  score = EXCLUDED.score,
                  reason = EXCLUDED.reason,
                  created_at = now()
                """,
                new MapSqlParameterSource("noteId", noteId)
                    .addValue("resourceId", result.resourceId())
                    .addValue("score", result.score())
                    .addValue("reason", result.reason() == null ? "" : result.reason())
            );
        }
    }

    private List<String> noteConditions(String keyword, UUID folderId, String tag, MapSqlParameterSource params) {
        List<String> conditions = new ArrayList<>();
        conditions.add("n.user_id = :userId");
        conditions.add("n.status = 'ACTIVE'");
        if (keyword != null && !keyword.isBlank()) {
            conditions.add("(n.title ILIKE :keyword OR n.plain_text ILIKE :keyword)");
            params.addValue("keyword", "%" + keyword.trim() + "%");
        }
        if (folderId != null) {
            conditions.add("n.folder_id = :folderId");
            params.addValue("folderId", folderId);
        }
        if (tag != null && !tag.isBlank()) {
            conditions.add("""
                EXISTS (
                  SELECT 1
                  FROM app.note_tag_link ntl
                  JOIN app.note_tag nt ON nt.id = ntl.tag_id
                  WHERE ntl.note_id = n.id AND nt.user_id = :userId AND nt.name = :tag
                )
                """);
            params.addValue("tag", tag.trim());
        }
        return conditions;
    }

    private String noteListSelectSql() {
        return """
            SELECT n.id,
                   n.folder_id,
                   n.title,
                   n.plain_text,
                   n.word_count,
                   n.reading_minutes,
                   n.last_saved_at,
                   n.updated_at,
                   n.rag_resource_id,
                   EXISTS(SELECT 1 FROM rag.resource_chunk rc WHERE rc.resource_id = n.rag_resource_id) AS rag_indexed,
                   COALESCE(tags.tags_json, '[]') AS tags_json
            FROM app.note n
            LEFT JOIN LATERAL (
              SELECT json_agg(json_build_object('id', t.id, 'name', t.name, 'color', t.color, 'count', 0) ORDER BY t.name) AS tags_json
              FROM app.note_tag_link l
              JOIN app.note_tag t ON t.id = l.tag_id
              WHERE l.note_id = n.id
            ) tags ON true
            """;
    }

    private RowMapper<NoteListItemResponse> noteListRowMapper() {
        return (rs, rowNum) -> new NoteListItemResponse(
            (UUID) rs.getObject("id"),
            (UUID) rs.getObject("folder_id"),
            rs.getString("title"),
            preview(rs.getString("plain_text"), 180),
            parseTags(rs.getString("tags_json")),
            rs.getInt("word_count"),
            rs.getInt("reading_minutes"),
            readOffsetDateTime(rs, "last_saved_at"),
            readOffsetDateTime(rs, "updated_at"),
            rs.getBoolean("rag_indexed")
        );
    }

    private RowMapper<NoteDetailResponse> noteDetailRowMapper() {
        return (rs, rowNum) -> new NoteDetailResponse(
            (UUID) rs.getObject("id"),
            (UUID) rs.getObject("folder_id"),
            rs.getString("title"),
            rs.getString("markdown_content"),
            rs.getString("plain_text"),
            rs.getString("content_hash"),
            parseTags(rs.getString("tags_json")),
            rs.getInt("word_count"),
            rs.getInt("reading_minutes"),
            readOffsetDateTime(rs, "last_saved_at"),
            readOffsetDateTime(rs, "created_at"),
            readOffsetDateTime(rs, "updated_at"),
            rs.getBoolean("rag_indexed"),
            (UUID) rs.getObject("rag_resource_id")
        );
    }

    private RowMapper<NoteTagResponse> tagRowMapper() {
        return (rs, rowNum) -> new NoteTagResponse(
            (UUID) rs.getObject("id"),
            rs.getString("name"),
            rs.getString("color"),
            rs.getLong("count")
        );
    }

    private RowMapper<NoteVersionResponse> versionRowMapper() {
        return (rs, rowNum) -> new NoteVersionResponse(
            (UUID) rs.getObject("id"),
            rs.getInt("version_no"),
            rs.getString("title"),
            rs.getString("markdown_content"),
            rs.getString("plain_text"),
            rs.getString("content_hash"),
            rs.getString("change_summary"),
            readOffsetDateTime(rs, "created_at")
        );
    }

    private NormalizedNote normalizeNoteInput(String rawTitle, String rawMarkdown) {
        String markdown = rawMarkdown == null ? "" : rawMarkdown.trim();
        String plainText = toPlainText(markdown);
        String title = rawTitle == null || rawTitle.isBlank()
            ? deriveTitle(plainText)
            : rawTitle.trim();
        int wordCount = countWords(plainText);
        int readingMinutes = Math.max(1, (int) Math.ceil(wordCount / 320.0));
        String hash = sha256(title + "\n" + markdown);
        return new NormalizedNote(title, markdown, plainText, hash, wordCount, readingMinutes);
    }

    private String toPlainText(String markdown) {
        String noCodeFence = markdown.replaceAll("(?s)```.*?```", " ");
        String noImages = noCodeFence.replaceAll("!\\[[^]]*]\\([^)]*\\)", " ");
        String noLinks = noImages.replaceAll("\\[([^]]+)]\\([^)]*\\)", "$1");
        String stripped = MARKDOWN_SYNTAX.matcher(noLinks).replaceAll(" ");
        return stripped.replaceAll("\\s+", " ").trim();
    }

    private String deriveTitle(String plainText) {
        if (plainText == null || plainText.isBlank()) {
            return "未命名笔记";
        }
        return preview(plainText, 40);
    }

    private int countWords(String plainText) {
        if (plainText == null || plainText.isBlank()) {
            return 0;
        }
        int cjk = 0;
        int asciiWords = 0;
        boolean inAsciiWord = false;
        for (int i = 0; i < plainText.length(); i += 1) {
            char ch = plainText.charAt(i);
            if (Character.UnicodeScript.of(ch) == Character.UnicodeScript.HAN) {
                cjk += 1;
                inAsciiWord = false;
                continue;
            }
            if (Character.isLetterOrDigit(ch)) {
                if (!inAsciiWord) {
                    asciiWords += 1;
                    inAsciiWord = true;
                }
            } else {
                inAsciiWord = false;
            }
        }
        return cjk + asciiWords;
    }

    private String buildRelatedResourceQuery(NoteDetailResponse note) {
        String tags = String.join(" ", note.tags().stream().map(NoteTagResponse::name).toList());
        return (note.title() + "\n" + tags + "\n" + preview(note.plainText(), 1200)).trim();
    }

    private List<String> normalizeTags(List<String> rawTags) {
        if (rawTags == null || rawTags.isEmpty()) {
            return List.of();
        }
        LinkedHashSet<String> normalized = new LinkedHashSet<>();
        for (String raw : rawTags) {
            if (raw == null) {
                continue;
            }
            String tag = raw.trim();
            if (!tag.isBlank()) {
                normalized.add(tag.length() > 32 ? tag.substring(0, 32) : tag);
            }
            if (normalized.size() >= MAX_TAGS_PER_NOTE) {
                break;
            }
        }
        return List.copyOf(normalized);
    }

    private List<String> tagNamesForNote(UUID noteId) {
        return jdbcTemplate.query(
            """
            SELECT t.name
            FROM app.note_tag_link l
            JOIN app.note_tag t ON t.id = l.tag_id
            WHERE l.note_id = :noteId
            ORDER BY t.name
            """,
            new MapSqlParameterSource("noteId", noteId),
            (rs, rowNum) -> rs.getString("name")
        );
    }

    private List<NoteTagResponse> parseTags(String json) {
        if (json == null || json.isBlank()) {
            return List.of();
        }
        try {
            return objectMapper.readValue(json, TAG_LIST_TYPE).stream()
                .map(item -> new NoteTagResponse(
                    readUuid(item.get("id")),
                    readString(item.get("name")),
                    readString(item.get("color")),
                    readLong(item.get("count"))
                ))
                .toList();
        } catch (JsonProcessingException ex) {
            return List.of();
        }
    }

    private List<String> readStringList(Object value) {
        if (!(value instanceof List<?> list)) {
            return List.of();
        }
        return list.stream()
            .map(String::valueOf)
            .map(String::trim)
            .filter(item -> !item.isBlank())
            .toList();
    }

    private List<NoteTodoResponse> readTodos(Object value) {
        if (!(value instanceof List<?> list)) {
            return List.of();
        }
        List<NoteTodoResponse> todos = new ArrayList<>();
        for (Object item : list) {
            if (item instanceof Map<?, ?> map) {
                todos.add(new NoteTodoResponse(
                    readString(map.get("title")),
                    readString(map.get("priority")),
                    Boolean.TRUE.equals(map.get("completed"))
                ));
            } else if (item instanceof NoteTodoResult todo) {
                todos.add(new NoteTodoResponse(todo.title(), todo.priority(), todo.completed()));
            }
        }
        return todos;
    }

    private Map<String, Object> parseMap(String json) {
        if (json == null || json.isBlank()) {
            return Map.of();
        }
        try {
            return objectMapper.readValue(json, STRING_OBJECT_MAP);
        } catch (JsonProcessingException ex) {
            return Map.of();
        }
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException ex) {
            throw new IllegalStateException("JSON 序列化失败", ex);
        }
    }

    private String colorForTag(String tag) {
        String[] palette = {"#4f46e5", "#0891b2", "#059669", "#d97706", "#dc2626", "#7c3aed"};
        return palette[Math.floorMod(tag.toLowerCase(Locale.ROOT).hashCode(), palette.length)];
    }

    private MapSqlParameterSource baseParams(UUID userId) {
        return new MapSqlParameterSource("userId", userId);
    }

    private OffsetDateTime readOffsetDateTime(ResultSet rs, String column) throws SQLException {
        Timestamp timestamp = rs.getTimestamp(column);
        return timestamp == null ? null : timestamp.toInstant().atOffset(ZoneOffset.UTC);
    }

    private OffsetDateTime readOffsetDateTime(Object value) {
        if (value instanceof Timestamp timestamp) {
            return timestamp.toInstant().atOffset(ZoneOffset.UTC);
        }
        if (value instanceof OffsetDateTime offsetDateTime) {
            return offsetDateTime;
        }
        return null;
    }

    private UUID readUuid(Object value) {
        if (value instanceof UUID uuid) {
            return uuid;
        }
        if (value instanceof String text && !text.isBlank()) {
            return UUID.fromString(text);
        }
        return null;
    }

    private long readLong(Object value) {
        if (value instanceof Number number) {
            return number.longValue();
        }
        return 0L;
    }

    private String readString(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private String preview(String input, int maxLength) {
        if (input == null || input.isBlank()) {
            return "";
        }
        String normalized = input.trim();
        return normalized.length() <= maxLength ? normalized : normalized.substring(0, maxLength);
    }

    private String sha256(String text) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(digest.digest(text.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException ex) {
            throw new IllegalStateException("SHA-256 不可用", ex);
        }
    }

    private record NormalizedNote(
        String title,
        String markdownContent,
        String plainText,
        String contentHash,
        int wordCount,
        int readingMinutes
    ) {
    }
}
