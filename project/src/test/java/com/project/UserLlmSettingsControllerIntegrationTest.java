package com.project;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class UserLlmSettingsControllerIntegrationTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Test
    void saveSettingsRejectsOversizedNestedProviderSecret() throws Exception {
        MvcResult registerResult = mockMvc.perform(post("/api/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {
                      "loginId": "settings_%s",
                      "password": "Password123",
                      "fullName": "Settings User",
                      "majorCode": "CS"
                    }
                    """.formatted(System.nanoTime())))
            .andExpect(status().isOk())
            .andReturn();

        mockMvc.perform(put("/api/settings/llm")
                .header("Authorization", "Bearer " + readToken(registerResult))
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {
                      "enabled": true,
                      "activeProvider": "openai",
                      "fallbackProvider": "",
                      "providers": {
                        "openai": {
                          "provider": "openai",
                          "baseUrl": "https://api.openai.com/v1",
                          "apiKey": "%s",
                          "apiSecret": "",
                          "appId": "",
                          "modelOverrides": {}
                        }
                      },
                      "componentOverrides": {},
                      "skillOverrides": {}
                    }
                    """.formatted("k".repeat(2100))))
            .andExpect(status().isBadRequest())
            .andExpect(jsonPath("$.code").value("INVALID_ARGUMENT"));
    }

    private String readToken(MvcResult result) throws Exception {
        JsonNode json = objectMapper.readTree(result.getResponse().getContentAsString());
        return json.path("token").asText();
    }
}
