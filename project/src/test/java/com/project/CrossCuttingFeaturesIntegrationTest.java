package com.project;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.application.smartengine.SmartEngineQueueService;
import com.project.domain.audit.AuditLogRepository;
import com.project.security.InternalTokenVerifier;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.request;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Integration tests for idempotency, audit logging, and artifact download signing.
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class CrossCuttingFeaturesIntegrationTest {

    private static final Pattern DOWNLOAD_URL_PATTERN = Pattern.compile("\"downloadUrl\":\"([^\"]+)\"");
    private static final String INTERNAL_TOKEN = "test-internal-token";

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private AuditLogRepository auditLogRepository;

    @MockBean
    private SmartEngineQueueService smartEngineQueueService;

    @Test
    void submitSupportsIdempotencyAndSignedArtifactDownload() throws Exception {
        Path tempFile = Files.createTempFile("zhixue-artifact-", ".md");
        Files.writeString(tempFile, "# database index guide", StandardCharsets.UTF_8);
        when(smartEngineQueueService.enqueue(any())).thenReturn("0-1");

        AuthContext auth = register("cross_" + System.nanoTime());
        String idempotencyKey = "idem-" + UUID.randomUUID();

        MvcResult firstSubmit = mockMvc.perform(post("/api/smart-engine/submit")
                .header("Authorization", "Bearer " + auth.token())
                .header("Idempotency-Key", idempotencyKey)
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {
                      "conversationId": "%s",
                      "serviceType": "RESOURCE_GENERATION",
                      "params": {
                        "resourceType": "DOCUMENT"
                      }
                    }
                    """.formatted(UUID.randomUUID())))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.taskId").isNotEmpty())
            .andReturn();

        String taskId = readField(firstSubmit, "taskId");

        mockMvc.perform(post("/api/smart-engine/submit")
                .header("Authorization", "Bearer " + auth.token())
                .header("Idempotency-Key", idempotencyKey)
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {
                      "conversationId": "%s",
                      "serviceType": "RESOURCE_GENERATION",
                      "params": {
                        "resourceType": "DOCUMENT"
                      }
                    }
                    """.formatted(UUID.randomUUID())))
            .andExpect(status().isConflict())
            .andExpect(jsonPath("$.code").value("IDEMPOTENT_REPLAY"))
            .andExpect(jsonPath("$.taskId").value(taskId));

        recordWorkerEvent(taskId, 1, "resource_file", "generating", Map.ofEntries(
            Map.entry("assetType", "DOCUMENT"),
            Map.entry("title", "database index guide"),
            Map.entry("fileName", tempFile.getFileName().toString()),
            Map.entry("localPath", tempFile.toString()),
            Map.entry("mimeType", "text/markdown"),
            Map.entry("generatedBy", "LLM"),
            Map.entry("contentOrigin", "LLM"),
            Map.entry("provider", "test-provider"),
            Map.entry("model", "test-model"),
            Map.entry("agentName", "document_generation"),
            Map.entry("evidenceIds", List.of("doc-1")),
            Map.entry("fallback", false),
            Map.entry("fromCache", false)
        ));
        recordWorkerEvent(taskId, 2, "done", "completed", Map.of("summary", "resource ready"));

        awaitTaskCompletion(auth.token(), taskId);

        MvcResult streamResult = mockMvc.perform(get("/api/smart-engine/tasks/" + taskId + "/stream")
                .header("Authorization", "Bearer " + auth.token()))
            .andExpect(request().asyncStarted())
            .andReturn();

        String streamBody = streamResult.getResponse().getContentAsString(StandardCharsets.UTF_8);
        Matcher matcher = DOWNLOAD_URL_PATTERN.matcher(streamBody);
        assertThat(matcher.find()).isTrue();
        String downloadUrl = matcher.group(1);

        MvcResult downloadResult = mockMvc.perform(get(downloadUrl)
                .header("Authorization", "Bearer " + auth.token()))
            .andExpect(status().isOk())
            .andReturn();

        assertThat(downloadResult.getResponse().getContentAsString(StandardCharsets.UTF_8))
            .contains("database index guide");
        assertThat(auditLogRepository.findAll()).extracting("eventCategory")
            .contains("AUTH", "TASK", "DOWNLOAD");
    }

    private void awaitTaskCompletion(String token, String taskId) throws Exception {
        for (int attempt = 0; attempt < 20; attempt++) {
            MvcResult statusResult = mockMvc.perform(get("/api/smart-engine/tasks/" + taskId)
                    .header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andReturn();
            if ("COMPLETED".equals(readField(statusResult, "status"))) {
                return;
            }
            Thread.sleep(100);
        }
        throw new AssertionError("Task did not complete in time");
    }

    private void recordWorkerEvent(
        String taskId,
        int seq,
        String eventType,
        String stage,
        Map<String, Object> payload
    ) throws Exception {
        mockMvc.perform(post("/internal/smart-engine/tasks/" + taskId + "/events")
                .header(InternalTokenVerifier.INTERNAL_TOKEN_HEADER, INTERNAL_TOKEN)
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(Map.of(
                    "eventType", eventType,
                    "stage", stage,
                    "seq", seq,
                    "payload", payload
                ))))
            .andExpect(status().isOk());
    }

    private AuthContext register(String loginId) throws Exception {
        MvcResult registerResult = mockMvc.perform(post("/api/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {
                      "loginId": "%s",
                      "password": "Password123",
                      "fullName": "Test User",
                      "majorCode": "CS"
                    }
                    """.formatted(loginId)))
            .andExpect(status().isOk())
            .andReturn();

        JsonNode jsonNode = objectMapper.readTree(registerResult.getResponse().getContentAsString());
        return new AuthContext(jsonNode.path("token").asText());
    }

    private String readField(MvcResult result, String fieldName) throws Exception {
        return objectMapper.readTree(result.getResponse().getContentAsString()).path(fieldName).asText();
    }

    private record AuthContext(String token) {
    }
}
