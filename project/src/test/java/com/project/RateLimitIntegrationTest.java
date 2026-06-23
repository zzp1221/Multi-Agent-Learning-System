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

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Verifies rate limiting behavior with a constrained test configuration.
 */
@SpringBootTest(properties = {
    "app.rate-limit.user-requests-per-minute=1",
    "app.rate-limit.ip-requests-per-minute=100",
    "app.rate-limit.login-requests-per-minute=1",
    "app.rate-limit.register-requests-per-hour=100"
})
@AutoConfigureMockMvc
@ActiveProfiles("test")
class RateLimitIntegrationTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Test
    void authenticatedRequestsAreRateLimited() throws Exception {
        String token = registerAndGetToken("limit_" + System.nanoTime());

        mockMvc.perform(get("/api/auth/me")
                .header("Authorization", "Bearer " + token))
            .andExpect(status().isOk());

        mockMvc.perform(get("/api/auth/me")
                .header("Authorization", "Bearer " + token))
            .andExpect(status().isTooManyRequests())
            .andExpect(jsonPath("$.code").value("TOO_MANY_REQUESTS"));
    }

    @Test
    void loginRequestsAreRateLimitedBeforeAuthentication() throws Exception {
        String loginId = "login_limit_" + System.nanoTime();
        registerAndGetToken(loginId);

        mockMvc.perform(post("/api/auth/login")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {
                      "loginId": "%s",
                      "password": "wrong-password"
                    }
                    """.formatted(loginId)))
            .andExpect(status().isUnauthorized());

        mockMvc.perform(post("/api/auth/login")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {
                      "loginId": "%s",
                      "password": "wrong-password"
                    }
                    """.formatted(loginId)))
            .andExpect(status().isTooManyRequests())
            .andExpect(jsonPath("$.code").value("TOO_MANY_REQUESTS"));
    }

    private String registerAndGetToken(String loginId) throws Exception {
        MvcResult result = mockMvc.perform(post("/api/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {
                      "loginId": "%s",
                      "password": "Password123",
                      "fullName": "限流用户",
                      "majorCode": "CS"
                    }
                    """.formatted(loginId)))
            .andExpect(status().isOk())
            .andReturn();

        JsonNode jsonNode = objectMapper.readTree(result.getResponse().getContentAsString());
        return jsonNode.path("token").asText();
    }
}
