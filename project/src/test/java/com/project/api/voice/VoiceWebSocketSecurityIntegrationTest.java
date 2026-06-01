package com.project.api.voice;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class VoiceWebSocketSecurityIntegrationTest {

    @Autowired
    private MockMvc mockMvc;

    @Test
    void voiceWebSocketHandshakePathIsNotRejectedBySpringSecurity() throws Exception {
        int status = mockMvc.perform(get("/api/voice/ws"))
            .andReturn()
            .getResponse()
            .getStatus();

        assertThat(status).isNotEqualTo(401);
    }
}
