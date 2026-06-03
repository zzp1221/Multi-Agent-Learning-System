package com.project.application.voice;

import java.util.function.Consumer;

public interface VoiceTtsClient {

    void synthesize(String text, String voice, Consumer<VoiceTtsChunk> chunkConsumer);
}
