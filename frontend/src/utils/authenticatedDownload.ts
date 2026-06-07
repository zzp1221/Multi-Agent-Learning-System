import { API_BASE_URL, isUnauthorizedError, request } from '../api/request';

const ARTIFACT_DOWNLOAD_PATH_PATTERN = /^\/api\/assets\/download\/[^/?#]+/;
const FALLBACK_FILE_NAME = 'download';

interface AuthenticatedDownloadOptions {
  url: string;
  fileName?: string;
  title?: string;
}

export function isInternalArtifactDownloadUrl(rawUrl: string): boolean {
  const resolvedUrl = resolveUrl(rawUrl);
  if (!resolvedUrl || !ARTIFACT_DOWNLOAD_PATH_PATTERN.test(resolvedUrl.pathname)) {
    return false;
  }

  if (!isAbsoluteUrl(rawUrl)) {
    return true;
  }

  return isTrustedInternalOrigin(resolvedUrl.origin);
}

export async function downloadAuthenticatedFile(options: AuthenticatedDownloadOptions): Promise<void> {
  if (!isInternalArtifactDownloadUrl(options.url)) {
    openDownloadUrl(options.url);
    return;
  }

  try {
    const response = await request.getInstance().get<Blob>(normalizeRelativeDownloadUrl(options.url), {
      responseType: 'blob',
    });
    const responseFileName = readContentDispositionFileName(response.headers['content-disposition']);
    saveBlob(response.data, responseFileName || options.fileName || options.title || FALLBACK_FILE_NAME);
  } catch (error) {
    throw new Error(toDownloadErrorMessage(error));
  }
}

export function openDownloadUrl(rawUrl: string): void {
  const anchor = document.createElement('a');
  anchor.href = rawUrl;
  anchor.target = '_blank';
  anchor.rel = 'noopener noreferrer';
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
}

function resolveUrl(rawUrl: string): URL | null {
  if (typeof window === 'undefined' || !rawUrl.trim()) {
    return null;
  }

  try {
    if (isAbsoluteUrl(rawUrl)) {
      return new URL(rawUrl);
    }
    const baseUrl = API_BASE_URL || window.location.origin;
    return new URL(rawUrl.startsWith('/') ? rawUrl : `/${rawUrl}`, baseUrl);
  } catch {
    return null;
  }
}

function isAbsoluteUrl(rawUrl: string): boolean {
  return /^https?:\/\//i.test(rawUrl);
}

function isTrustedInternalOrigin(origin: string): boolean {
  if (typeof window !== 'undefined' && origin === window.location.origin) {
    return true;
  }
  if (!API_BASE_URL) {
    return false;
  }
  try {
    return origin === new URL(API_BASE_URL, window.location.origin).origin;
  } catch {
    return false;
  }
}

function normalizeRelativeDownloadUrl(rawUrl: string): string {
  if (isAbsoluteUrl(rawUrl)) {
    return rawUrl;
  }
  return rawUrl.startsWith('/') ? rawUrl : `/${rawUrl}`;
}

function readContentDispositionFileName(header: unknown): string {
  if (typeof header !== 'string' || !header.trim()) {
    return '';
  }

  const encodedMatch = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (encodedMatch?.[1]) {
    return decodeFileName(encodedMatch[1]);
  }

  const quotedMatch = /filename="([^"]+)"/i.exec(header);
  if (quotedMatch?.[1]) {
    return decodeFileName(quotedMatch[1]);
  }

  const plainMatch = /filename=([^;]+)/i.exec(header);
  return plainMatch?.[1] ? decodeFileName(plainMatch[1]) : '';
}

function decodeFileName(value: string): string {
  const normalized = value.trim().replace(/^["']|["']$/g, '');
  try {
    return decodeURIComponent(normalized);
  } catch {
    return normalized;
  }
}

function saveBlob(blob: Blob, fileName: string): void {
  const blobUrl = window.URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = blobUrl;
  anchor.download = sanitizeFileName(fileName);
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  window.URL.revokeObjectURL(blobUrl);
}

function sanitizeFileName(fileName: string): string {
  const cleaned = fileName.replace(/[\\/:*?"<>|\u0000-\u001F]/g, '_').trim();
  return cleaned || FALLBACK_FILE_NAME;
}

function toDownloadErrorMessage(error: unknown): string {
  if (isUnauthorizedError(error)) {
    return '登录已失效，请重新登录后下载';
  }

  const status = readHttpStatus(error);
  if (status === 403) {
    return '下载链接已失效或无权限，请重新生成资源后再试';
  }
  if (status === 404 || status === 410) {
    return '下载链接已失效，请重新生成资源';
  }
  return '下载失败，请稍后重试';
}

function readHttpStatus(error: unknown): number | undefined {
  if (!error || typeof error !== 'object') {
    return undefined;
  }

  const maybeHttpStatus = (error as { httpStatus?: unknown }).httpStatus;
  if (typeof maybeHttpStatus === 'number') {
    return maybeHttpStatus;
  }

  const maybeResponseStatus = (error as { response?: { status?: unknown } }).response?.status;
  return typeof maybeResponseStatus === 'number' ? maybeResponseStatus : undefined;
}
