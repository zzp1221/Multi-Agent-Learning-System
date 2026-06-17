import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APP_EVENTS, addAppEventListener } from '../utils/appEvents';
import {
  AUTH_USER_STORAGE_KEY,
  getAuthToken,
  isUnauthorizedError,
  notifyAuthSessionExpired,
  persistAuthSession,
} from './request';

describe('auth session expiration notification', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('clears persisted auth and dispatches an expiration event when a token exists', () => {
    const listener = vi.fn();
    const remove = addAppEventListener(APP_EVENTS.authSessionExpired, listener);
    persistAuthSession({ token: 'expired-token' });
    window.localStorage.setItem(AUTH_USER_STORAGE_KEY, JSON.stringify({ id: 1 }));

    notifyAuthSessionExpired('expired');

    expect(getAuthToken()).toBe('');
    expect(window.localStorage.getItem(AUTH_USER_STORAGE_KEY)).toBeNull();
    expect(listener).toHaveBeenCalledWith({ reason: 'expired' });
    remove();
  });

  it('does not dispatch when there is no persisted token', () => {
    const listener = vi.fn();
    const remove = addAppEventListener(APP_EVENTS.authSessionExpired, listener);

    notifyAuthSessionExpired('expired');

    expect(listener).not.toHaveBeenCalled();
    remove();
  });

  it('recognizes localized auth-expired errors without numeric status text', () => {
    expect(isUnauthorizedError(new Error('登录状态已失效，请重新登录'))).toBe(true);
  });

  it('does not treat upstream provider 401 errors as auth session expiration', () => {
    expect(
      isUnauthorizedError(new Error("Client error '401 Unauthorized' for url 'https://api.xiaomimimo.com/v1/chat/completions'")),
    ).toBe(false);
  });
});
