package com.project.application.voice;

public interface VoiceRealtimeAsrClient {

    VoiceRealtimeAsrSession start(
        String sessionKey,
        int sampleRate,
        VoiceRealtimeAsrListener listener
    );
}
