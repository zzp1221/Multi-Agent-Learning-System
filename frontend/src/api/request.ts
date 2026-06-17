import axios, { AxiosError, AxiosInstance, AxiosRequestConfig } from 'axios';
import { APP_EVENTS, dispatchAppEvent } from '../utils/appEvents';

/**
 * 后端统一响应结构
 */
type ResultCode = number | string;

interface Result<T = unknown> {
  code: ResultCode;
  message: string;
  data: T;
  traceId?: string;
  timestamp?: number;
}

const SUCCESS_CODES = new Set<ResultCode>([0, 200, '0', '200', 'SUCCESS']);

class ApiError extends Error {
  readonly code: ResultCode;
  readonly traceId?: string;
  readonly httpStatus?: number;

  constructor(message: string, options: { code?: ResultCode; traceId?: string; httpStatus?: number } = {}) {
    super(message);
    this.name = 'ApiError';
    this.code = options.code ?? -1;
    this.traceId = options.traceId;
    this.httpStatus = options.httpStatus;
  }
}

export class AuthSessionExpiredError extends Error {
  constructor() {
    super('登录状态已失效，请重新登录');
    this.name = 'AuthSessionExpiredError';
  }
}

function resolveApiBaseUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL?.trim();
  if (configured) {
    return configured.replace(/\/+$/, '');
  }
  return '';
}

export const API_BASE_URL = resolveApiBaseUrl();

const AUTH_TOKEN_STORAGE_KEY = 'auth_token';
export const AUTH_USER_STORAGE_KEY = 'auth_user';
const DEFAULT_TIMEOUT_MS = 30_000;
const DEFAULT_GET_RETRY_TIMES = 2;

interface ExtendedAxiosRequestConfig extends AxiosRequestConfig {
  dedupeKey?: string;
  dedupe?: boolean;
  retry?: number;
}

const pendingGetMap = new Map<string, Promise<unknown>>();

const instance: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: DEFAULT_TIMEOUT_MS,
});

export function getAuthToken(): string {
  if (typeof window === 'undefined') {
    return '';
  }
  return window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY)?.trim() ?? '';
}

export function persistAuthSession(payload: { token?: string }): void {
  if (typeof window === 'undefined') {
    return;
  }
  if (payload.token) {
    window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, payload.token);
  }
}

export function clearAuthSession(): void {
  if (typeof window === 'undefined') {
    return;
  }
  window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
  window.localStorage.removeItem(AUTH_USER_STORAGE_KEY);
}

export function notifyAuthSessionExpired(reason: string): void {
  if (typeof window === 'undefined' || !getAuthToken()) {
    return;
  }
  clearAuthSession();
  dispatchAppEvent(APP_EVENTS.authSessionExpired, { reason });
}

export function getAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const token = getAuthToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

instance.interceptors.request.use((config) => {
  const authHeaders = getAuthHeaders();
  if (Object.keys(authHeaders).length > 0) {
    config.headers = config.headers ?? {};
    Object.assign(config.headers, authHeaders);
  }
  return config;
});

/**
 * 响应拦截器
 * 
 * 后端约定：所有响应都是 HTTP 200 + Result
 * - code === 200 → 成功，返回 data
 * - code !== 200 → 失败，直接显示 message
 */
instance.interceptors.response.use(
  (response) => {
    const result = response.data as Result;
    
    // 检查是否是 Result 格式
    if (result && typeof result === 'object' && 'code' in result) {
      if (SUCCESS_CODES.has(result.code)) {
        // 成功：返回 data
        response.data = result.data;
        return response;
      }
      // 失败：直接抛出 message
      return Promise.reject(
        new ApiError(result.message || '请求失败', {
          code: result.code,
          traceId: result.traceId,
          httpStatus: response.status,
        }),
      );
    }
    
    // 非 Result 格式，直接返回
    return response;
  },
  (error: AxiosError<Result>) => {
    // 有响应的情况：后端返回了结果（即使是错误）
    if (error.response) {
      const { data, status } = error.response;
      if (status === 401) {
        notifyAuthSessionExpired('unauthorized');
        return Promise.reject(new AuthSessionExpiredError());
      }
      if (status === 429) {
        return Promise.reject(
          new ApiError('请求过于频繁，请稍等片刻后重试', { httpStatus: status }),
        );
      }
      // 尝试解析 Result 格式
      if (data && typeof data === 'object' && 'code' in data && 'message' in data) {
        const result = data as Result;
        return Promise.reject(
          new ApiError(result.message || '请求失败', {
            code: result.code,
            traceId: result.traceId,
            httpStatus: status,
          }),
        );
      }
      // 响应格式不对
      return Promise.reject(new ApiError('请求失败，请重试', { httpStatus: status }));
    }

    // 没有响应的情况：真正的网络错误或连接被重置
    // 对于文件上传，可能是网络超时或连接中断，但不一定是文件大小问题
    // 让后端返回真实的错误信息，而不是在这里假设
    const config = error.config;
    const isUpload = config && (
      config.url?.includes('/upload') ||
      config.headers?.['Content-Type']?.toString().includes('multipart')
    );

    if (isUpload) {
      // 文件上传失败且没有响应，可能是网络超时或连接中断
      // 不直接假设是文件大小问题，返回更通用的错误信息
      return Promise.reject(new ApiError('上传失败，可能是网络超时或连接中断，请重试'));
    }

    // 其他网络错误
    return Promise.reject(new ApiError('网络连接失败，请检查网络'));
  }
);

export const request = {
  async get<T>(url: string, config?: ExtendedAxiosRequestConfig): Promise<T> {
    const requestConfig = config ?? {};
    const dedupeEnabled = requestConfig.dedupe !== false;
    const dedupeKey = requestConfig.dedupeKey ?? `${url}:${JSON.stringify(requestConfig.params ?? {})}`;
    if (dedupeEnabled && pendingGetMap.has(dedupeKey)) {
      return pendingGetMap.get(dedupeKey) as Promise<T>;
    }

    const retryTimes = requestConfig.retry ?? DEFAULT_GET_RETRY_TIMES;
    const runWithRetry = async (): Promise<T> => {
      for (let attempt = 0; attempt <= retryTimes; attempt += 1) {
        try {
          const response = await instance.get<T>(url, requestConfig);
          return response.data;
        } catch (error) {
          const shouldRetry =
            attempt < retryTimes &&
            isRetryableGetError(error);
          if (!shouldRetry) {
            throw error;
          }
          await wait(200 * (attempt + 1));
        }
      }
      throw new ApiError('请求失败，请重试');
    };

    const promise = runWithRetry().finally(() => {
      if (dedupeEnabled) {
        pendingGetMap.delete(dedupeKey);
      }
    });

    if (dedupeEnabled) {
      pendingGetMap.set(dedupeKey, promise);
    }
    return promise;
  },

  post<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return instance.post(url, data, config).then(res => res.data);
  },

  put<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return instance.put(url, data, config).then(res => res.data);
  },

  patch<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return instance.patch(url, data, config).then(res => res.data);
  },

  delete<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    return instance.delete(url, config).then(res => res.data);
  },

  /**
   * 获取原始实例（用于特殊场景如下载 Blob）
   */
  getInstance(): AxiosInstance {
    return instance;
  },
};

/**
 * 获取错误信息
 */
export function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return toUserFacingErrorMessage(error.message);
  }
  return '未知错误';
}

function toUserFacingErrorMessage(message: string): string {
  const normalized = message.trim();
  if (!normalized) {
    return '请求失败，请稍后重试';
  }
  if (/\b401\b/.test(normalized) || normalized.includes('请先登录') || normalized.includes('认证信息无效')) {
    return '登录状态已失效，请重新登录';
  }
  if (/(traceId|taskId|Traceback|RuntimeError|ValidationError|AxiosError|Request failed|Network Error|HTTP\s*\d{3}|status\s*code\s*\d{3})/i.test(normalized)) {
    return '请求暂时没有完成，请稍后重试';
  }
  if (/^\{[\s\S]*\}$/.test(normalized) || /^\[[\s\S]*\]$/.test(normalized)) {
    return '请求暂时没有完成，请稍后重试';
  }
  return normalized;
}

export function isUnauthorizedError(error: unknown): boolean {
  if (error instanceof ApiError) {
    return error.httpStatus === 401 || error.code === 401 || error.code === '401' || error.code === 'AUTH_REQUIRED';
  }
  if (error instanceof Error) {
    return error instanceof AuthSessionExpiredError
      || error.message.includes('登录状态已失效')
      || error.message.includes('请先登录')
      || error.message.includes('认证信息无效');
  }
  return false;
}

export default request;

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function isRetryableGetError(error: unknown): boolean {
  if (error instanceof ApiError) {
    return error.httpStatus === undefined || error.httpStatus >= 500 || error.httpStatus === 429;
  }
  if (axios.isAxiosError(error)) {
    return !error.response || error.response.status >= 500 || error.response.status === 429;
  }
  return false;
}
