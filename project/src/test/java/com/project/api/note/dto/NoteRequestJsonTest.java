package com.project.api.note.dto;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class NoteRequestJsonTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void createNoteAcceptsContentAsMarkdownContentAlias() throws Exception {
        CreateNoteRequest request = objectMapper.readValue(
            """
            {
              "title": "Alias Probe",
              "content": "# Alias Probe\\nbody"
            }
            """,
            CreateNoteRequest.class
        );

        assertThat(request.markdownContent()).isEqualTo("# Alias Probe\nbody");
    }

    @Test
    void updateNoteAcceptsContentAsMarkdownContentAlias() throws Exception {
        UpdateNoteRequest request = objectMapper.readValue(
            """
            {
              "content": "updated body"
            }
            """,
            UpdateNoteRequest.class
        );

        assertThat(request.markdownContent()).isEqualTo("updated body");
    }
}
