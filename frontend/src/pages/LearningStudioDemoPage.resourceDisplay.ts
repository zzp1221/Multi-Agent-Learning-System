import { readString as readSharedString } from '../utils/valueReaders';
import { readNumericRaw } from './LearningStudioDemoPage.taskPayloadReaders';
import type {
  InlineResourceView,
  TempDownloadLink,
  VideoCardStyle,
  VideoResult,
} from './LearningStudioDemoPage.types';

function readString(value: unknown): string {
  return readSharedString(value);
}

function readNumeric(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return Math.max(0, Math.min(100, value));
  }
  if (typeof value === 'string') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return Math.max(0, Math.min(100, parsed));
    }
  }
  return undefined;
}
export function formatExpiresHint(payload: Record<string, unknown> | undefined): string {
  if (!payload) {
    return '下载链接已生成';
  }

  const expiresInSec = readNumeric(payload.expiresInSec);
  if (expiresInSec !== undefined) {
    return `${expiresInSec} 秒后过期`;
  }

  const expiresAt = readString(payload.expiresAt);
  if (expiresAt) {
    return `到期时间 ${new Date(expiresAt).toLocaleString('zh-CN')}`;
  }

  return '下载链接已生成';
}

export function readDuration(payload: Record<string, unknown> | undefined): number | undefined {
  if (!payload) {
    return undefined;
  }
  const duration = readNumericRaw(payload.duration) ?? readNumericRaw(payload.durationSeconds) ?? readNumericRaw(payload.totalDuration);
  if (duration === undefined) {
    return undefined;
  }
  return Math.max(0, duration);
}

export function readVideoStyle(payload: Record<string, unknown> | undefined): VideoCardStyle | undefined {
  if (!payload) {
    return undefined;
  }
  const style = readString(payload.style) || readString(payload.videoStyle);
  if (style === 'talking_head' || style === 'animation' || style === 'hybrid') {
    return style;
  }
  return undefined;
}

export function readUrlField(payload: Record<string, unknown> | undefined, keys: string[]): string {
  if (!payload) {
    return '';
  }
  for (const key of keys) {
    const value = readString(payload[key]);
    if (value) {
      return value;
    }
  }
  return '';
}

export function isVideoLink(item: TempDownloadLink): boolean {
  if (item.resourceType !== 'VIDEO') {
    return false;
  }
  const mimeType = (item.mimeType || '').toLowerCase();
  const fileName = (item.fileName || '').toLowerCase();
  const url = item.url.toLowerCase();
  if (mimeType.startsWith('video/')) {
    return true;
  }
  return ['.mp4', '.webm', '.mov', '.m4v', '.m3u8'].some((ext) => fileName.endsWith(ext) || url.includes(ext));
}

export function mapDownloadToVideoResult(item: TempDownloadLink): VideoResult {
  return {
    title: item.title,
    videoUrl: item.url,
    thumbnailUrl: item.thumbnailUrl,
    duration: item.duration,
    style: item.style,
    knowledgePoint: item.knowledgePoint,
    expiresHint: item.expiresHint,
    fileName: item.fileName,
  };
}

export function isSafeRecommendationContent(title: string, summary: string, sourceName: string, url: string): boolean {
  const combined = `${title} ${summary} ${sourceName} ${url}`.toLowerCase();
  const blockedTokens = [
    'china-dictatorship',
    'anti chinese',
    'anti-china',
    'anti china',
    'anti ccp',
    '反共',
    '反华',
    '政治宣传',
    '宣传库',
    'propaganda',
    'dictatorship',
    'falun',
    'falun gong',
    '法轮功',
    '六四',
    '天安门',
    '疆独',
    '港独',
    '台独',
    '邪教',
    '习近平',
    'xijinping',
    'ccp',
    '共产党',
  ];
  return !blockedTokens.some((token) => combined.includes(token));
}

export function truncateRecommendationText(value: string, limit: number): string {
  const normalized = value.trim();
  if (normalized.length <= limit) {
    return normalized;
  }
  return normalized.slice(0, limit);
}

export function readVideoResult(payload: Record<string, unknown> | undefined): VideoResult | null {
  if (!payload) {
    return null;
  }
  const assetType = readString(payload.assetType);
  if (assetType && assetType !== 'VIDEO') {
    return null;
  }
  const videoUrl =
    readUrlField(payload, ['videoUrl', 'finalVideoUrl', 'final_video_url', 'downloadUrl', 'resourceUrl']) ||
    readNestedVideoUrl(payload.result);
  if (!videoUrl) {
    return null;
  }
  const mimeType = readString(payload.mimeType).toLowerCase();
  const fileName = readString(payload.fileName).toLowerCase();
  const normalizedUrl = videoUrl.toLowerCase();
  const isVideoUrl = mimeType.startsWith('video/')
    || ['.mp4', '.webm', '.mov', '.m4v', '.m3u8'].some((ext) => fileName.endsWith(ext) || normalizedUrl.includes(ext));
  if (!isVideoUrl) {
    return null;
  }

  return {
    title: readString(payload.title) || readString(payload.topic) || '教学视频',
    videoUrl,
    thumbnailUrl: readUrlField(payload, ['thumbnailUrl', 'thumbnail_url', 'posterUrl', 'coverUrl']),
    duration: readDuration(payload),
    style: readVideoStyle(payload),
    knowledgePoint: readString(payload.knowledgePoint) || readString(payload.topic),
    expiresHint: formatExpiresHint(payload),
    renderStatus: 'ready',
  };
}

export function mergeVideoResult(previous: VideoResult | null, next: VideoResult): VideoResult {
  if (!previous) {
    return next;
  }
  if (next.videoUrl || next.renderStatus === 'ready' || next.renderStatus === 'failed') {
    return {
      ...previous,
      ...next,
    };
  }
  return {
    ...next,
    videoUrl: previous.videoUrl || next.videoUrl,
    thumbnailUrl: previous.thumbnailUrl || next.thumbnailUrl,
    renderStatus: previous.renderStatus === 'ready' ? previous.renderStatus : next.renderStatus,
    renderMessage: next.renderMessage || previous.renderMessage,
  };
}

export function readBrowserRenderedVideoResult(
  payload: Record<string, unknown> | undefined,
  renderMessage: string,
): VideoResult | null {
  if (!payload) {
    return null;
  }
  const assetType = readString(payload.assetType).toUpperCase();
  const displayMode = readString(payload.displayMode).toUpperCase();
  const mimeType = readString(payload.mimeType).toLowerCase();
  const fileName = readString(payload.fileName).toLowerCase();
  const hasAudio = Boolean(readString(payload.audioBase64));
  const isBrowserRenderedVideo =
    hasAudio
    || displayMode === 'VIDEO_PLAYER'
    || fileName === 'browser-rendered.webm'
    || (assetType === 'VIDEO' && mimeType.startsWith('video/'));
  if (!isBrowserRenderedVideo) {
    return null;
  }
  return {
    title: readString(payload.title) || readString(payload.topic) || '教学视频',
    videoUrl: '',
    thumbnailUrl: readUrlField(payload, ['thumbnailUrl', 'thumbnail_url', 'posterUrl', 'coverUrl']),
    duration: readDuration(payload),
    style: readVideoStyle(payload),
    knowledgePoint: readString(payload.knowledgePoint) || readString(payload.topic),
    expiresHint: '视频正在生成',
    fileName: readString(payload.fileName) || undefined,
    renderStatus: 'rendering',
    renderMessage,
  };
}

function readNestedVideoUrl(value: unknown): string {
  if (!value || typeof value !== 'object') {
    return '';
  }
  const payload = value as Record<string, unknown>;
  return readUrlField(payload, ['videoUrl', 'finalVideoUrl', 'final_video_url', 'downloadUrl', 'resourceUrl']);
}

export function readInlineResource(payload: Record<string, unknown> | undefined): InlineResourceView | null {
  if (!payload) {
    return null;
  }
  const displayMode = readString(payload.displayMode).toUpperCase();
  const inlineContent = readString(payload.inlineContent);
  if (!inlineContent) {
    return null;
  }
  const title = readString(payload.title) || '内嵌资源';
  const summary = readString(payload.summary);
  if (displayMode === 'INLINE_CODE') {
    return {
      kind: 'code',
      title,
      summary,
      content: inlineContent,
      language: readString(payload.language) || 'text',
      explanation: readString(payload.explanation),
    };
  }
  if (displayMode === 'INLINE_MERMAID') {
    return {
      kind: 'mermaid',
      title,
      summary,
      content: inlineContent,
    };
  }
  if (displayMode === 'MARKDOWN_CARD') {
    return {
      kind: 'markdown',
      title,
      summary,
      content: inlineContent,
    };
  }
  return null;
}
