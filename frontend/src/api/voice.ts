import { API_BASE_URL, getAuthHeaders, getAuthToken, request } from './request';
import { streamSse } from './sse';

export interface VoiceTranscribeResponse {
  text: string;
  durationMs: number;
  provider: string;
  model: string;
}

export interface VoiceSessionResponse {
  sessionId: string;
  expiresAt: string;
  sampleRate: number;
  provider: string;
  asrModel: string;
  ttsModel: string;
}

export interface VoiceCommandResponse {
  intent: string;
  normalizedText: string;
  handledLocally: boolean;
  context: Record<string, string>;
}

export interface VoiceTtsEvent {
  event: string;
  payload: {
    audio?: string;
    sampleRate?: number;
    format?: string;
    message?: string;
    finished?: boolean;
  };
}

export type VoiceRealtimeEventType =
  | 'ready'
  | 'asr_ready'
  | 'asr_connecting'
  | 'asr_partial'
  | 'asr_final'
  | 'commit_ack'
  | 'cancelled'
  | 'error';

export interface VoiceRealtimeEvent {
  type: VoiceRealtimeEventType;
  sessionId?: string;
  turnId?: string;
  cancelledTurnId?: string;
  text?: string;
  sampleRate?: number;
  provider?: string;
  model?: string;
  message?: string;
}

export const voiceApi = {
  async transcribePcm(file: Blob): Promise<VoiceTranscribeResponse> {
    const formData = new FormData();
    formData.append('file', file, 'voice.pcm');
    return request.post<VoiceTranscribeResponse>('/api/voice/transcribe', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    });
  },

  async createSession(): Promise<VoiceSessionResponse> {
    return request.post<VoiceSessionResponse>('/api/voice/sessions');
  },

  async parseCommand(text: string): Promise<VoiceCommandResponse> {
    return request.post<VoiceCommandResponse>('/api/voice/commands/parse', { text });
  },

  async streamTts(
    text: string,
    handlers: {
      onEvent: (event: VoiceTtsEvent) => void;
      onDone: () => void;
      onError: (error: Error) => void;
    },
    signal?: AbortSignal,
  ): Promise<void> {
    await streamSse(`${API_BASE_URL}/api/voice/tts/stream`, {
      init: {
        method: 'POST',
        headers: {
          Accept: 'text/event-stream',
          'Content-Type': 'application/json',
          ...getAuthHeaders(),
        },
        body: JSON.stringify({ text }),
        signal,
      },
      missingBodyMessage: '无法读取语音合成流',
      requestFailedMessage: (status) => `语音合成请求失败 (${status})`,
      onEvent: (rawEvent) => {
        const parsed = parseTtsPayload(rawEvent.event, rawEvent.data);
        handlers.onEvent(parsed);
        if (parsed.event === 'done') {
          handlers.onDone();
          return true;
        }
        if (parsed.event === 'error') {
          handlers.onError(new Error(parsed.payload.message || '语音合成失败'));
          return true;
        }
        return false;
      },
      onDone: handlers.onDone,
      onError: handlers.onError,
    });
  },

  buildRealtimeUrl(sessionId: string): string {
    const base = API_BASE_URL || window.location.origin;
    const url = new URL('/api/voice/ws', base);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    url.searchParams.set('sessionId', sessionId);
    url.searchParams.set('token', getAuthToken());
    return url.toString();
  },
};

function parseTtsPayload(event: string, rawData: string): VoiceTtsEvent {
  try {
    const parsed = JSON.parse(rawData) as { event?: string; payload?: VoiceTtsEvent['payload'] };
    return {
      event: parsed.event ?? event,
      payload: parsed.payload ?? {},
    };
  } catch {
    return {
      event,
      payload: {},
    };
  }
}
