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

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Integration tests for the authentication API contract.
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class AuthControllerIntegrationTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Test
    void registerReturnsTokenAndCurrentUserCanBeLoaded() throws Exception {
        String loginId = "user_" + System.nanoTime();
        String payload = """
            {
              "loginId": "%s",
              "password": "Password123",
              "fullName": "张三",
              "majorCode": "CS"
            }
            """.formatted(loginId);

        MvcResult registerResult = mockMvc.perform(post("/api/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content(payload))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.token").isNotEmpty())
            .andExpect(jsonPath("$.user.loginId").value(loginId))
            .andReturn();

        String token = readToken(registerResult);

        mockMvc.perform(get("/api/auth/me")
                .header("Authorization", "Bearer " + token))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.loginId").value(loginId))
            .andExpect(jsonPath("$.fullName").value("张三"))
            .andExpect(jsonPath("$.majorCode").value("CS"));
    }

    @Test
    void loginReturnsNewTokenForExistingUser() throws Exception {
        String loginId = "user_" + System.nanoTime();
        String registerPayload = """
            {
              "loginId": "%s",
              "password": "Password123",
              "fullName": "李四",
              "majorCode": "SE"
            }
            """.formatted(loginId);

        mockMvc.perform(post("/api/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content(registerPayload))
            .andExpect(status().isOk());

        MvcResult loginResult = mockMvc.perform(post("/api/auth/login")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {
                      "loginId": "%s",
                      "password": "Password123"
                    }
                    """.formatted(loginId)))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.token").isNotEmpty())
            .andExpect(jsonPath("$.user.fullName").value("李四"))
            .andReturn();

        assertThat(readToken(loginResult)).isNotBlank();
    }

    @Test
    void repeatedLoginIssuesDistinctTokensSoLogoutDoesNotRevokeFreshLogin() throws Exception {
        String loginId = "user_" + System.nanoTime();
        String registerPayload = """
            {
              "loginId": "%s",
              "password": "Password123",
              "fullName": "Distinct Token User",
              "majorCode": "SE"
            }
            """.formatted(loginId);

        mockMvc.perform(post("/api/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content(registerPayload))
            .andExpect(status().isOk());

        String firstToken = readToken(login(loginId));
        String secondToken = readToken(login(loginId));
        assertThat(secondToken).isNotEqualTo(firstToken);

        mockMvc.perform(post("/api/auth/logout")
                .header("Authorization", "Bearer " + firstToken))
            .andExpect(status().isOk());

        mockMvc.perform(get("/api/auth/me")
                .header("Authorization", "Bearer " + secondToken))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.loginId").value(loginId));
    }

    @Test
    void meWithoutTokenReturnsAuthRequired() throws Exception {
        mockMvc.perform(get("/api/auth/me"))
            .andExpect(status().isUnauthorized())
            .andExpect(jsonPath("$.code").value("AUTH_REQUIRED"))
            .andExpect(jsonPath("$.message").value("请先登录"));
    }

    @Test
    void loginWithUnknownUserAndShortPasswordReturnsUnauthorizedInsteadOfValidationError() throws Exception {
        mockMvc.perform(post("/api/auth/login")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {
                      "loginId": "missing_user",
                      "password": "x"
                    }
                    """))
            .andExpect(status().isUnauthorized())
            .andExpect(jsonPath("$.code").value("INVALID_CREDENTIALS"))
            .andExpect(jsonPath("$.message").value("账号或密码错误"));
    }

    @Test
    void duplicateRegisterReturnsGenericRejection() throws Exception {
        String loginId = "user_" + System.nanoTime();
        String payload = """
            {
              "loginId": "%s",
              "password": "Password123",
              "fullName": "Duplicate User",
              "majorCode": "CS"
            }
            """.formatted(loginId);

        mockMvc.perform(post("/api/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content(payload))
            .andExpect(status().isOk());

        mockMvc.perform(post("/api/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content(payload))
            .andExpect(status().isBadRequest())
            .andExpect(jsonPath("$.code").value("REGISTRATION_REJECTED"))
            .andExpect(jsonPath("$.message").value("注册信息无法使用"));
    }

    @Test
    void logoutRevokesCurrentToken() throws Exception {
        String loginId = "user_" + System.nanoTime();
        MvcResult registerResult = mockMvc.perform(post("/api/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {
                      "loginId": "%s",
                      "password": "Password123",
                      "fullName": "王五",
                      "majorCode": "AI"
                    }
                    """.formatted(loginId)))
            .andExpect(status().isOk())
            .andReturn();
        String token = readToken(registerResult);

        mockMvc.perform(post("/api/auth/logout")
                .header("Authorization", "Bearer " + token))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.code").value("SUCCESS"))
            .andExpect(jsonPath("$.message").value("退出成功"));

        mockMvc.perform(get("/api/auth/me")
                .header("Authorization", "Bearer " + token))
            .andExpect(status().isUnauthorized())
            .andExpect(jsonPath("$.code").value("AUTH_REQUIRED"));
    }

    @Test
    void unsupportedAuthenticatedMethodReturnsMethodNotAllowed() throws Exception {
        String loginId = "user_" + System.nanoTime();
        MvcResult registerResult = mockMvc.perform(post("/api/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {
                      "loginId": "%s",
                      "password": "Password123",
                      "fullName": "Test User",
                      "majorCode": "AI"
                    }
                    """.formatted(loginId)))
            .andExpect(status().isOk())
            .andReturn();

        mockMvc.perform(post("/api/auth/me")
                .header("Authorization", "Bearer " + readToken(registerResult)))
            .andExpect(status().isMethodNotAllowed())
            .andExpect(jsonPath("$.code").value("METHOD_NOT_ALLOWED"));
    }

    private String readToken(MvcResult result) throws Exception {
        JsonNode json = objectMapper.readTree(result.getResponse().getContentAsString());
        return json.path("token").asText();
    }

    private MvcResult login(String loginId) throws Exception {
        return mockMvc.perform(post("/api/auth/login")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {
                      "loginId": "%s",
                      "password": "Password123"
                    }
                    """.formatted(loginId)))
            .andExpect(status().isOk())
            .andReturn();
    }
}
