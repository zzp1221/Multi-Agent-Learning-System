package com.project.application.conversation;

import com.project.api.conversation.dto.ConversationMessageStreamRequest;
import com.project.application.artifact.ArtifactDownloadDescriptor;
import com.project.application.artifact.ArtifactDownloadService;
import com.project.application.smartengine.PythonStreamEvent;
import com.project.security.JwtAuthenticatedUser;
import com.project.domain.artifact.ResourceType;
import com.project.domain.conversation.QnaSessionRepository;
import com.project.domain.profile.UserProfileCurrentRepository;
import com.project.domain.task.ServiceType;
import com.project.domain.task.SmartEngineTask;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ConversationServiceResourceFileSigningTest {

    @Test
    void conversationParamsPromoteConfirmedSlideOutlineForPythonGeneration() {
        ConversationService service = new ConversationService(
            mock(QnaSessionRepository.class),
            null,
            null,
            null,
            mock(UserProfileCurrentRepository.class),
            null,
            null,
            null,
            null,
            mock(ArtifactDownloadService.class),
            null
        );
        ConversationMessageStreamRequest request = new ConversationMessageStreamRequest(
            "confirm slide outline and generate PPT file",
            List.of(),
            ServiceType.TUTORING,
            false,
            null,
            new com.project.api.conversation.dto.VoiceContextRequest(
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                "conversation_resource_generation",
                null,
                null,
                "generate_slides",
                null,
                null,
                "RESOURCE_GENERATION",
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                "true",
                "outline-confirmed"
            )
        );

        Map<String, Object> params = ReflectionTestUtils.invokeMethod(
            service,
            "buildConversationParams",
            new JwtAuthenticatedUser(UUID.randomUUID(), "learner", "STUDENT"),
            UUID.randomUUID(),
            request,
            List.<com.project.api.conversation.dto.ConversationMessageItemResponse>of()
        );

        assertThat(params)
            .containsEntry("confirmedSlideOutline", "true")
            .containsEntry("confirmedSlideOutlineText", "outline-confirmed");
        assertThat(params.get("learningContext"))
            .isInstanceOf(Map.class)
            .asInstanceOf(org.assertj.core.api.InstanceOfAssertFactories.MAP)
            .containsEntry("confirmedSlideOutline", "true")
            .containsEntry("confirmedSlideOutlineText", "outline-confirmed");
    }

    @Test
    void conversationResourceFileIsSignedAndSandboxPathsAreRemoved() {
        UUID userId = UUID.randomUUID();
        QnaSessionRepository repository = mock(QnaSessionRepository.class);
        ArtifactDownloadService downloadService = mock(ArtifactDownloadService.class);
        when(downloadService.issueDownload(
            any(SmartEngineTask.class),
            eq(ResourceType.DOCUMENT),
            eq("联合索引讲义"),
            eq("guide.md"),
            eq("D:/tmp/guide.md"),
            eq("text/markdown")
        )).thenReturn(new ArtifactDownloadDescriptor("/api/assets/download/token123", 3600, OffsetDateTime.parse("2026-06-05T10:00:00Z")));
        ConversationService service = new ConversationService(
            repository,
            null,
            null,
            null,
            null,
            null,
            null,
            null,
            null,
            downloadService,
            null
        );
        SmartEngineTask task = new SmartEngineTask();
        task.setId(UUID.randomUUID());
        task.setUserId(userId);
        task.setTraceId("trace-conversation-resource");
        task.setServiceType(ServiceType.TUTORING);
        PythonStreamEvent event = new PythonStreamEvent(
            "resource_file",
            "document_generation",
            Map.ofEntries(
                Map.entry("assetType", "DOCUMENT"),
                Map.entry("title", "联合索引讲义"),
                Map.entry("fileName", "guide.md"),
                Map.entry("localPath", "D:/tmp/guide.md"),
                Map.entry("mimeType", "text/markdown"),
                Map.entry("generatedBy", "LLM"),
                Map.entry("contentOrigin", "LLM"),
                Map.entry("provider", "unit-provider"),
                Map.entry("model", "unit-model"),
                Map.entry("agentName", "document_generation"),
                Map.entry("evidenceIds", List.of("doc-1")),
                Map.entry("fallback", false),
                Map.entry("fromCache", false)
            )
        );

        PythonStreamEvent signed = ReflectionTestUtils.invokeMethod(
            service,
            "signConversationResourceFileEvent",
            task,
            event
        );

        Map<String, Object> capturedPayload = signed.safePayload();
        assertThat(signed.eventType()).isEqualTo("resource_file");
        assertThat(capturedPayload).containsEntry("downloadUrl", "/api/assets/download/token123");
        assertThat(capturedPayload).doesNotContainKeys("localPath", "sandboxPath");
        verify(downloadService).issueDownload(any(SmartEngineTask.class), eq(ResourceType.DOCUMENT), eq("联合索引讲义"), eq("guide.md"), eq("D:/tmp/guide.md"), eq("text/markdown"));
    }

    @Test
    void slidesResourceFileIsSignedEvenWhenLegacyOutlineDisplayModeAppears() {
        ArtifactDownloadService downloadService = mock(ArtifactDownloadService.class);
        when(downloadService.issueDownload(
            any(SmartEngineTask.class),
            eq(ResourceType.SLIDES),
            eq("联合索引 PPT 大纲"),
            eq("outline.md"),
            eq("/sandbox/outline.md"),
            eq("text/markdown")
        )).thenReturn(new ArtifactDownloadDescriptor("/api/assets/download/slides-outline", 3600, OffsetDateTime.parse("2026-06-05T10:00:00Z")));
        ConversationService service = new ConversationService(
            mock(QnaSessionRepository.class),
            null,
            null,
            null,
            null,
            null,
            null,
            null,
            null,
            downloadService,
            null
        );
        SmartEngineTask task = new SmartEngineTask();
        task.setId(UUID.randomUUID());
        task.setUserId(UUID.randomUUID());
        task.setTraceId("trace-pending-slides");
        task.setServiceType(ServiceType.TUTORING);
        PythonStreamEvent event = new PythonStreamEvent(
            "resource_file",
            "slides_outline",
            Map.ofEntries(
                Map.entry("assetType", "SLIDES"),
                Map.entry("displayMode", "SLIDE_OUTLINE_CONFIRMATION"),
                Map.entry("title", "联合索引 PPT 大纲"),
                Map.entry("inlineContent", "# 联合索引 PPT 大纲"),
                Map.entry("fileName", "outline.md"),
                Map.entry("mimeType", "text/markdown"),
                Map.entry("localPath", "D:/tmp/outline.md"),
                Map.entry("sandboxPath", "/sandbox/outline.md"),
                Map.entry("generatedBy", "LLM"),
                Map.entry("contentOrigin", "LLM"),
                Map.entry("provider", "unit-provider"),
                Map.entry("model", "unit-model"),
                Map.entry("agentName", "slide_generator"),
                Map.entry("evidenceIds", List.of("doc-1")),
                Map.entry("fallback", false),
                Map.entry("fromCache", false)
            )
        );

        PythonStreamEvent signed = ReflectionTestUtils.invokeMethod(
            service,
            "signConversationResourceFileEvent",
            task,
            event
        );

        Map<String, Object> capturedPayload = signed.safePayload();
        assertThat(signed.eventType()).isEqualTo("resource_file");
        assertThat(capturedPayload).containsEntry("inlineContent", "# 联合索引 PPT 大纲");
        assertThat(capturedPayload).containsEntry("downloadUrl", "/api/assets/download/slides-outline");
        assertThat(capturedPayload).doesNotContainKeys("localPath", "sandboxPath");
        verify(downloadService).issueDownload(any(SmartEngineTask.class), eq(ResourceType.SLIDES), eq("联合索引 PPT 大纲"), eq("outline.md"), eq("/sandbox/outline.md"), eq("text/markdown"));
    }
}
