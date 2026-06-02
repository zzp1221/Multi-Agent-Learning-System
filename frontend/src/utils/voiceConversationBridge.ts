export type VoiceConversationStreamPhase = 'start' | 'chunk' | 'done' | 'error';

export interface VoiceConversationStreamEventDetail {
  conversationId: string;
  streamId: string;
  phase: VoiceConversationStreamPhase;
  userText?: string;
  chunk?: string;
  errorMessage?: string;
}

export const VOICE_CONVERSATION_STREAM_EVENT = 'app:voice-conversation-stream';

export function dispatchVoiceConversationStream(detail: VoiceConversationStreamEventDetail) {
  if (typeof window === 'undefined') {
    return;
  }
  window.dispatchEvent(new CustomEvent<VoiceConversationStreamEventDetail>(VOICE_CONVERSATION_STREAM_EVENT, {
    detail,
  }));
}

export function readVoiceConversationStreamDetail(event: Event): VoiceConversationStreamEventDetail | null {
  if (event.type !== VOICE_CONVERSATION_STREAM_EVENT) {
    return null;
  }
  const detail = (event as CustomEvent<VoiceConversationStreamEventDetail>).detail;
  if (!detail?.conversationId?.trim() || !detail.streamId?.trim() || !detail.phase) {
    return null;
  }
  return detail;
}
