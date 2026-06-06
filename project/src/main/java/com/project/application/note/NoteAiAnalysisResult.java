package com.project.application.note;

import java.util.List;

public record NoteAiAnalysisResult(
    String summary,
    List<String> keywords,
    List<NoteTodoResult> todos,
    String provider,
    String model
) {
}
