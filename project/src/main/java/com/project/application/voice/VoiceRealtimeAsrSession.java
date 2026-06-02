package com.project.application.voice;

public interface VoiceRealtimeAsrSession extends AutoCloseable {

    void appendAudio(byte[] pcmAudio);

    void commit();

    void cancel();

    @Override
    void close();
}
