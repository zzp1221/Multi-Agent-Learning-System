import { describe, expect, it, vi } from 'vitest';
import { APP_EVENTS, addAppEventListener, dispatchAppEvent } from './appEvents';

describe('appEvents', () => {
  it('dispatches and receives typed events with detail', () => {
    const listener = vi.fn();
    const remove = addAppEventListener(APP_EVENTS.activeConversationChanged, listener);

    dispatchAppEvent(APP_EVENTS.activeConversationChanged, { conversationId: 'conversation-1' });

    expect(listener).toHaveBeenCalledWith({ conversationId: 'conversation-1' });
    remove();
  });

  it('dispatches auth session expiration events with a reason', () => {
    const listener = vi.fn();
    const remove = addAppEventListener(APP_EVENTS.authSessionExpired, listener);

    dispatchAppEvent(APP_EVENTS.authSessionExpired, { reason: 'token expired' });

    expect(listener).toHaveBeenCalledWith({ reason: 'token expired' });
    remove();
  });

  it('unsubscribes listeners', () => {
    const listener = vi.fn();
    const remove = addAppEventListener(APP_EVENTS.conversationUpdated, listener);
    remove();

    dispatchAppEvent(APP_EVENTS.conversationUpdated);

    expect(listener).not.toHaveBeenCalled();
  });
});
