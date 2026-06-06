package com.project.api.resource;

import com.project.api.resource.dto.ResourceDetailResponse;
import com.project.api.resource.dto.ResourceItemResponse;
import com.project.api.resource.dto.ResourceListResponse;
import com.project.api.resource.dto.ResourceProgressRequest;
import com.project.api.resource.dto.ResourceSemanticSearchResponse;
import com.project.api.resource.dto.ResourceStatsResponse;
import com.project.api.resource.dto.ResourceTagResponse;
import com.project.api.resource.dto.ResourceUserStateResponse;
import com.project.application.resource.ResourceLibraryService;
import com.project.security.AuthenticatedUserResolver;
import com.project.security.JwtAuthenticatedUser;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.UUID;

@RestController
@RequestMapping("/api/resources")
@Tag(name = "Resource Library")
public class ResourceLibraryController {

    private final ResourceLibraryService resourceLibraryService;

    public ResourceLibraryController(ResourceLibraryService resourceLibraryService) {
        this.resourceLibraryService = resourceLibraryService;
    }

    @GetMapping
    @Operation(summary = "List learning resources")
    public ResponseEntity<ResourceListResponse> listResources(
        Authentication authentication,
        @RequestParam(name = "keyword", required = false) String keyword,
        @RequestParam(name = "type", required = false) String type,
        @RequestParam(name = "domain", required = false) String domain,
        @RequestParam(name = "subject", required = false) String subject,
        @RequestParam(name = "category", required = false) String category,
        @RequestParam(name = "subcategory", required = false) String subcategory,
        @RequestParam(name = "difficulty", required = false) String difficulty,
        @RequestParam(name = "source", required = false) String source,
        @RequestParam(name = "favoriteOnly", required = false) Boolean favoriteOnly,
        @RequestParam(name = "sort", required = false) String sort,
        @RequestParam(name = "page", required = false) Integer page,
        @RequestParam(name = "size", required = false) Integer size
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(resourceLibraryService.listResources(
            principal.userId(),
            keyword,
            type,
            domain == null || domain.isBlank() ? subject : domain,
            category,
            subcategory,
            difficulty,
            source,
            favoriteOnly,
            sort,
            page,
            size
        ));
    }

    @GetMapping("/{id}")
    @Operation(summary = "Get learning resource details")
    public ResponseEntity<ResourceDetailResponse> getResource(
        Authentication authentication,
        @PathVariable UUID id
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(resourceLibraryService.getResource(principal.userId(), id));
    }

    @PostMapping("/{id}/favorite")
    @Operation(summary = "Favorite a learning resource")
    public ResponseEntity<ResourceUserStateResponse> favorite(
        Authentication authentication,
        @PathVariable UUID id
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(resourceLibraryService.setFavorite(principal.userId(), id, true));
    }

    @DeleteMapping("/{id}/favorite")
    @Operation(summary = "Unfavorite a learning resource")
    public ResponseEntity<ResourceUserStateResponse> unfavorite(
        Authentication authentication,
        @PathVariable UUID id
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(resourceLibraryService.setFavorite(principal.userId(), id, false));
    }

    @PostMapping("/{id}/progress")
    @Operation(summary = "Update learning progress for a resource")
    public ResponseEntity<ResourceUserStateResponse> updateProgress(
        Authentication authentication,
        @PathVariable UUID id,
        @Valid @RequestBody(required = false) ResourceProgressRequest request
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(resourceLibraryService.updateProgress(principal.userId(), id, request));
    }

    @GetMapping("/search/semantic")
    @Operation(summary = "Semantic search learning resources")
    public ResponseEntity<ResourceSemanticSearchResponse> semanticSearch(
        Authentication authentication,
        @RequestParam(name = "query") String query,
        @RequestParam(name = "topK", required = false) Integer topK
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(resourceLibraryService.semanticSearch(principal.userId(), query, topK));
    }

    @GetMapping("/recommendations")
    @Operation(summary = "Get recommended learning resources")
    public ResponseEntity<List<ResourceItemResponse>> recommendations(
        Authentication authentication,
        @RequestParam(name = "limit", required = false) Integer limit
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(resourceLibraryService.recommendations(principal.userId(), limit));
    }

    @GetMapping("/stats")
    @Operation(summary = "Get resource learning statistics")
    public ResponseEntity<ResourceStatsResponse> stats(Authentication authentication) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(resourceLibraryService.stats(principal.userId()));
    }

    @GetMapping("/tags")
    @Operation(summary = "Get hot resource tags")
    public ResponseEntity<List<ResourceTagResponse>> tags(
        Authentication authentication,
        @RequestParam(name = "limit", required = false) Integer limit
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(resourceLibraryService.tags(principal.userId(), limit));
    }
}
