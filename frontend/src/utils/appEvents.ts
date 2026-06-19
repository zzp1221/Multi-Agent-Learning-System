export const APP_EVENTS = {
  activeConversationChanged: 'app:active-conversation-changed',
  authSessionExpired: 'app:auth-session-expired',
  conversationUpdated: 'app:conversation-updated',
  newChat: 'app:new-chat',
  openConversation: 'app:open-conversation',
  profileUpdated: 'app:profile-updated',
  resourceGenerationUpdated: 'app:resource-generation-updated',
} as const;

export interface SelectedConversationEventDetail {
  conversationId?: string;
  title?: string;
  lastMessagePreview?: string;
  lastMessageAt?: string;
  updatedAt?: string;
}

interface AppEventDetailMap {
  [APP_EVENTS.activeConversationChanged]: { conversationId: string };
  [APP_EVENTS.authSessionExpired]: { reason?: string };
  [APP_EVENTS.conversationUpdated]: undefined;
  [APP_EVENTS.newChat]: undefined;
  [APP_EVENTS.openConversation]: SelectedConversationEventDetail;
  [APP_EVENTS.profileUpdated]: undefined;
  [APP_EVENTS.resourceGenerationUpdated]: { conversationId: string };
}

type AppEventName = keyof AppEventDetailMap;

export function dispatchAppEvent<Name extends AppEventName>(
  name: Name,
  ...detail: AppEventDetailMap[Name] extends undefined ? [] : [AppEventDetailMap[Name]]
): void {
  if (typeof window === 'undefined') {
    return;
  }
  const payload = detail[0];
  if (payload === undefined) {
    window.dispatchEvent(new Event(name));
    return;
  }
  window.dispatchEvent(new CustomEvent(name, { detail: payload }));
}

export function addAppEventListener<Name extends AppEventName>(
  name: Name,
  listener: (detail: AppEventDetailMap[Name]) => void,
): () => void {
  if (typeof window === 'undefined') {
    return () => {};
  }
  const handler = (event: Event) => {
    listener((event as CustomEvent<AppEventDetailMap[Name]>).detail);
  };
  window.addEventListener(name, handler as EventListener);
  return () => {
    window.removeEventListener(name, handler as EventListener);
  };
}
