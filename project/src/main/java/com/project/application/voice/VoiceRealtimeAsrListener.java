package com.project.application.voice;

public interface VoiceRealtimeAsrListener {

    void onReady();

    void onPartial(String text);

    void onFinal(String text);

    void onError(Throwable error);
}
