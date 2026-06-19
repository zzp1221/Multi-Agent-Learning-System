import type {
  ConversationStreamEventPayload,
  InlineResourceView,
  TempDownloadLink,
  VideoResult,
} from './LearningStudioDemoPage.types';
import { AUTH_USER_STORAGE_KEY } from '../api/request';
import { sanitizeMarkdownContent } from '../utils/markdownSanitizer';

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
  resourceType?: GeneratedResourceType;
  taskId?: string;
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
  downloadUrl?: string;
  expiresAt?: string;
  sourceAgent?: string;
  inline?: InlineResourceView;
  quiz?: ResourceGenerationQuizSummary;
  video?: VideoResult;
  pptistSlides?: string;
  updatedAt: number;
}

export interface ResourceGenerationSession {
  conversationId: string;
  ownerUserId?: string;
  taskId?: string;
  resourceType?: GeneratedResourceType;
  title?: string;
  topic?: string;
  taskStatus: 'idle' | 'running' | 'completed' | 'partial_failed' | 'failed';
  status?: 'idle' | 'running' | 'completed' | 'partial_failed' | 'failed';
  summary?: string;
  downloadUrl?: string;
  expiresAt?: string;
  conversationTriggered: boolean;
  progress: number;
  statusText: string;
  resources: ResourceGenerationResource[];
  completedAt?: number;
  updatedAt: number;
}

export const RESOURCE_GENERATION_UPDATED_EVENT = 'app:resource-generation-updated';
const STORAGE_KEY = 'learning_studio_conversation_resources';
const LAST_SESSION_STORAGE_KEY = 'learning_studio_last_resource_session';
const MAX_SESSIONS = 20;
const ANONYMOUS_OWNER_ID = '__anonymous__';

export function loadResourceGenerationSession(conversationId: string): ResourceGenerationSession {
  const ownerUserId = currentResourceOwnerId();
  const normalizedId = resolveConversationIdForLoad(conversationId, ownerUserId);
  const all = loadAllSessions();
  return all[sessionStorageKey(ownerUserId, normalizedId)] ?? createEmptySession(normalizedId, ownerUserId);
}

export function recordConversationResourceEvent(
  conversationId: string,
  eventName: string,
  data: ConversationStreamEventPayload,
): ResourceGenerationSession {
  const ownerUserId = currentResourceOwnerId();
  const normalizedId = normalizeConversationId(conversationId);
  const all = loadAllSessions();
  const key = sessionStorageKey(ownerUserId, normalizedId);
  const current = all[key] ?? createEmptySession(normalizedId, ownerUserId);
  const next = reduceResourceEvent(current, eventName, data, ownerUserId);
  all[key] = next;
  saveAllSessions(all);
  if (next.conversationTriggered || next.resources.length > 0) {
    saveLastSessionPointer(ownerUserId, normalizedId);
  }
  notifyResourceGenerationUpdated(normalizedId);
  return next;
}

export function updateResourceVideoRenderResult(
  conversationId: string,
  resourceIdValue: string,
  videoPatch: Partial<VideoResult>,
  statusText?: string,
): ResourceGenerationSession {
  const ownerUserId = currentResourceOwnerId();
  const normalizedId = normalizeConversationId(conversationId);
  const all = loadAllSessions();
  const key = sessionStorageKey(ownerUserId, normalizedId);
  const current = all[key] ?? createEmptySession(normalizedId, ownerUserId);
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
    ownerUserId,
    resources,
    updatedAt: now,
  };
  all[key] = next;
  saveAllSessions(all);
  saveLastSessionPointer(ownerUserId, normalizedId);
  notifyResourceGenerationUpdated(normalizedId);
  return next;
}

function reduceResourceEvent(
  session: ResourceGenerationSession,
  eventName: string,
  data: ConversationStreamEventPayload,
  ownerUserId: string,
): ResourceGenerationSession {
  const payload = data.payload ?? {};
  const now = Date.now();
  const eventTaskId = readPayloadString(data as unknown as Record<string, unknown>, 'taskId', 'task_id')
    || readPayloadString(payload, 'taskId', 'task_id')
    || session.taskId;
  let next: ResourceGenerationSession = {
    ...session,
    ownerUserId,
    taskId: eventTaskId,
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
      status: failed ? 'partial_failed' : 'running',
      resourceType: artifactType || next.resourceType,
      title: readPayloadString(payload, 'title') || next.title,
      progress,
      statusText: progressText,
      topic: next.topic || readPayloadString(payload, 'topic'),
      resources: artifactType && failed
        ? upsertResource(next.resources, {
          id: resourceId(artifactType, readPayloadString(payload, 'title') || resourceLabel(artifactType)),
          type: artifactType,
          resourceType: artifactType,
          taskId: eventTaskId,
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
      status: 'running',
      resourceType: 'VIDEO',
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
          resourceType: 'VIDEO',
          taskId: eventTaskId,
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
      status: 'running',
      resourceType: resource.type,
      title: resource.title,
      summary: resource.summary || next.summary,
      downloadUrl: resource.download?.url || resource.downloadUrl || next.downloadUrl,
      expiresAt: resource.expiresAt || next.expiresAt,
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
      status: 'running',
      resourceType: 'QUIZ',
      title: quiz.title,
      summary: quiz.description || `${quiz.questionCount} 道练习题已生成`,
      progress: next.progress,
      statusText: '练习题已进入答题助手',
      topic: next.topic || quiz.topic,
      resources: upsertResource(next.resources, {
        id: resourceId('QUIZ', quiz.title),
        type: 'QUIZ',
        resourceType: 'QUIZ',
        taskId: eventTaskId,
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
      status: failed ? 'failed' : partial ? 'partial_failed' : 'completed',
      progress: failed ? next.progress : 100,
      statusText: readString(payload.summary)
        || (failed ? '资源生成失败' : partial ? '资源部分完成' : '资源生成完成'),
      summary: readString(payload.summary) || next.summary,
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
      status: 'failed',
      statusText: readString(payload.message) || '资源生成失败',
      summary: readString(payload.message) || next.summary,
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
  if (isLegacySlideOutlineResource({
    type,
    title,
    summary,
    displayMode,
    inline,
    downloadUrl,
    pptistSlides: isPptistEditor ? readPayloadString(payload, 'inlineContent', 'inline_content') || undefined : undefined,
  })) {
    return null;
  }
  const video = type === 'VIDEO' ? readVideoResult(payload) : null;
  const fileName = readPayloadString(payload, 'fileName', 'file_name') || defaultFileName(title, type, inline);
  const mimeType = readString(payload.mimeType) || defaultMimeType(type, inline);
  return {
    id: resourceId(type, title),
    type,
    resourceType: type,
    taskId: readPayloadString(payload, 'taskId', 'task_id') || undefined,
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
    downloadUrl: downloadUrl || undefined,
    expiresAt: readPayloadString(payload, 'expiresAt', 'expires_at') || undefined,
    downloadFallback: undefined,
    inline: isPptistEditor ? undefined : inline ?? undefined,
    pptistSlides: isPptistEditor ? readPayloadString(payload, 'inlineContent', 'inline_content') || undefined : undefined,
    video: video ?? undefined,
    updatedAt: now,
  };
}

function readInlineResource(payload: Record<string, unknown>): InlineResourceView | null {
  const rawInlineContent = readPayloadString(payload, 'inlineContent', 'inline_content');
  const displayMode = readPayloadString(payload, 'displayMode', 'display_mode').toUpperCase();
  const assetType = normalizeResourceType(readPayloadString(payload, 'assetType', 'asset_type'));
  const mimeType = readPayloadString(payload, 'mimeType', 'mime_type').toLowerCase();
  const inlineContent = assetType === 'CODE' || mimeType.includes('python') || mimeType.includes('javascript') || mimeType.includes('sql')
    ? rawInlineContent.replace(/\r\n/g, '\n')
    : sanitizeMarkdownContent(rawInlineContent);
  if (!inlineContent) {
    return null;
  }
  const title = readPayloadString(payload, 'title') || resourceLabel(assetType || 'DOCUMENT');
  const base = {
    title,
    summary: sanitizeMarkdownContent(readPayloadString(payload, 'summary')) || undefined,
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
  const ownerUserId = currentResourceOwnerId();
  return Object.fromEntries(
    Object.entries(loadStoredSessions(ownerUserId))
      .filter(([, session]) => session.ownerUserId === ownerUserId),
  );
}

function loadStoredSessions(fallbackOwnerUserId: string): Record<string, ResourceGenerationSession> {
  if (typeof window === 'undefined') {
    return {};
  }
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '{}') as Record<string, ResourceGenerationSession>;
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return {};
    }
    const sessions = Object.fromEntries(
      Object.entries(parsed)
        .map(([, session]) => {
          const normalizedSession = normalizeStoredSession(session, fallbackOwnerUserId);
          return [sessionStorageKey(normalizedSession.ownerUserId || fallbackOwnerUserId, normalizedSession.conversationId), normalizedSession] as const;
        })
        .filter(([_key], index, entries) => entries.findIndex(([otherKey]) => otherKey === _key) === index),
    );
    if (hasStoredSessionMigration(parsed, sessions)) {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(Object.fromEntries(pruneStoredSessions(sessions))));
    }
    return sessions;
  } catch {
    return {};
  }
}

function normalizeStoredSession(
  session: ResourceGenerationSession,
  fallbackOwnerUserId: string,
): ResourceGenerationSession {
  const ownerUserId = readString(session.ownerUserId) || fallbackOwnerUserId;
  const conversationId = normalizeConversationId(session.conversationId);
  const resources = (session.resources ?? [])
    .map(normalizeStoredResource)
    .filter((resource) => !isLegacySlideOutlineResource(resource));
  const taskStatus = normalizeStoredTaskStatus(session.taskStatus || session.status, resources);
  return {
    ...session,
    ownerUserId,
    conversationId,
    taskStatus,
    status: taskStatus,
    conversationTriggered: Boolean(session.conversationTriggered || resources.length),
    progress: clampPercent(readNumber(session.progress) ?? (taskStatus === 'completed' ? 100 : 0)),
    statusText: readString(session.statusText) || defaultSessionStatusText(taskStatus),
    resources,
    updatedAt: readNumber(session.updatedAt) ?? Date.now(),
  };
}

function normalizeStoredResource(resource: ResourceGenerationResource): ResourceGenerationResource {
  const type = normalizeResourceType(readString(resource.type || resource.resourceType)) || 'DOCUMENT';
  const downloadUrl = resource.downloadUrl || resource.download?.url;
  const normalized = {
    ...resource,
    type,
    resourceType: resource.resourceType || type,
    downloadUrl,
    expiresAt: resource.expiresAt || resource.download?.expiresHint,
  };
  if (normalized.status) {
    return normalized;
  }
  if (normalized.quiz || normalized.inline || normalized.download || normalized.video || normalized.downloadUrl || normalized.pptistSlides) {
    return { ...normalized, status: 'ready' };
  }
  return { ...normalized, status: 'generating' };
}

function hasStoredSessionMigration(
  rawSessions: Record<string, ResourceGenerationSession>,
  normalizedSessions: Record<string, ResourceGenerationSession>,
): boolean {
  const rawResourceCount = Object.values(rawSessions).reduce(
    (count, session) => count + (Array.isArray(session.resources) ? session.resources.length : 0),
    0,
  );
  const normalizedResourceCount = Object.values(normalizedSessions).reduce(
    (count, session) => count + session.resources.length,
    0,
  );
  return normalizedResourceCount < rawResourceCount;
}

function isLegacySlideOutlineResource(resource: {
  type?: GeneratedResourceType | string;
  title?: string;
  summary?: string;
  statusText?: string;
  displayMode?: string;
  inline?: InlineResourceView | null;
  download?: TempDownloadLink;
  downloadUrl?: string;
  pptistSlides?: string;
}): boolean {
  const type = normalizeResourceType(readString(resource.type)) || normalizeResourceType(readString((resource as { resourceType?: string }).resourceType));
  if (type !== 'SLIDES') {
    return false;
  }
  const displayMode = readString(resource.displayMode).toUpperCase();
  if (displayMode === 'SLIDE_OUTLINE_CONFIRMATION') {
    return true;
  }
  if (resource.pptistSlides || resource.download || resource.downloadUrl) {
    return false;
  }
  const text = [
    resource.title,
    resource.summary,
    resource.statusText,
    resource.inline?.title,
    resource.inline?.summary,
    resource.inline?.content,
  ].map((value) => readString(value)).join('\n');
  return /(PPT\s*大纲|PPT大纲|大纲已生成|等待.*确认|确认后.*生成|等待用户确认)/i.test(text);
}

function saveAllSessions(sessions: Record<string, ResourceGenerationSession>): void {
  if (typeof window === 'undefined') {
    return;
  }
  const ownerUserId = currentResourceOwnerId();
  const storedSessions = loadStoredSessions(ownerUserId);
  const mergedSessions = {
    ...Object.fromEntries(
      Object.entries(storedSessions).filter(([, session]) => session.ownerUserId !== ownerUserId),
    ),
    ...sessions,
  };
  const entries = pruneStoredSessions(mergedSessions);
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(Object.fromEntries(entries)));
}

function pruneStoredSessions(
  sessions: Record<string, ResourceGenerationSession>,
): [string, ResourceGenerationSession][] {
  const grouped = new Map<string, [string, ResourceGenerationSession][]>();
  for (const entry of Object.entries(sessions)) {
    const ownerUserId = entry[1].ownerUserId || ANONYMOUS_OWNER_ID;
    grouped.set(ownerUserId, [...(grouped.get(ownerUserId) ?? []), entry]);
  }
  return [...grouped.values()].flatMap((entries) =>
    entries
      .sort((left, right) => right[1].updatedAt - left[1].updatedAt)
      .slice(0, MAX_SESSIONS),
  );
}

function createEmptySession(conversationId: string, ownerUserId = currentResourceOwnerId()): ResourceGenerationSession {
  return {
    conversationId,
    ownerUserId,
    taskStatus: 'idle',
    status: 'idle',
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

function resolveConversationIdForLoad(conversationId: string, ownerUserId: string): string {
  const normalizedId = conversationId.trim();
  if (normalizedId) {
    return normalizeConversationId(normalizedId);
  }
  const lastConversationId = loadLastSessionPointer(ownerUserId);
  if (lastConversationId) {
    return lastConversationId;
  }
  const recentSession = latestNonEmptySession(ownerUserId);
  return recentSession?.conversationId ?? normalizeConversationId('');
}

function latestNonEmptySession(ownerUserId: string): ResourceGenerationSession | null {
  const sessions = Object.values(loadAllSessions())
    .filter((session) =>
      session.ownerUserId === ownerUserId
      && session.conversationTriggered
      && session.resources.length > 0
    )
    .sort((left, right) => right.updatedAt - left.updatedAt);
  return sessions[0] ?? null;
}

function loadLastSessionPointer(ownerUserId: string): string {
  if (typeof window === 'undefined') {
    return '';
  }
  try {
    const parsed = JSON.parse(window.localStorage.getItem(LAST_SESSION_STORAGE_KEY) || '{}') as Record<string, string>;
    const conversationId = parsed?.[ownerUserId];
    return typeof conversationId === 'string' ? normalizeConversationId(conversationId) : '';
  } catch {
    return '';
  }
}

function saveLastSessionPointer(ownerUserId: string, conversationId: string): void {
  if (typeof window === 'undefined') {
    return;
  }
  let parsed: Record<string, string> = {};
  try {
    parsed = JSON.parse(window.localStorage.getItem(LAST_SESSION_STORAGE_KEY) || '{}') as Record<string, string>;
  } catch {
    parsed = {};
  }
  parsed[ownerUserId] = normalizeConversationId(conversationId);
  window.localStorage.setItem(LAST_SESSION_STORAGE_KEY, JSON.stringify(parsed));
}

function currentResourceOwnerId(): string {
  if (typeof window === 'undefined') {
    return ANONYMOUS_OWNER_ID;
  }
  try {
    const parsed = JSON.parse(window.localStorage.getItem(AUTH_USER_STORAGE_KEY) || 'null') as {
      id?: number | string;
      userId?: number | string;
    } | null;
    const rawId = parsed?.userId ?? parsed?.id;
    if (rawId !== undefined && rawId !== null && String(rawId).trim()) {
      return String(rawId).trim();
    }
  } catch {
    return ANONYMOUS_OWNER_ID;
  }
  return ANONYMOUS_OWNER_ID;
}

function sessionStorageKey(ownerUserId: string, conversationId: string): string {
  return `${ownerUserId}::${normalizeConversationId(conversationId)}`;
}

function normalizeStoredTaskStatus(
  value: unknown,
  resources: ResourceGenerationResource[],
): ResourceGenerationSession['taskStatus'] {
  const normalized = readString(value).toLowerCase();
  if (normalized === 'running' || normalized === 'completed' || normalized === 'partial_failed' || normalized === 'failed') {
    return normalized;
  }
  if (resources.some((resource) => resource.status === 'failed')) {
    return resources.some((resource) => resource.status === 'ready') ? 'partial_failed' : 'failed';
  }
  if (resources.some((resource) => resource.status === 'ready')) {
    return 'completed';
  }
  return 'idle';
}

function defaultSessionStatusText(status: ResourceGenerationSession['taskStatus']): string {
  return {
    idle: '等待在对话中触发资源生成',
    running: '资源生成中',
    completed: '资源生成完成',
    partial_failed: '资源部分完成',
    failed: '资源生成失败',
  }[status];
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
