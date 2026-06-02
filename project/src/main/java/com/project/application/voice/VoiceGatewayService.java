package com.project.application.voice;

import com.project.api.voice.dto.VoiceCommandRequest;
import com.project.api.voice.dto.VoiceCommandResponse;
import com.project.api.voice.dto.VoiceSessionResponse;
import com.project.api.voice.dto.VoiceTranscribeResponse;
import com.project.application.common.ApplicationException;
import com.project.config.AppProperties;
import com.project.security.JwtAuthenticatedUser;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.util.UUID;

@Service
public class VoiceGatewayService {

    private final AppProperties appProperties;
    private final VoiceAsrClient asrClient;
    private final VoiceSessionService sessionService;
    private final VoiceCommandParser commandParser;

    public VoiceGatewayService(
        AppProperties appProperties,
        VoiceAsrClient asrClient,
        VoiceSessionService sessionService,
        VoiceCommandParser commandParser
    ) {
        this.appProperties = appProperties;
        this.asrClient = asrClient;
        this.sessionService = sessionService;
        this.commandParser = commandParser;
    }

    public VoiceTranscribeResponse transcribe(JwtAuthenticatedUser currentUser, MultipartFile file) {
        ensureEnabled();
        if (file == null || file.isEmpty()) {
            throw new ApplicationException("VOICE_AUDIO_EMPTY", "请先录制语音", HttpStatus.BAD_REQUEST);
        }
        if (file.getSize() > appProperties.getVoice().getMaxAudioBytes()) {
            throw new ApplicationException("VOICE_AUDIO_TOO_LARGE", "语音文件超过大小限制", HttpStatus.BAD_REQUEST);
        }
        if (!isSupportedPcm(file.getContentType())) {
            throw new ApplicationException("VOICE_AUDIO_FORMAT_UNSUPPORTED", "仅支持 16k 单声道 PCM 音频", HttpStatus.BAD_REQUEST);
        }
        byte[] audio = readAudio(file);
        validatePcmDuration(audio);
        VoiceAsrResult result = asrClient.transcribePcm16(audio, appProperties.getVoice().getSampleRate());
        return new VoiceTranscribeResponse(result.text(), result.durationMs(), result.provider(), result.model());
    }

    public VoiceSessionResponse createSession(JwtAuthenticatedUser currentUser) {
        ensureEnabled();
        VoiceSessionState session = sessionService.create(currentUser);
        return new VoiceSessionResponse(
            session.sessionId(),
            session.expiresAt(),
            appProperties.getVoice().getSampleRate(),
            appProperties.getVoice().getProvider(),
            appProperties.getVoice().getAsrModel(),
            appProperties.getVoice().getTtsModel()
        );
    }

    public VoiceCommandResponse parseCommand(JwtAuthenticatedUser currentUser, VoiceCommandRequest request) {
        ensureEnabled();
        return commandParser.parse(request);
    }

    public void ensureSessionOwnedBy(UUID sessionId, JwtAuthenticatedUser currentUser) {
        ensureEnabled();
        if (sessionId == null || currentUser == null || !sessionService.isOwnedBy(sessionId, currentUser.userId())) {
            throw new ApplicationException("VOICE_SESSION_NOT_FOUND", "语音会话不存在或已过期", HttpStatus.NOT_FOUND);
        }
    }

    private void ensureEnabled() {
        if (!appProperties.getVoice().isEnabled()) {
            throw new ApplicationException("VOICE_DISABLED", "语音助手未启用", HttpStatus.SERVICE_UNAVAILABLE);
        }
    }

    private boolean isSupportedPcm(String contentType) {
        if (contentType == null || contentType.isBlank()) {
            return true;
        }
        String normalized = contentType.toLowerCase();
        return normalized.contains("audio/pcm")
            || normalized.contains("audio/l16")
            || normalized.contains("application/octet-stream");
    }

    private byte[] readAudio(MultipartFile file) {
        try {
            return file.getBytes();
        } catch (IOException ex) {
            throw new ApplicationException("VOICE_AUDIO_READ_FAILED", "语音读取失败，请重试", HttpStatus.BAD_REQUEST);
        }
    }

    private void validatePcmDuration(byte[] audio) {
        int bytesPerSecond = appProperties.getVoice().getSampleRate() * 2;
        int maxBytes = bytesPerSecond * appProperties.getVoice().getMaxAudioSeconds();
        if (audio.length > maxBytes) {
            throw new ApplicationException("VOICE_AUDIO_TOO_LONG", "单次语音不能超过 " + appProperties.getVoice().getMaxAudioSeconds() + " 秒", HttpStatus.BAD_REQUEST);
        }
    }
}
