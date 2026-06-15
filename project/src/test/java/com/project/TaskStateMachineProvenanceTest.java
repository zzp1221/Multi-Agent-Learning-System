package com.project;

import com.project.application.artifact.ArtifactDownloadService;
import com.project.application.smartengine.PythonStreamEvent;
import com.project.application.smartengine.SmartEngineTaskEventCache;
import com.project.application.smartengine.TaskEventRecordResult;
import com.project.application.smartengine.TaskStateMachineService;
import com.project.application.smartengine.TaskStreamEventPayload;
import com.project.application.smartengine.VideoGenerationTaskService;
import com.project.domain.task.ServiceType;
import com.project.domain.task.SmartEngineTask;
import com.project.domain.task.SmartEngineTaskRepository;
import com.project.domain.task.TaskStatus;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class TaskStateMachineProvenanceTest {

    @Mock
    private SmartEngineTaskRepository taskRepository;

    @Mock
    private ArtifactDownloadService artifactDownloadService;

    @Mock
    private VideoGenerationTaskService videoGenerationTaskService;

    private TaskStateMachineService service;
    private SmartEngineTaskEventCache taskEventCache;
    private SmartEngineTask task;
    private UUID taskId;

    @BeforeEach
    void setUp() {
        taskEventCache = new SmartEngineTaskEventCache();
        service = new TaskStateMachineService(
            taskRepository,
            taskEventCache,
            artifactDownloadService,
            videoGenerationTaskService
        );
        taskId = UUID.randomUUID();
        task = new SmartEngineTask();
        task.setId(taskId);
        task.setUserId(UUID.randomUUID());
        task.setTraceId("trace-provenance");
        task.setServiceType(ServiceType.RESOURCE_GENERATION);
        task.setTaskStatus(TaskStatus.RUNNING);

        when(taskRepository.findWithLockById(taskId)).thenReturn(Optional.of(task));
    }

    @Test
    void resourceFileWithMissingProvenanceFailsBeforeDownloadSigning() {
        TaskStreamEventPayload result = service.recordPythonEvent(
            taskId,
            new PythonStreamEvent(
                "resource_file",
                "document_generation",
                Map.of(
                    "assetType", "DOCUMENT",
                    "title", "Generated guide",
                    "fileName", "guide.md",
                    "localPath", "/tmp/guide.md",
                    "mimeType", "text/markdown"
                )
            )
        );

        assertThat(result.event()).isEqualTo("error");
        assertThat(result.payload()).containsEntry("code", "PROVENANCE_INVALID");
        assertThat(result.payload()).containsEntry("sourceEvent", "resource_file");
        assertThat(task.getTaskStatus()).isEqualTo(TaskStatus.FAILED);
        verify(artifactDownloadService, never()).issueDownload(any(), any(), any(), any(), any(), any());
    }

    @Test
    void questionBatchWithMissingProvenanceFails() {
        TaskStreamEventPayload result = service.recordPythonEvent(
            taskId,
            new PythonStreamEvent(
                "question_batch",
                "practice",
                Map.of(
                    "title", "Practice",
                    "topic", "Index",
                    "difficulty", "BASIC",
                    "questions", List.of(Map.of("questionId", "q1", "stem", "Explain index usage"))
                )
            )
        );

        assertThat(result.event()).isEqualTo("error");
        assertThat(result.payload()).containsEntry("code", "PROVENANCE_INVALID");
        assertThat(result.payload()).containsEntry("sourceEvent", "question_batch");
        assertThat(task.getTaskStatus()).isEqualTo(TaskStatus.FAILED);
    }

    @Test
    void resourceFileWithMissingContentOriginFails() {
        TaskStreamEventPayload result = service.recordPythonEvent(
            taskId,
            new PythonStreamEvent(
                "resource_file",
                "document_generation",
                Map.ofEntries(
                    Map.entry("assetType", "DOCUMENT"),
                    Map.entry("title", "Generated guide"),
                    Map.entry("fileName", "guide.md"),
                    Map.entry("localPath", "/tmp/guide.md"),
                    Map.entry("mimeType", "text/markdown"),
                    Map.entry("generatedBy", "LLM"),
                    Map.entry("provider", "test-provider"),
                    Map.entry("model", "test-model"),
                    Map.entry("agentName", "document_generation"),
                    Map.entry("evidenceIds", List.of("doc-1")),
                    Map.entry("fallback", false),
                    Map.entry("fromCache", false)
                )
            )
        );

        assertThat(result.event()).isEqualTo("error");
        assertThat(result.payload()).containsEntry("code", "PROVENANCE_INVALID");
        assertThat(task.getTaskStatus()).isEqualTo(TaskStatus.FAILED);
        verify(artifactDownloadService, never()).issueDownload(any(), any(), any(), any(), any(), any());
    }

    @Test
    void videoSpeechPayloadWithMissingProvenanceFails() {
        task.setServiceType(ServiceType.VIDEO_GENERATION);

        TaskStreamEventPayload result = service.recordPythonEvent(
            taskId,
            new PythonStreamEvent(
                "video_gen:speech",
                "speech_synthesized",
                Map.of(
                    "stage", "speech_synthesized",
                    "percent", 50,
                    "audioBase64", "real-audio-bytes",
                    "scriptText", "LLM script should prove provenance"
                )
            )
        );

        assertThat(result.event()).isEqualTo("error");
        assertThat(result.payload()).containsEntry("code", "PROVENANCE_INVALID");
        assertThat(result.payload()).containsEntry("sourceEvent", "video_gen:speech");
        assertThat(task.getTaskStatus()).isEqualTo(TaskStatus.FAILED);
    }

    @Test
    void externalResourceLinkWithoutLlmProvenanceIsAllowed() {
        TaskStreamEventPayload result = service.recordPythonEvent(
            taskId,
            new PythonStreamEvent(
                "resource_file",
                "resource_push",
                Map.of(
                    "assetType", "READING",
                    "title", "External reference",
                    "summary", "External source",
                    "displayMode", "external_link",
                    "sourceName", "MIT OpenCourseWare",
                    "downloadUrl", "https://example.edu/course"
                )
            )
        );

        assertThat(result.event()).isEqualTo("resource_file");
        assertThat(task.getTaskStatus()).isEqualTo(TaskStatus.RUNNING);
        assertThat(task.getResponseSummary()).containsEntry("title", "External reference");
    }

    @Test
    void pendingSlideOutlineKeepsInlineContentWithoutDownloadSigning() {
        TaskStreamEventPayload result = service.recordPythonEvent(
            taskId,
            new PythonStreamEvent(
                "resource_file",
                "slides_outline",
                Map.of(
                    "assetType", "SLIDES",
                    "displayMode", "SLIDE_OUTLINE_CONFIRMATION",
                    "title", "联合索引 PPT 大纲",
                    "inlineContent", "# 联合索引 PPT 大纲",
                    "localPath", "/tmp/outline.md",
                    "sandboxPath", "/sandbox/outline.md"
                )
            )
        );

        assertThat(result.event()).isEqualTo("resource_file");
        assertThat(result.payload()).containsEntry("inlineContent", "# 联合索引 PPT 大纲");
        assertThat(result.payload()).doesNotContainKeys("downloadUrl", "localPath", "sandboxPath");
        assertThat(task.getTaskStatus()).isEqualTo(TaskStatus.RUNNING);
        assertThat(task.getResponseSummary()).containsEntry("inlineContent", "# 联合索引 PPT 大纲");
        verify(artifactDownloadService, never()).issueDownload(any(), any(), any(), any(), any(), any());
    }

    @Test
    void partialFailedDoneCompletesTaskWithPartialStage() {
        TaskStreamEventPayload result = service.recordPythonEvent(
            taskId,
            new PythonStreamEvent(
                "done",
                "resource_bundle",
                Map.of(
                    "status", "PARTIAL_FAILED",
                    "summary", "4 resources generated, 1 failed",
                    "resourceFailures", List.of(Map.of("resourceType", "SLIDES", "error", "llm unavailable"))
                )
            )
        );

        assertThat(result.event()).isEqualTo("done");
        assertThat(task.getTaskStatus()).isEqualTo(TaskStatus.COMPLETED);
        assertThat(task.getCurrentStage()).isEqualTo("partial_failed");
        assertThat(task.getResponseSummary()).containsEntry("status", "PARTIAL_FAILED");
    }

    @Test
    void waitingConfirmationDoneKeepsTaskRunningWithoutFakeCompletion() {
        task.setProgressPercent(new java.math.BigDecimal("42"));

        TaskStreamEventPayload result = service.recordPythonEvent(
            taskId,
            new PythonStreamEvent(
                "done",
                "resource_bundle",
                Map.of(
                    "status", "WAITING_CONFIRMATION",
                    "summary", "PPT 大纲已生成，等待确认",
                    "pendingSlideOutlines", List.of(Map.of("title", "SQL 基础 PPT 大纲"))
                )
            )
        );

        assertThat(result.event()).isEqualTo("done");
        assertThat(task.getTaskStatus()).isEqualTo(TaskStatus.RUNNING);
        assertThat(task.getCurrentStage()).isEqualTo("waiting_confirmation");
        assertThat(task.getProgressPercent()).isEqualByComparingTo("42");
        assertThat(task.getCompletedAt()).isNull();
        assertThat(task.getResponseSummary()).containsEntry("status", "WAITING_CONFIRMATION");
    }

    @Test
    void progressEventDoesNotMoveBackwardOrPublishLowerPercent() {
        task.setProgressPercent(new BigDecimal("90"));

        TaskStreamEventPayload result = service.recordPythonEvent(
            taskId,
            new PythonStreamEvent(
                "progress",
                "learning_path",
                Map.of(
                    "stage", "learning_path",
                    "percent", 45
                )
            )
        );

        assertThat(result.event()).isEqualTo("progress");
        assertThat(task.getTaskStatus()).isEqualTo(TaskStatus.RUNNING);
        assertThat(task.getProgressPercent()).isEqualByComparingTo("90");
        assertThat((Number) result.payload().get("percent")).isEqualTo(90);
        assertThat(taskEventCache.replay(taskId)).hasSize(1);
        assertThat((Number) taskEventCache.replay(taskId).get(0).payload().get("percent")).isEqualTo(90);
    }

    @Test
    void videoProgressEventDoesNotPublishLowerPercent() {
        task.setProgressPercent(new BigDecimal("60"));

        TaskStreamEventPayload startResult = service.recordPythonEvent(
            taskId,
            new PythonStreamEvent(
                "video_gen:start",
                "video_started",
                Map.of(
                    "assetType", "VIDEO",
                    "stage", "video_started",
                    "percent", 10
                )
            )
        );

        assertThat(startResult.event()).isEqualTo("video_gen:start");
        assertThat(task.getProgressPercent()).isEqualByComparingTo("60");
        assertThat((Number) startResult.payload().get("percent")).isEqualTo(60);
        assertThat(taskEventCache.replay(taskId).get(0).event()).isEqualTo("video_gen:start");
        assertThat((Number) taskEventCache.replay(taskId).get(0).payload().get("percent")).isEqualTo(60);

        TaskStreamEventPayload speechResult = service.recordPythonEvent(
            taskId,
            new PythonStreamEvent(
                "video_gen:speech",
                "speech_synthesized",
                Map.ofEntries(
                    Map.entry("assetType", "VIDEO"),
                    Map.entry("stage", "speech_synthesized"),
                    Map.entry("percent", 85),
                    Map.entry("generatedBy", "LLM"),
                    Map.entry("contentOrigin", "LLM"),
                    Map.entry("provider", "test-provider"),
                    Map.entry("model", "test-model"),
                    Map.entry("agentName", "video_generation"),
                    Map.entry("evidenceIds", List.of("doc-1")),
                    Map.entry("fallback", false),
                    Map.entry("fromCache", false)
                )
            )
        );

        assertThat(speechResult.event()).isEqualTo("video_gen:speech");
        assertThat(task.getProgressPercent()).isEqualByComparingTo("85");
        assertThat((Number) speechResult.payload().get("percent")).isEqualTo(85);
    }

    @Test
    void duplicateTerminalSeqStillFailsActiveTaskAtNextSequence() {
        taskEventCache.append(new TaskStreamEventPayload(
            "progress",
            taskId,
            task.getTraceId(),
            8,
            java.time.OffsetDateTime.now(),
            Map.of("stage", "practice", "percent", 35)
        ));

        TaskEventRecordResult result = service.recordPythonEvent(
            taskId,
            new PythonStreamEvent(
                "error",
                null,
                Map.of(
                    "code", "RESOURCE_BUNDLE_FAILED",
                    "message", "Practice question LLM generation failed; template fallback is not allowed"
                )
            ),
            8
        );

        assertThat(result.created()).isTrue();
        assertThat(result.payload().event()).isEqualTo("error");
        assertThat(result.payload().seq()).isEqualTo(9);
        assertThat(result.payload().payload()).containsEntry("code", "RESOURCE_BUNDLE_FAILED");
        assertThat(task.getTaskStatus()).isEqualTo(TaskStatus.FAILED);
        assertThat(task.getErrorCode()).isEqualTo("RESOURCE_BUNDLE_FAILED");
    }
}
