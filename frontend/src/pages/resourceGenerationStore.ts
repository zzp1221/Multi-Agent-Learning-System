import type {
  ConversationStreamEventPayload,
  InlineResourceView,
  TempDownloadLink,
  VideoResult,
} from './LearningStudioDemoPage.types';

export type GeneratedResourceType = 'DOCUMENT' | 'SLIDES' | 'MINDMAP' | 'QUIZ' | 'READING' | 'VIDEO' | 'CODE';
export type ResourceGenerationResourceStatus = 'generating' | 'ready' | 'waiting_confirmation' | 'failed' | 'not_confirmed';

export interface ResourceGenerationQuizSummary {
  title: string;
  topic: string;
  difficulty?: string;
  description?: string;
  questionCount: number;
  generatedBy?: string;
  agentName?: string;
}

export interface ResourceGenerationResource {
  id: string;
  type: GeneratedResourceType;
  title: string;
  summary?: string;
  status: ResourceGenerationResourceStatus;
  statusText?: string;
  progress?: number;
  failureReason?: string;
  download?: TempDownloadLink;
  downloadFallback?: {
    content: string;
    mimeType: string;
    fileName: string;
  };
  sourceAgent?: string;
  inline?: InlineResourceView;
  slideOutline?: string;
  quiz?: ResourceGenerationQuizSummary;
  video?: VideoResult;
  updatedAt: number;
}

export interface ResourceGenerationSession {
  conversationId: string;
  topic?: string;
  taskStatus: 'idle' | 'running' | 'waiting_confirmation' | 'completed' | 'partial_failed' | 'failed';
  conversationTriggered: boolean;
  progress: number;
  statusText: string;
  resources: ResourceGenerationResource[];
  completedAt?: number;
  updatedAt: number;
}

export const RESOURCE_GENERATION_UPDATED_EVENT = 'app:resource-generation-updated';
const STORAGE_KEY = 'learning_studio_conversation_resources';
const MAX_SESSIONS = 20;

export function loadResourceGenerationSession(conversationId: string): ResourceGenerationSession {
  const normalizedId = normalizeConversationId(conversationId);
  const all = loadAllSessions();
  return all[normalizedId] ?? createEmptySession(normalizedId);
}

export function clearResourceGenerationSession(conversationId: string): void {
  if (typeof window === 'undefined') {
    return;
  }
  const normalizedId = normalizeConversationId(conversationId);
  const all = loadAllSessions();
  delete all[normalizedId];
  saveAllSessions(all);
  notifyResourceGenerationUpdated(normalizedId);
}

export function markSlideOutlineRejected(conversationId: string, outlineTitle?: string): ResourceGenerationSession {
  const normalizedId = normalizeConversationId(conversationId);
  const all = loadAllSessions();
  const current = all[normalizedId] ?? createEmptySession(normalizedId);
  const title = (outlineTitle || '').trim();
  const now = Date.now();
  const resources = current.resources.map((resource) => {
    const target = resource.type === 'SLIDES'
      && resource.status === 'waiting_confirmation'
      && (!title || resource.title === title);
    if (!target) {
      return resource;
    }
    return {
      ...resource,
      status: 'not_confirmed' as const,
      statusText: '未确认，未生成',
      download: undefined,
      downloadFallback: undefined,
      inline: undefined,
      slideOutline: undefined,
      updatedAt: now,
    };
  });
  const next = {
    ...current,
    resources,
    updatedAt: now,
    statusText: 'PPT 大纲未确认，已停止生成演示文稿',
  };
  all[normalizedId] = next;
  saveAllSessions(all);
  notifyResourceGenerationUpdated(normalizedId);
  return next;
}

export function recordConversationResourceEvent(
  conversationId: string,
  eventName: string,
  data: ConversationStreamEventPayload,
): ResourceGenerationSession {
  const normalizedId = normalizeConversationId(conversationId);
  const all = loadAllSessions();
  const current = all[normalizedId] ?? createEmptySession(normalizedId);
  const next = reduceResourceEvent(current, eventName, data);
  all[normalizedId] = next;
  saveAllSessions(all);
  notifyResourceGenerationUpdated(normalizedId);
  return next;
}

export function updateResourceVideoRenderResult(
  conversationId: string,
  resourceIdValue: string,
  videoPatch: Partial<VideoResult>,
  statusText?: string,
): ResourceGenerationSession {
  const normalizedId = normalizeConversationId(conversationId);
  const all = loadAllSessions();
  const current = all[normalizedId] ?? createEmptySession(normalizedId);
  const now = Date.now();
  const resources = current.resources.map((resource) => {
    if (resource.id !== resourceIdValue || resource.type !== 'VIDEO') {
      return resource;
    }
    const failed = videoPatch.renderStatus === 'failed';
    return {
      ...resource,
      status: failed ? 'failed' as const : 'ready' as const,
      statusText: statusText || videoPatch.renderMessage || resource.statusText,
      failureReason: failed ? videoPatch.renderMessage || resource.failureReason : resource.failureReason,
      video: {
        ...(resource.video ?? {
          title: resource.title,
          videoUrl: '',
        }),
        ...videoPatch,
      },
      updatedAt: now,
    };
  });
  const next = {
    ...current,
    resources,
    updatedAt: now,
  };
  all[normalizedId] = next;
  saveAllSessions(all);
  notifyResourceGenerationUpdated(normalizedId);
  return next;
}

function reduceResourceEvent(
  session: ResourceGenerationSession,
  eventName: string,
  data: ConversationStreamEventPayload,
): ResourceGenerationSession {
  const payload = data.payload ?? {};
  const now = Date.now();
  let next: ResourceGenerationSession = {
    ...session,
    updatedAt: now,
  };

  if (eventName === 'progress') {
    const resourceProgress = isResourceProgressEvent(payload);
    if (!next.conversationTriggered && !resourceProgress) {
      return next;
    }
    const progress = clampPercent(readNumber(payload.percent) ?? readNumber(payload.progress) ?? next.progress);
    const artifactType = normalizeResourceType(readString(payload.artifactType));
    const status = readString(payload.status).toUpperCase();
    const failed = status === 'FAILED' || status === 'ERROR';
    const progressText = userFacingProgressText(payload, artifactType);
    next = {
      ...next,
      conversationTriggered: true,
      taskStatus: failed ? 'partial_failed' : 'running',
      progress,
      statusText: progressText,
      topic: next.topic || readString(payload.topic),
      resources: artifactType && failed
        ? upsertResource(next.resources, {
          id: resourceId(artifactType, readString(payload.title) || resourceLabel(artifactType)),
          type: artifactType,
          title: readString(payload.title) || resourceLabel(artifactType),
          summary: readString(payload.message) || undefined,
          status: failed ? 'failed' : 'generating',
          statusText: progressText,
          progress,
          failureReason: failed ? readString(payload.message) || '生成失败' : undefined,
          sourceAgent: readString(payload.agentName) || undefined,
          updatedAt: now,
        })
        : next.resources,
    };
    return next;
  }

  if (eventName.startsWith('video_gen:')) {
    const progress = videoProgress(payload, next.progress);
    next = {
      ...next,
      conversationTriggered: true,
      taskStatus: 'running',
      progress,
      statusText: videoProgressText(eventName),
      topic: next.topic || readString(payload.topic),
    };
    if (eventName === 'video_gen:speech' || eventName === 'video_gen:complete') {
      const video = readVideoResult(payload);
      if (video) {
        next.resources = upsertResource(next.resources, {
          id: resourceId('VIDEO', video.title),
          type: 'VIDEO',
          title: video.title,
          summary: readString(payload.message),
          sourceAgent: readString(payload.agentName) || readString(payload.generatedBy) || undefined,
          status: 'ready',
          statusText: '短视频素材已就绪',
          progress,
          video,
          updatedAt: now,
        });
      }
    }
    return next;
  }

  if (eventName === 'resource_file') {
    if (!hasAllowedResourceProvenance(payload)) {
      return next;
    }
    if (isSlideOutlineConfirmationPayload(payload) && readString(payload.inlineContent)) {
      return {
        ...next,
        conversationTriggered: true,
        taskStatus: 'running',
        statusText: '等待在对话中确认 PPT 大纲',
        topic: next.topic || readString(payload.topic) || readString(payload.title),
      };
    }
    const resource = readResourceFile(payload, now);
    if (!resource) {
      return next;
    }
    return {
      ...next,
      conversationTriggered: true,
      taskStatus: 'running',
      progress: next.progress,
      statusText: resource.statusText || `${resourceLabel(resource.type)}已生成`,
      topic: next.topic || readString(payload.topic) || resource.title,
      resources: upsertResource(next.resources, resource),
    };
  }

  if (eventName === 'question_batch') {
    if (!hasRealLlmProvenance(payload)) {
      return next;
    }
    const quiz = readQuestionBatchSummary(payload);
    if (!quiz) {
      return next;
    }
    return {
      ...next,
      conversationTriggered: true,
      taskStatus: 'running',
      progress: next.progress,
      statusText: '练习题已进入答题助手',
      topic: next.topic || quiz.topic,
      resources: upsertResource(next.resources, {
        id: resourceId('QUIZ', quiz.title),
        type: 'QUIZ',
        title: quiz.title,
        summary: quiz.description || `${quiz.questionCount} 道练习题已生成`,
        status: 'ready',
        statusText: '已进入练习弹窗，可继续答题',
        sourceAgent: quiz.agentName || quiz.generatedBy,
        quiz,
        updatedAt: now,
      }),
    };
  }

  if (eventName === 'done') {
    if (!next.conversationTriggered) {
      return next;
    }
    const status = readString(payload.status).toUpperCase();
    const failed = status === 'FAILED' || status === 'ERROR';
    const partial = status === 'PARTIAL_FAILED';
    const waitingConfirmation = status === 'WAITING_CONFIRMATION';
    return {
      ...next,
      taskStatus: failed ? 'failed' : partial ? 'partial_failed' : waitingConfirmation ? 'waiting_confirmation' : 'completed',
      progress: failed ? next.progress : waitingConfirmation ? Math.min(next.progress, 99) : 100,
      statusText: readString(payload.summary)
        || (failed ? '资源生成失败' : partial ? '资源部分完成' : waitingConfirmation ? '等待确认后继续生成' : '资源生成完成'),
      completedAt: waitingConfirmation ? next.completedAt : now,
    };
  }

  if (eventName === 'error') {
    if (!next.conversationTriggered) {
      return next;
    }
    return {
      ...next,
      taskStatus: 'failed',
      statusText: readString(payload.message) || '资源生成失败',
    };
  }

  return next;
}

function readResourceFile(payload: Record<string, unknown>, now: number): ResourceGenerationResource | null {
  const type = normalizeResourceType(readString(payload.assetType));
  if (!type) {
    return null;
  }
  const title = readString(payload.title) || readString(payload.fileName) || resourceLabel(type);
  const summary = readString(payload.summary);
  const downloadUrl = readString(payload.downloadUrl);
  const inline = readInlineResource(payload);
  const displayMode = readString(payload.displayMode).toUpperCase();
  const waitingSlideConfirmation = type === 'SLIDES' && displayMode === 'SLIDE_OUTLINE_CONFIRMATION';
  const missingSlideOutline = waitingSlideConfirmation && !readString(payload.inlineContent);
  const video = type === 'VIDEO' ? readVideoResult(payload) : null;
  const fileName = readString(payload.fileName) || defaultFileName(title, type, inline);
  const mimeType = readString(payload.mimeType) || defaultMimeType(type, inline);
  return {
    id: resourceId(type, title),
    type,
    title,
    summary,
    status: missingSlideOutline ? 'failed' : waitingSlideConfirmation ? 'waiting_confirmation' : 'ready',
    statusText: missingSlideOutline ? 'PPT 大纲事件缺少正文，请重新生成' : waitingSlideConfirmation ? '等待确认后生成 PPT 文件' : '已生成',
    failureReason: missingSlideOutline ? 'PPT 大纲事件缺少正文，请重新生成' : undefined,
    sourceAgent: readString(payload.agentName) || readString(payload.generatedBy) || readString(payload.sourceAgent) || undefined,
    download: downloadUrl
      ? {
        title,
        url: downloadUrl,
        fileName,
        expiresHint: readString(payload.expiresAt) || '临时下载链接',
        resourceType: type,
        mimeType,
        summary: summary || undefined,
        sourceName: readString(payload.sourceName) || undefined,
        thumbnailUrl: readString(payload.thumbnailUrl) || readString(payload.thumbnailPath) || undefined,
        duration: readNumber(payload.durationSeconds),
        knowledgePoint: readString(payload.knowledgePoint) || undefined,
      }
      : undefined,
    downloadFallback: undefined,
    inline: waitingSlideConfirmation ? undefined : inline ?? undefined,
    slideOutline: waitingSlideConfirmation ? readString(payload.inlineContent) || undefined : undefined,
    video: video ?? undefined,
    updatedAt: now,
  };
}

function readInlineResource(payload: Record<string, unknown>): InlineResourceView | null {
  const inlineContent = readString(payload.inlineContent);
  if (!inlineContent) {
    return null;
  }
  const displayMode = readString(payload.displayMode).toUpperCase();
  const assetType = normalizeResourceType(readString(payload.assetType));
  const mimeType = readString(payload.mimeType).toLowerCase();
  const title = readString(payload.title) || resourceLabel(assetType || 'DOCUMENT');
  const base = {
    title,
    summary: readString(payload.summary) || undefined,
    content: inlineContent,
  };
  if (assetType === 'MINDMAP' || displayMode === 'MERMAID' || inlineContent.trim().toLowerCase().startsWith('mindmap')) {
    return { ...base, kind: 'mermaid' };
  }
  if (assetType === 'CODE' || mimeType.includes('python') || mimeType.includes('javascript')) {
    return {
      ...base,
      kind: 'code',
      language: readString(payload.language) || 'text',
      explanation: readString(payload.explanation) || undefined,
    };
  }
  return { ...base, kind: 'markdown' };
}

function readQuestionBatchSummary(payload: Record<string, unknown>): ResourceGenerationQuizSummary | null {
  const questions = Array.isArray(payload.questions) ? payload.questions : [];
  if (questions.length === 0) {
    return null;
  }
  return {
    title: readString(payload.title) || '练习题',
    topic: readString(payload.topic),
    difficulty: readString(payload.difficulty),
    description: readString(payload.description) || undefined,
    generatedBy: readString(payload.generatedBy) || undefined,
    agentName: readString(payload.agentName) || undefined,
    questionCount: questions.length,
  };
}

function hasAllowedResourceProvenance(payload: Record<string, unknown>): boolean {
  if (isSlideOutlineConfirmationPayload(payload)) {
    return true;
  }
  if (!requiresLlmProvenance(payload)) {
    return true;
  }
  return hasRealLlmProvenance(payload);
}

function isSlideOutlineConfirmationPayload(payload: Record<string, unknown>): boolean {
  const assetType = readString(payload.assetType).toUpperCase();
  const displayMode = readString(payload.displayMode).toUpperCase();
  return (assetType === 'SLIDES' || assetType === 'PPT') && displayMode === 'SLIDE_OUTLINE_CONFIRMATION';
}

function requiresLlmProvenance(payload: Record<string, unknown>): boolean {
  const displayMode = readString(payload.displayMode).toLowerCase();
  const sourceName = readString(payload.sourceName).toLowerCase();
  if (displayMode === 'external_link') {
    return false;
  }
  return !sourceName || sourceName === 'generated';
}

function hasRealLlmProvenance(payload: Record<string, unknown>): boolean {
  const evidenceIds = payload.evidenceIds;
  return readString(payload.generatedBy).toUpperCase() === 'LLM'
    && readString(payload.contentOrigin).toUpperCase() === 'LLM'
    && Boolean(readString(payload.provider))
    && Boolean(readString(payload.model))
    && Boolean(readString(payload.agentName))
    && Array.isArray(evidenceIds)
    && payload.fallback === false
    && typeof payload.fromCache === 'boolean';
}

function readVideoResult(payload: Record<string, unknown>): VideoResult | null {
  const assetType = normalizeResourceType(readString(payload.assetType));
  if (assetType && assetType !== 'VIDEO') {
    return null;
  }
  const videoUrl = readString(payload.videoUrl) || readString(payload.finalVideoUrl) || readString(payload.downloadUrl);
  const renderStatus = readString(payload.audioBase64) && !videoUrl ? 'rendering' : undefined;
  if (!videoUrl && !renderStatus) {
    return null;
  }
  return {
    title: readString(payload.title) || '教学短视频',
    videoUrl,
    thumbnailUrl: readString(payload.thumbnailUrl) || readString(payload.thumbnailPath) || undefined,
    duration: readNumber(payload.durationSeconds),
    style: readVideoStyle(payload.videoStyle),
    knowledgePoint: readString(payload.knowledgePoint) || readString(payload.topic) || undefined,
    expiresHint: readString(payload.expiresAt) || undefined,
    fileName: readString(payload.fileName) || undefined,
    renderStatus,
    renderMessage: renderStatus ? '视频素材已生成，正在浏览器本地渲染' : undefined,
    audioBase64: readString(payload.audioBase64) || undefined,
    audioFormat: readString(payload.format) || readString(payload.audioFormat) || undefined,
    avatarDataUrl: readString(payload.avatarDataUrl) || undefined,
    renderTaskId: readString(payload.renderTaskId) || readString(payload.taskId) || undefined,
  };
}

function upsertResource(
  resources: ResourceGenerationResource[],
  item: ResourceGenerationResource,
): ResourceGenerationResource[] {
  const index = resources.findIndex((resource) => resource.id === item.id);
  if (index < 0) {
    return [...resources, item];
  }
  const next = [...resources];
  next[index] = {
    ...next[index],
    ...item,
    download: Object.prototype.hasOwnProperty.call(item, 'download') ? item.download : next[index].download,
    downloadFallback: Object.prototype.hasOwnProperty.call(item, 'downloadFallback') ? item.downloadFallback : next[index].downloadFallback,
    sourceAgent: item.sourceAgent ?? next[index].sourceAgent,
    inline: Object.prototype.hasOwnProperty.call(item, 'inline') ? item.inline : next[index].inline,
    slideOutline: Object.prototype.hasOwnProperty.call(item, 'slideOutline') ? item.slideOutline : next[index].slideOutline,
    quiz: Object.prototype.hasOwnProperty.call(item, 'quiz') ? item.quiz : next[index].quiz,
    video: Object.prototype.hasOwnProperty.call(item, 'video') ? item.video : next[index].video,
  };
  return next;
}

function loadAllSessions(): Record<string, ResourceGenerationSession> {
  if (typeof window === 'undefined') {
    return {};
  }
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '{}') as Record<string, ResourceGenerationSession>;
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(parsed).map(([key, session]) => [
        key,
        {
          ...session,
          conversationTriggered: Boolean(session.conversationTriggered || session.resources?.length),
          resources: (session.resources ?? [])
            .map(normalizeStoredResource)
            .filter((resource) => !isPendingSlideOutlineResource(resource)),
        },
      ]),
    );
  } catch {
    return {};
  }
}

function normalizeStoredResource(resource: ResourceGenerationResource): ResourceGenerationResource {
  if (resource.status) {
    return resource;
  }
  if (resource.quiz || resource.inline || resource.download || resource.video) {
    return { ...resource, status: 'ready' };
  }
  return { ...resource, status: 'generating' };
}

function isPendingSlideOutlineResource(resource: ResourceGenerationResource): boolean {
  return resource.type === 'SLIDES'
    && resource.status === 'waiting_confirmation'
    && Boolean(resource.slideOutline?.trim());
}

function saveAllSessions(sessions: Record<string, ResourceGenerationSession>): void {
  if (typeof window === 'undefined') {
    return;
  }
  const entries = Object.entries(sessions)
    .sort((left, right) => right[1].updatedAt - left[1].updatedAt)
    .slice(0, MAX_SESSIONS);
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(Object.fromEntries(entries)));
}

function createEmptySession(conversationId: string): ResourceGenerationSession {
  return {
    conversationId,
    taskStatus: 'idle',
    conversationTriggered: false,
    progress: 0,
    statusText: '等待在对话中触发资源生成',
    resources: [],
    updatedAt: Date.now(),
  };
}

function notifyResourceGenerationUpdated(conversationId: string): void {
  if (typeof window === 'undefined') {
    return;
  }
  window.dispatchEvent(new CustomEvent(RESOURCE_GENERATION_UPDATED_EVENT, { detail: { conversationId } }));
}

function userFacingProgressText(payload: Record<string, unknown>, artifactType: GeneratedResourceType | ''): string {
  const status = readString(payload.status).toUpperCase();
  if (status === 'FAILED') {
    return `${artifactType ? resourceLabel(artifactType) : '资源'}生成失败`;
  }
  if (artifactType) {
    return `正在生成${resourceLabel(artifactType)}`;
  }
  return readString(payload.message) || '多智能体正在协同生成资源';
}

function isResourceProgressEvent(payload: Record<string, unknown>): boolean {
  const stage = readString(payload.stage).toLowerCase();
  if (stage.includes('resource')) {
    return true;
  }
  if (normalizeResourceType(readString(payload.artifactType))) {
    return true;
  }
  return Boolean(payload.resourceTypes || payload.conversationTriggeredResourceGeneration);
}

function videoProgress(payload: Record<string, unknown>, fallback: number): number {
  return clampPercent(readNumber(payload.percent) ?? readNumber(payload.progress) ?? fallback);
}

function videoProgressText(eventName: string): string {
  return {
    'video_gen:start': '短视频任务已启动',
    'video_gen:script': '短视频脚本已生成',
    'video_gen:speech': '短视频语音已生成',
    'video_gen:avatar': '短视频正在本地渲染',
    'video_gen:complete': '短视频素材已就绪',
  }[eventName] ?? '短视频生成中';
}

function normalizeResourceType(value: string): GeneratedResourceType | '' {
  const normalized = value.trim().toUpperCase();
  if (normalized === 'PPT') {
    return 'SLIDES';
  }
  if (normalized === 'EXPLANATION') {
    return 'DOCUMENT';
  }
  if (normalized === 'CODE_CASE' || normalized === 'PRACTICAL_CASE') {
    return 'CODE';
  }
  if (['DOCUMENT', 'SLIDES', 'MINDMAP', 'QUIZ', 'READING', 'VIDEO', 'CODE'].includes(normalized)) {
    return normalized as GeneratedResourceType;
  }
  return '';
}

export function resourceLabel(type: GeneratedResourceType): string {
  return {
    DOCUMENT: '文档',
    SLIDES: 'PPT',
    MINDMAP: '思维导图',
    QUIZ: '练习题',
    READING: '阅读材料',
    VIDEO: '短视频',
    CODE: '代码案例',
  }[type];
}

function resourceId(type: GeneratedResourceType, title: string): string {
  return `${type}:${title.trim() || type}`;
}

function readString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function readNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

function clampPercent(value: number): number {
  return Math.max(0, Math.min(100, Math.round(value)));
}

function normalizeConversationId(conversationId: string): string {
  return conversationId.trim() || '__current__';
}

function readVideoStyle(value: unknown): VideoResult['style'] {
  const normalized = readString(value);
  return normalized === 'talking_head' || normalized === 'animation' || normalized === 'hybrid'
    ? normalized
    : undefined;
}

function defaultMimeType(type: GeneratedResourceType, inline: InlineResourceView | null): string {
  if (inline?.kind === 'code') {
    return inline.language === 'python' ? 'text/x-python;charset=utf-8' : 'text/plain;charset=utf-8';
  }
  if (inline?.kind === 'mermaid' || type === 'MINDMAP') {
    return 'text/plain;charset=utf-8';
  }
  if (type === 'QUIZ') {
    return 'application/json;charset=utf-8';
  }
  return 'text/markdown;charset=utf-8';
}

function defaultFileName(title: string, type: GeneratedResourceType, inline: InlineResourceView | null): string {
  const baseName = sanitizeFileName(title || resourceLabel(type));
  const extension = defaultFileExtension(type, inline);
  return baseName.toLowerCase().endsWith(`.${extension}`) ? baseName : `${baseName}.${extension}`;
}

function defaultFileExtension(type: GeneratedResourceType, inline: InlineResourceView | null): string {
  if (inline?.kind === 'code') {
    const language = (inline.language || '').toLowerCase();
    if (language === 'python' || language === 'py') {
      return 'py';
    }
    if (language === 'javascript' || language === 'js') {
      return 'js';
    }
    if (language === 'typescript' || language === 'ts') {
      return 'ts';
    }
    return 'txt';
  }
  if (inline?.kind === 'mermaid' || type === 'MINDMAP') {
    return 'mmd';
  }
  if (type === 'QUIZ') {
    return 'json';
  }
  return 'md';
}

function sanitizeFileName(value: string): string {
  return value
    .trim()
    .replace(/[\\/:*?"<>|]/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    || 'resource';
}
