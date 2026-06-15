package com.project.api.note;

import com.project.api.note.dto.CreateNoteFolderRequest;
import com.project.api.note.dto.CreateNoteRequest;
import com.project.api.note.dto.NoteAnalysisResponse;
import com.project.api.note.dto.NoteDetailResponse;
import com.project.api.note.dto.NoteFolderResponse;
import com.project.api.note.dto.NoteListResponse;
import com.project.api.note.dto.NoteSemanticSearchResponse;
import com.project.api.note.dto.NoteTagResponse;
import com.project.api.note.dto.NoteVersionResponse;
import com.project.api.note.dto.UpdateNoteFolderRequest;
import com.project.api.note.dto.UpdateNoteRequest;
import com.project.api.note.dto.UpdateNoteTagsRequest;
import com.project.api.resource.dto.ResourceSemanticSearchResponse;
import com.project.application.common.ApplicationException;
import com.project.application.note.NoteService;
import com.project.security.AuthenticatedUserResolver;
import com.project.security.JwtAuthenticatedUser;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.UUID;

@RestController
@RequestMapping("/api/notes")
@Tag(name = "AI Notebook")
public class NoteController {

    private final NoteService noteService;

    public NoteController(NoteService noteService) {
        this.noteService = noteService;
    }

    @GetMapping
    @Operation(summary = "List notes")
    public ResponseEntity<NoteListResponse> listNotes(
        Authentication authentication,
        @RequestParam(required = false) String keyword,
        @RequestParam(required = false) UUID folderId,
        @RequestParam(required = false) String tag,
        @RequestParam(required = false) Integer page,
        @RequestParam(required = false) Integer size
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(noteService.listNotes(principal.userId(), keyword, folderId, tag, page, size));
    }

    @PostMapping
    @Operation(summary = "Create a note")
    public ResponseEntity<NoteDetailResponse> createNote(
        Authentication authentication,
        @Valid @RequestBody(required = false) CreateNoteRequest request
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        CreateNoteRequest safeRequest = request == null ? new CreateNoteRequest(null, null, null, List.of()) : request;
        return ResponseEntity.ok(noteService.createNote(principal.userId(), safeRequest));
    }

    @GetMapping("/{noteId}")
    @Operation(summary = "Get a note")
    public ResponseEntity<NoteDetailResponse> getNote(
        Authentication authentication,
        @PathVariable UUID noteId
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(noteService.getNote(principal.userId(), noteId));
    }

    @PutMapping("/{noteId}")
    @Operation(summary = "Update a note")
    public ResponseEntity<NoteDetailResponse> updateNote(
        Authentication authentication,
        @PathVariable UUID noteId,
        @Valid @RequestBody UpdateNoteRequest request
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(noteService.updateNote(principal.userId(), noteId, request));
    }

    @DeleteMapping("/{noteId}")
    @Operation(summary = "Delete a note")
    public ResponseEntity<Void> deleteNote(
        Authentication authentication,
        @PathVariable UUID noteId
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        noteService.deleteNote(principal.userId(), noteId);
        return ResponseEntity.noContent().build();
    }

    @GetMapping("/folders")
    @Operation(summary = "List note folders")
    public ResponseEntity<List<NoteFolderResponse>> folders(Authentication authentication) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(noteService.folders(principal.userId()));
    }

    @PostMapping("/folders")
    @Operation(summary = "Create a note folder")
    public ResponseEntity<NoteFolderResponse> createFolder(
        Authentication authentication,
        @Valid @RequestBody CreateNoteFolderRequest request
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(noteService.createFolder(principal.userId(), request));
    }

    @PutMapping("/folders/{folderId}")
    @Operation(summary = "Update a note folder")
    public ResponseEntity<NoteFolderResponse> updateFolder(
        Authentication authentication,
        @PathVariable UUID folderId,
        @Valid @RequestBody UpdateNoteFolderRequest request
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(noteService.updateFolder(principal.userId(), folderId, request));
    }

    @DeleteMapping("/folders/{folderId}")
    @Operation(summary = "Delete a note folder")
    public ResponseEntity<Void> deleteFolder(
        Authentication authentication,
        @PathVariable UUID folderId
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        noteService.deleteFolder(principal.userId(), folderId);
        return ResponseEntity.noContent().build();
    }

    @GetMapping("/tags")
    @Operation(summary = "List note tags")
    public ResponseEntity<List<NoteTagResponse>> tags(Authentication authentication) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(noteService.tags(principal.userId()));
    }

    @PutMapping("/{noteId}/tags")
    @Operation(summary = "Replace note tags")
    public ResponseEntity<NoteDetailResponse> updateTags(
        Authentication authentication,
        @PathVariable UUID noteId,
        @Valid @RequestBody UpdateNoteTagsRequest request
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(noteService.updateTags(principal.userId(), noteId, request));
    }

    @GetMapping("/{noteId}/versions")
    @Operation(summary = "List note versions")
    public ResponseEntity<List<NoteVersionResponse>> versions(
        Authentication authentication,
        @PathVariable UUID noteId
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(noteService.versions(principal.userId(), noteId));
    }

    @PostMapping("/{noteId}/versions/{versionId}/restore")
    @Operation(summary = "Restore note version")
    public ResponseEntity<NoteDetailResponse> restoreVersion(
        Authentication authentication,
        @PathVariable UUID noteId,
        @PathVariable UUID versionId
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(noteService.restoreVersion(principal.userId(), noteId, versionId));
    }

    @PostMapping("/{noteId}/ai/analyze")
    @Operation(summary = "Analyze note with AI")
    public ResponseEntity<NoteAnalysisResponse> analyze(
        Authentication authentication,
        @PathVariable UUID noteId,
        @RequestParam(required = false) Boolean force
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(noteService.analyze(principal.userId(), noteId, Boolean.TRUE.equals(force)));
    }

    @GetMapping("/{noteId}/related-resources")
    @Operation(summary = "Search resources related to a note")
    public ResponseEntity<ResourceSemanticSearchResponse> relatedResources(
        Authentication authentication,
        @PathVariable UUID noteId,
        @RequestParam(required = false) Integer topK
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(noteService.relatedResources(principal.userId(), noteId, topK));
    }

    @GetMapping("/search/semantic")
    @Operation(summary = "Semantic search user notes")
    public ResponseEntity<NoteSemanticSearchResponse> semanticSearch(
        Authentication authentication,
        @RequestParam(required = false) String query,
        @RequestParam(required = false, name = "q") String queryAlias,
        @RequestParam(required = false) Integer topK
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(noteService.semanticSearch(principal.userId(), resolveSemanticQuery(query, queryAlias), topK));
    }

    private String resolveSemanticQuery(String query, String queryAlias) {
        String resolved = query == null || query.isBlank() ? queryAlias : query;
        if (resolved == null || resolved.isBlank()) {
            throw new ApplicationException("INVALID_ARGUMENT", "搜索关键词不能为空", HttpStatus.BAD_REQUEST);
        }
        return resolved;
    }
}
