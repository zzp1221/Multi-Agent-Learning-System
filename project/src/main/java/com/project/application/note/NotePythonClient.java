package com.project.application.note;

import java.util.UUID;

public interface NotePythonClient {
    NoteAiAnalysisResult analyze(String title, String markdownContent, String plainText);

    NoteRagIndexResult index(NoteRagIndexRequest request);

    NoteSemanticSearchResult semanticSearch(UUID userId, String query, int topK);
}
