export type VoicePageAction = 'start_review' | 'generate_study_plan';

export interface VoicePageActionEventDetail {
  action: VoicePageAction;
}

export const VOICE_PAGE_ACTION_EVENT = 'app:voice-page-action';
const VOICE_PAGE_ACTION_STORAGE_KEY = 'voice_page_action';

export function queueVoicePageAction(action: VoicePageAction) {
  if (typeof window === 'undefined') {
    return;
  }
  window.sessionStorage.setItem(VOICE_PAGE_ACTION_STORAGE_KEY, action);
  window.dispatchEvent(new CustomEvent<VoicePageActionEventDetail>(VOICE_PAGE_ACTION_EVENT, {
    detail: { action },
  }));
}

export function consumeQueuedVoicePageAction(expectedAction: VoicePageAction): boolean {
  if (typeof window === 'undefined') {
    return false;
  }
  const queuedAction = window.sessionStorage.getItem(VOICE_PAGE_ACTION_STORAGE_KEY);
  if (queuedAction !== expectedAction) {
    return false;
  }
  window.sessionStorage.removeItem(VOICE_PAGE_ACTION_STORAGE_KEY);
  return true;
}

export function isVoicePageActionEvent(event: Event, expectedAction: VoicePageAction): boolean {
  if (event.type !== VOICE_PAGE_ACTION_EVENT) {
    return false;
  }
  const detail = (event as CustomEvent<VoicePageActionEventDetail>).detail;
  return detail?.action === expectedAction;
}
