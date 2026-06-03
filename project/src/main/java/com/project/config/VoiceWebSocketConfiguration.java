package com.project.config;

import com.project.api.voice.VoiceRealtimeWebSocketHandler;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.socket.config.annotation.EnableWebSocket;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.config.annotation.WebSocketHandlerRegistry;

@Configuration
@EnableWebSocket
public class VoiceWebSocketConfiguration implements WebSocketConfigurer {

    private final VoiceRealtimeWebSocketHandler voiceRealtimeWebSocketHandler;

    public VoiceWebSocketConfiguration(VoiceRealtimeWebSocketHandler voiceRealtimeWebSocketHandler) {
        this.voiceRealtimeWebSocketHandler = voiceRealtimeWebSocketHandler;
    }

    @Override
    public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
        registry.addHandler(voiceRealtimeWebSocketHandler, "/api/voice/ws")
            .setAllowedOriginPatterns("*");
    }
}
