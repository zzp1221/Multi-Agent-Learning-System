package com.project.api.note;

import com.project.api.common.GlobalExceptionHandler;
import com.project.api.note.dto.NoteSemanticSearchResponse;
import com.project.application.note.NoteService;
import com.project.security.JwtAuthenticatedUser;
import org.junit.jupiter.api.Test;
import org.springframework.security.core.Authentication;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;
import java.util.UUID;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;
import static org.springframework.test.web.servlet.setup.MockMvcBuilders.standaloneSetup;

class NoteControllerTest {

    private final JwtAuthenticatedUser user = new JwtAuthenticatedUser(
        UUID.fromString("60000000-0000-0000-0000-000000000201"),
        "notebook-user",
        "USER"
    );

    @Test
    void semanticSearchAcceptsQAlias() throws Exception {
        NoteService noteService = mock(NoteService.class);
        when(noteService.semanticSearch(user.userId(), "Python", 5))
            .thenReturn(new NoteSemanticSearchResponse("Python", true, "", List.of()));
        MockMvc mockMvc = mockMvc(noteService);

        mockMvc.perform(get("/api/notes/search/semantic")
                .principal(auth())
                .param("q", "Python")
                .param("topK", "5"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.query").value("Python"));

        verify(noteService).semanticSearch(user.userId(), "Python", 5);
    }

    @Test
    void semanticSearchRejectsBlankQuery() throws Exception {
        MockMvc mockMvc = mockMvc(mock(NoteService.class));

        mockMvc.perform(get("/api/notes/search/semantic")
                .principal(auth())
                .param("q", " "))
            .andExpect(status().isBadRequest())
            .andExpect(jsonPath("$.code").value("INVALID_ARGUMENT"));
    }

    private MockMvc mockMvc(NoteService noteService) {
        return standaloneSetup(new NoteController(noteService))
            .setControllerAdvice(new GlobalExceptionHandler())
            .build();
    }

    private Authentication auth() {
        Authentication authentication = mock(Authentication.class);
        when(authentication.getPrincipal()).thenReturn(user);
        return authentication;
    }
}
