package com.project;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.application.smartengine.PythonAgentClient;
import com.project.application.smartengine.SmartEngineInvocation;
import com.project.application.smartengine.PythonStreamEvent;
import com.project.application.conversation.PythonConversationMessageClient;
import com.project.domain.profile.UserProfileCurrent;
import com.project.domain.profile.UserProfileCurrentRepository;
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
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doAnswer;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.asyncDispatch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.request;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Integration tests covering conversation metadata and current-profile queries.
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class ConversationAndProfileIntegrationTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private UserProfileCurrentRepository userProfileCurrentRepository;

    @MockBean
    private PythonAgentClient pythonAgentClient;

    @MockBean
    private PythonConversationMessageClient pythonConversationMessageClient;

    @Test
    void createConversationAndStreamMessage() throws Exception {
        doAnswer(invocation -> List.of()).when(pythonConversationMessageClient).listMessages(any(), any());
        doAnswer(invocation -> null).when(pythonConversationMessageClient).appendMessage(any(), any(), any(), any(), any());
        doAnswer(invocation -> {
            SmartEngineInvocation agentInvocation = invocation.getArgument(0, SmartEngineInvocation.class);
            assertThat(agentInvocation.params()).containsEntry("webSearchEnabled", true);
            assertThat(agentInvocation.params()).containsKey("voiceContext");
            @SuppressWarnings("unchecked")
            Map<String, String> voiceContext = (Map<String, String>) agentInvocation.params().get("voiceContext");
            assertThat(voiceContext).containsEntry("pageType", "qna_chat");
            @SuppressWarnings("unchecked")
            Consumer<PythonStreamEvent> consumer = invocation.getArgument(1, Consumer.class);
            consumer.accept(new PythonStreamEvent(
                "result_chunk",
                "explaining",
                Map.of(
                    "text", "先理解联合索引的最左前缀原则",
                    "pedagogyStrategy", "EXPLAIN",
                    "nextAction", "WAIT_USER"
                )
            ));
            consumer.accept(new PythonStreamEvent("done", "completed", Map.of("message", "讲解结束")));
            return null;
        }).when(pythonAgentClient).stream(any(), any());

        AuthContext authContext = register("conv_" + System.nanoTime());

        MvcResult createConversationResult = mockMvc.perform(post("/api/conversations")
                .header("Authorization", "Bearer " + authContext.token()))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.conversationId").isNotEmpty())
            .andReturn();

        String conversationId = readField(createConversationResult, "conversationId");

        MvcResult streamResult = mockMvc.perform(post("/api/conversations/" + conversationId + "/messages/stream")
                .header("Authorization", "Bearer " + authContext.token())
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {
                      "message": "请给我讲一下数据库联合索引",
                      "serviceType": "TUTORING",
                      "webSearchEnabled": true,
                      "voiceContext": {
                        "pageType": "qna_chat",
                        "pageTitle": "智能对话"
                      }
                    }
                    """))
            .andExpect(request().asyncStarted())
            .andReturn();

        streamResult.getAsyncResult(1000);
        MvcResult asyncResult = mockMvc.perform(asyncDispatch(streamResult))
            .andExpect(status().isOk())
            .andReturn();

        String responseBody = asyncResult.getResponse().getContentAsString(StandardCharsets.UTF_8);
        assertThat(responseBody).contains("event:result_chunk");
        assertThat(responseBody).contains("event:done");
        assertThat(responseBody).contains(conversationId);
        assertThat(responseBody).contains("联合索引");
    }

    @Test
    void currentProfileCanBeQueriedByOwner() throws Exception {
        AuthContext authContext = register("profile_" + System.nanoTime());

        UserProfileCurrent profileCurrent = new UserProfileCurrent();
        profileCurrent.setUserId(UUID.fromString(authContext.userId()));
        profileCurrent.setProfileJson(Map.of(
            "knowledgeBase", "INTERMEDIATE",
            "learningGoal", "掌握数据库索引优化",
            "preference", "图文结合"
        ));
        profileCurrent.setSummaryText("数据库原理基础中等，偏好图文结合讲解");
        profileCurrent.setUpdatedAt(OffsetDateTime.now());
        userProfileCurrentRepository.save(profileCurrent);

        mockMvc.perform(get("/api/users/" + authContext.userId() + "/profile/current")
                .header("Authorization", "Bearer " + authContext.token()))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.userId").value(authContext.userId()))
            .andExpect(jsonPath("$.profile.knowledgeBase").value("INTERMEDIATE"))
            .andExpect(jsonPath("$.summary").value("数据库原理基础中等，偏好图文结合讲解"));
    }

    private AuthContext register(String loginId) throws Exception {
        MvcResult registerResult = mockMvc.perform(post("/api/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {
                      "loginId": "%s",
                      "password": "Password123",
                      "fullName": "测试同学",
                      "majorCode": "CS"
                    }
                    """.formatted(loginId)))
            .andExpect(status().isOk())
            .andReturn();

        JsonNode jsonNode = objectMapper.readTree(registerResult.getResponse().getContentAsString());
        return new AuthContext(
            jsonNode.path("token").asText(),
            jsonNode.path("user").path("userId").asText()
        );
    }

    private String readField(MvcResult result, String fieldName) throws Exception {
        JsonNode jsonNode = objectMapper.readTree(result.getResponse().getContentAsString());
        return jsonNode.path(fieldName).asText();
    }

    private record AuthContext(String token, String userId) {
    }
}
