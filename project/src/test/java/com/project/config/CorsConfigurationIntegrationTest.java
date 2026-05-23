package com.project.config;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpHeaders;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.filter.CorsFilter;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.options;
import static org.springframework.test.web.servlet.setup.MockMvcBuilders.standaloneSetup;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

class CorsConfigurationIntegrationTest {

    private final MockMvc mockMvc = standaloneSetup(new TestController())
        .addFilters(new CorsFilter(new CorsConfiguration().corsConfigurationSource(new AppProperties())))
        .build();

    @Test
    void patchPreflightAllowsMistakeUpdateHeaders() throws Exception {
        MvcResult result = mockMvc.perform(options("/api/mistakes/00000000-0000-0000-0000-000000000000")
                .header(HttpHeaders.ORIGIN, "http://localhost:5173")
                .header(HttpHeaders.ACCESS_CONTROL_REQUEST_METHOD, "PATCH")
                .header(HttpHeaders.ACCESS_CONTROL_REQUEST_HEADERS, "Authorization, Content-Type"))
            .andExpect(status().isOk())
            .andReturn();

        assertThat(result.getResponse().getHeader(HttpHeaders.ACCESS_CONTROL_ALLOW_METHODS))
            .contains("PATCH");
        assertThat(result.getResponse().getHeader(HttpHeaders.ACCESS_CONTROL_ALLOW_HEADERS))
            .containsIgnoringCase("Authorization")
            .containsIgnoringCase("Content-Type")
            .doesNotContainIgnoringCase("X-User-Id");
    }

    @RestController
    private static class TestController {

        @PatchMapping("/api/mistakes/{id}")
        void updateMistake() {
        }
    }
}
