import type {
  ConversationStreamEventPayload,
  InlineResourceView,
  TempDownloadLink,
  VideoResult,
} from './LearningStudioDemoPage.types';

export type GeneratedResourceType = 'DOCUMENT' | 'SLIDES' | 'MINDMAP' | 'QUIZ' | 'READING' | 'VIDEO' | 'CODE';
export type ResourceGenerationResourceStatus = 'generating' | 'ready' | 'failed';

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
  quiz?: ResourceGenerationQuizSummary;
  video?: VideoResult;
  pptistSlides?: string;
  updatedAt: number;
}

export interface ResourceGenerationSession {
  conversationId: string;
  topic?: string;
  taskStatus: 'idle' | 'running' | 'completed' | 'partial_failed' | 'failed';
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
    const artifactType = normalizeResourceType(readPayloadString(payload, 'artifactType', 'artifact_type'));
    const status = readString(payload.status).toUpperCase();
    const failed = status === 'FAILED' || status === 'ERROR';
    const progressText = userFacingProgressText(payload, artifactType);
    next = {
      ...next,
      conversationTriggered: true,
      taskStatus: failed ? 'partial_failed' : 'running',
      progress,
      statusText: progressText,
      topic: next.topic || readPayloadString(payload, 'topic'),
      resources: artifactType && failed
        ? upsertResource(next.resources, {
          id: resourceId(artifactType, readPayloadString(payload, 'title') || resourceLabel(artifactType)),
          type: artifactType,
          title: readPayloadString(payload, 'title') || resourceLabel(artifactType),
          summary: readPayloadString(payload, 'message') || undefined,
          status: failed ? 'failed' : 'generating',
          statusText: progressText,
          progress,
          failureReason: failed ? readPayloadString(payload, 'message') || '生成失败' : undefined,
          sourceAgent: readPayloadString(payload, 'agentName', 'agent_name') || undefined,
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
      topic: next.topic || readPayloadString(payload, 'topic'),
    };
    if (eventName === 'video_gen:speech' || eventName === 'video_gen:complete') {
      const video = readVideoResult(payload);
      if (video) {
        next.resources = upsertResource(next.resources, {
          id: resourceId('VIDEO', video.title),
          type: 'VIDEO',
          title: video.title,
          summary: readPayloadString(payload, 'message'),
          sourceAgent: readPayloadString(payload, 'agentName', 'agent_name')
            || readPayloadString(payload, 'generatedBy', 'generated_by')
            || undefined,
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
      topic: next.topic || readPayloadString(payload, 'topic') || resource.title,
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
    return {
      ...next,
      taskStatus: failed ? 'failed' : partial ? 'partial_failed' : 'completed',
      progress: failed ? next.progress : 100,
      statusText: readString(payload.summary)
        || (failed ? '资源生成失败' : partial ? '资源部分完成' : '资源生成完成'),
      completedAt: now,
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
  const type = normalizeResourceType(readPayloadString(payload, 'assetType', 'asset_type'));
  if (!type) {
    return null;
  }
  const title = readPayloadString(payload, 'title') || readPayloadString(payload, 'fileName', 'file_name') || resourceLabel(type);
  const summary = readPayloadString(payload, 'summary');
  const downloadUrl = readPayloadString(payload, 'downloadUrl', 'download_url');
  const inline = readInlineResource(payload);
  const displayMode = readPayloadString(payload, 'displayMode', 'display_mode').toUpperCase();
  const isPptistEditor = type === 'SLIDES' && displayMode === 'PPTIST_EDITOR';
  const video = type === 'VIDEO' ? readVideoResult(payload) : null;
  const fileName = readPayloadString(payload, 'fileName', 'file_name') || defaultFileName(title, type, inline);
  const mimeType = readString(payload.mimeType) || defaultMimeType(type, inline);
  return {
    id: resourceId(type, title),
    type,
    title,
    summary,
    status: 'ready',
    statusText: '已生成',
    sourceAgent: readPayloadString(payload, 'agentName', 'agent_name')
      || readPayloadString(payload, 'generatedBy', 'generated_by')
      || readPayloadString(payload, 'sourceAgent', 'source_agent')
      || undefined,
    download: downloadUrl
      ? {
        title,
        url: downloadUrl,
        fileName,
        expiresHint: readPayloadString(payload, 'expiresAt', 'expires_at') || '临时下载链接',
        resourceType: type,
        mimeType,
        summary: summary || undefined,
        sourceName: readPayloadString(payload, 'sourceName', 'source_name') || undefined,
        thumbnailUrl: readPayloadString(payload, 'thumbnailUrl', 'thumbnail_url')
          || readPayloadString(payload, 'thumbnailPath', 'thumbnail_path')
          || undefined,
        duration: readPayloadNumber(payload, 'durationSeconds', 'duration_seconds'),
        knowledgePoint: readPayloadString(payload, 'knowledgePoint', 'knowledge_point') || undefined,
      }
      : undefined,
    downloadFallback: undefined,
    inline: isPptistEditor ? undefined : inline ?? undefined,
    pptistSlides: isPptistEditor ? readPayloadString(payload, 'inlineContent', 'inline_content') || undefined : undefined,
    video: video ?? undefined,
    updatedAt: now,
  };
}

function readInlineResource(payload: Record<string, unknown>): InlineResourceView | null {
  const inlineContent = readPayloadString(payload, 'inlineContent', 'inline_content');
  if (!inlineContent) {
    return null;
  }
  const displayMode = readPayloadString(payload, 'displayMode', 'display_mode').toUpperCase();
  const assetType = normalizeResourceType(readPayloadString(payload, 'assetType', 'asset_type'));
  const mimeType = readPayloadString(payload, 'mimeType', 'mime_type').toLowerCase();
  const title = readPayloadString(payload, 'title') || resourceLabel(assetType || 'DOCUMENT');
  const base = {
    title,
    summary: readPayloadString(payload, 'summary') || undefined,
    content: inlineContent,
  };
  if (assetType === 'MINDMAP' || displayMode === 'MERMAID' || inlineContent.trim().toLowerCase().startsWith('mindmap')) {
    return { ...base, kind: 'mermaid' };
  }
  if (assetType === 'CODE' || mimeType.includes('python') || mimeType.includes('javascript') || mimeType.includes('sql')) {
    return {
      ...base,
      kind: 'code',
      language: readString(payload.language) || 'text',
      explanation: readPayloadString(payload, 'explanation') || undefined,
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
    title: readPayloadString(payload, 'title') || '练习题',
    topic: readPayloadString(payload, 'topic'),
    difficulty: readPayloadString(payload, 'difficulty'),
    description: readPayloadString(payload, 'description') || undefined,
    generatedBy: readPayloadString(payload, 'generatedBy', 'generated_by') || undefined,
    agentName: readPayloadString(payload, 'agentName', 'agent_name') || undefined,
    questionCount: questions.length,
  };
}

function hasAllowedResourceProvenance(payload: Record<string, unknown>): boolean {
  if (!requiresLlmProvenance(payload)) {
    return true;
  }
  return hasRealLlmProvenance(payload);
}

function requiresLlmProvenance(payload: Record<string, unknown>): boolean {
  const displayMode = readPayloadString(payload, 'displayMode', 'display_mode').toLowerCase();
  const sourceName = readPayloadString(payload, 'sourceName', 'source_name').toLowerCase();
  if (displayMode === 'external_link') {
    return false;
  }
  return !sourceName || sourceName === 'generated';
}

function hasRealLlmProvenance(payload: Record<string, unknown>): boolean {
  const evidenceIds = payload.evidenceIds;
  return readPayloadString(payload, 'generatedBy', 'generated_by').toUpperCase() === 'LLM'
    && readPayloadString(payload, 'contentOrigin', 'content_origin').toUpperCase() === 'LLM'
    && Boolean(readString(payload.provider))
    && Boolean(readString(payload.model))
    && Boolean(readPayloadString(payload, 'agentName', 'agent_name'))
    && Array.isArray(evidenceIds)
    && payload.fallback === false
    && typeof payload.fromCache === 'boolean';
}

function readVideoResult(payload: Record<string, unknown>): VideoResult | null {
  const assetType = normalizeResourceType(readPayloadString(payload, 'assetType', 'asset_type'));
  if (assetType && assetType !== 'VIDEO') {
    return null;
  }
  const videoUrl = readPayloadString(payload, 'videoUrl', 'video_url')
    || readPayloadString(payload, 'finalVideoUrl', 'final_video_url')
    || readPayloadString(payload, 'downloadUrl', 'download_url');
  const audioBase64 = readPayloadString(payload, 'audioBase64', 'audio_base64');
  const renderStatus = audioBase64 && !videoUrl ? 'rendering' : undefined;
  if (!videoUrl && !renderStatus) {
    return null;
  }
  return {
    title: readPayloadString(payload, 'title') || '教学短视频',
    videoUrl,
    thumbnailUrl: readPayloadString(payload, 'thumbnailUrl', 'thumbnail_url')
      || readPayloadString(payload, 'thumbnailPath', 'thumbnail_path')
      || undefined,
    duration: readPayloadNumber(payload, 'durationSeconds', 'duration_seconds'),
    style: readVideoStyle(readPayloadString(payload, 'videoStyle', 'video_style')),
    knowledgePoint: readPayloadString(payload, 'knowledgePoint', 'knowledge_point')
      || readPayloadString(payload, 'topic')
      || undefined,
    expiresHint: readPayloadString(payload, 'expiresAt', 'expires_at') || undefined,
    fileName: readPayloadString(payload, 'fileName', 'file_name') || undefined,
    renderStatus,
    renderMessage: renderStatus ? '视频素材已生成，正在生成视频' : undefined,
    audioBase64: audioBase64 || undefined,
    audioFormat: readPayloadString(payload, 'format') || readPayloadString(payload, 'audioFormat', 'audio_format') || undefined,
    avatarDataUrl: readPayloadString(payload, 'avatarDataUrl', 'avatar_data_url') || undefined,
    renderTaskId: readPayloadString(payload, 'renderTaskId', 'render_task_id')
      || readPayloadString(payload, 'taskId', 'task_id')
      || undefined,
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
            .map(normalizeStoredResource),
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
    'video_gen:avatar': '短视频正在生成',
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

function readPayloadString(payload: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = readString(payload[key]);
    if (value) {
      return value;
    }
  }
  return '';
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

function readPayloadNumber(payload: Record<string, unknown>, ...keys: string[]): number | undefined {
  for (const key of keys) {
    const value = readNumber(payload[key]);
    if (value !== undefined) {
      return value;
    }
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
    if (language === 'sql') {
      return 'sql';
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
