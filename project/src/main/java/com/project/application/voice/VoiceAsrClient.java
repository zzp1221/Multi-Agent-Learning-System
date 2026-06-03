package com.project.application.voice;

public interface VoiceAsrClient {

    VoiceAsrResult transcribePcm16(byte[] pcmAudio, int sampleRate);
}
