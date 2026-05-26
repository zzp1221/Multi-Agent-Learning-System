import type { ConversationMessageItem } from '../api/conversation';
import {
  QNA_GREETING,
  type ChatMessage,
  type CompletedResourceView,
  type EngineService,
  type EngineState,
  type EngineTaskResultRecord,
  type EngineTaskSnapshot,
  type InlineResourceView,
  type QnaState,
} from './LearningStudioDemoPage.types';
import { sanitizeConversationMessageContent } from './LearningStudioDemoPage.utils';

export const ENGINE_TASK_STORAGE_KEY = 'learning_studio_engine_tasks';
export const QNA_SNAPSHOT_STORAGE_KEY = 'learning_studio_qna_snapshot';
export const QNA_CONVERSATION_CACHE_STORAGE_KEY = 'learning_studio_qna_cache';
export const SELECTED_CONVERSATION_STORAGE_KEY = 'learning_studio_selected_conversation';
export const ACTIVE_CONVERSATION_ID_STORAGE_KEY = 'learning_studio_active_conversation_id';
export const DEFAULT_ENGINE_SERVICE: EngineService = 'push';

export interface SelectedConversationSnapshot {
  conversationId: string;
  title?: string;
  lastMessagePreview?: string;
}

export interface PersistedEngineTaskSnapshot {
  selectedService: EngineService | null;
  conversationId: string;
  snapshots: Record<EngineService, EngineTaskSnapshot>;
}

export interface PersistedQnaSnapshot {
  conversationId: string;
  qnaInput: string;
  qnaState: QnaState;
  qnaMessages: ChatMessage[];
}

export interface PersistedConversationViewSnapshot {
  qnaInput: string;
  qnaMessages: ChatMessage[];
  qnaState?: QnaState;
}

export type QnaDrafts = Record<string, string>;
export type PersistedQnaConversationCache = Record<string, PersistedConversationViewSnapshot>;

export function mapConversationHistory(history: ConversationMessageItem[]): ChatMessage[] {
  const messages: ChatMessage[] = history
    .map((item, index) => ({
      id: item.messageId || `history-${index}`,
      role: (item.role === 'user' ? 'user' : 'assistant') as ChatMessage['role'],
      content: item.role === 'user'
        ? item.content?.trim() ?? ''
        : sanitizeConversationMessageContent(item.content ?? ''),
    }))
    .filter((item) => item.content);

  return messages.length > 0
    ? messages
    : [{ id: 'qna-greeting', role: 'assistant', content: QNA_GREETING }];
}

export function conversationCacheKey(conversationId: string): string {
  const normalized = conversationId.trim();
  return normalized || '__new__';
}

export function pickPreferredConversationMessages(
  cachedMessages: ChatMessage[] | undefined,
  fetchedMessages: ChatMessage[],
): ChatMessage[] {
  if (!cachedMessages || cachedMessages.length === 0) {
    return fetchedMessages;
  }
  const cachedTextLength = cachedMessages.reduce((sum, item) => sum + item.content.length, 0);
  const fetchedTextLength = fetchedMessages.reduce((sum, item) => sum + item.content.length, 0);
  if (cachedMessages.length > fetchedMessages.length || cachedTextLength > fetchedTextLength) {
    return cachedMessages;
  }
  return fetchedMessages;
}

export function isProcessingOnlyAssistantContent(content: string): boolean {
  const lines = content
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
  return lines.length > 0 && lines.every((line) => line.startsWith('[处理中]'));
}

export function hasPendingAssistantResponse(messages?: ChatMessage[]): boolean {
  const lastMessage = messages?.[messages.length - 1];
  if (!lastMessage || lastMessage.role !== 'assistant') {
    return false;
  }
  const content = lastMessage.content.trim();
  return !content || isProcessingOnlyAssistantContent(content);
}

export function hasResolvedAssistantResponse(messages: ChatMessage[]): boolean {
  const lastMessage = messages[messages.length - 1];
  return Boolean(
    lastMessage
      && lastMessage.role === 'assistant'
      && lastMessage.content.trim()
      && !isProcessingOnlyAssistantContent(lastMessage.content),
  );
}

export function createEmptyEngineTaskSnapshot(baseState: EngineState = 'ENGINE_IDLE'): EngineTaskSnapshot {
  return {
    engineState: baseState,
    taskId: '',
    taskProgress: 0,
    taskStatus: '未提交',
    taskSummary: '',
    serviceResultLines: [],
    downloadLinks: [],
    videoResult: null,
    inlineResource: null,
    inlineResources: [],
    practiceBatch: null,
    completedResources: [],
    judgeResult: null,
    resultHistory: [],
    selectedResultTaskId: '',
  };
}

export function createInitialEngineSnapshots(): Record<EngineService, EngineTaskSnapshot> {
  return {
    resource: createEmptyEngineTaskSnapshot(),
    path: createEmptyEngineTaskSnapshot(),
    push: createEmptyEngineTaskSnapshot(),
    assessment: createEmptyEngineTaskSnapshot(),
  };
}

export function hasLockedTask(snapshot: EngineTaskSnapshot): boolean {
  return snapshot.engineState === 'ENGINE_SUBMITTING' || snapshot.engineState === 'ENGINE_RUNNING';
}

function dedupeResultLines(lines: string[]): string[] {
  const seen = new Set<string>();
  const normalized: string[] = [];
  for (const raw of lines) {
    const line = String(raw).trim();
    if (!line || seen.has(line)) {
      continue;
    }
    seen.add(line);
    normalized.push(line);
  }
  return normalized;
}

export function getInlineResourcesFromSnapshot(snapshot: Partial<EngineTaskSnapshot>): InlineResourceView[] {
  const resources = Array.isArray(snapshot.inlineResources) ? snapshot.inlineResources.filter(Boolean) : [];
  if (snapshot.inlineResource && !resources.some((item) => item.kind === snapshot.inlineResource?.kind && item.title === snapshot.inlineResource?.title)) {
    resources.push(snapshot.inlineResource);
  }
  return resources;
}

export function createCompletedResourcesFromSnapshot(snapshot: Partial<EngineTaskSnapshot>): CompletedResourceView[] {
  if (Array.isArray(snapshot.completedResources) && snapshot.completedResources.length) {
    return snapshot.completedResources.filter(Boolean);
  }
  const completedResources: CompletedResourceView[] = getInlineResourcesFromSnapshot(snapshot).map((resource) => ({
    kind: 'inline',
    key: `inline:${resource.kind}:${resource.title}`,
    resource,
  }));
  if (snapshot.practiceBatch) {
    completedResources.push({
      kind: 'question_batch',
      key: `question_batch:${snapshot.practiceBatch.title}:${snapshot.practiceBatch.topic}`,
      batch: snapshot.practiceBatch,
    });
  }
  return completedResources;
}

function normalizeTaskResultRecord(record: Partial<EngineTaskResultRecord>): EngineTaskResultRecord | null {
  if (!record.taskId) {
    return null;
  }
  const now = Date.now();
  return {
    taskId: record.taskId,
    title: record.title || `任务 ${record.taskId.slice(0, 8)}`,
    taskStatus: record.taskStatus || '任务结果',
    engineState: record.engineState || 'ENGINE_COMPLETED',
    taskSummary: record.taskSummary || '',
    serviceResultLines: Array.isArray(record.serviceResultLines) ? record.serviceResultLines : [],
    downloadLinks: Array.isArray(record.downloadLinks) ? record.downloadLinks : [],
    videoResult: record.videoResult ?? null,
    inlineResources: Array.isArray(record.inlineResources) ? record.inlineResources : [],
    practiceBatch: record.practiceBatch ?? null,
    completedResources: createCompletedResourcesFromSnapshot(record),
    judgeResult: record.judgeResult ?? null,
    createdAt: typeof record.createdAt === 'number' ? record.createdAt : now,
    updatedAt: typeof record.updatedAt === 'number' ? record.updatedAt : now,
  };
}

function createTaskResultRecord(
  taskId: string,
  snapshot: EngineTaskSnapshot,
  overrides: Partial<EngineTaskResultRecord> = {},
): EngineTaskResultRecord {
  const now = Date.now();
  return {
    taskId,
    title: overrides.title || snapshot.taskSummary || snapshot.taskStatus || `任务 ${taskId.slice(0, 8)}`,
    taskStatus: overrides.taskStatus ?? snapshot.taskStatus,
    engineState: overrides.engineState ?? snapshot.engineState,
    taskSummary: overrides.taskSummary ?? snapshot.taskSummary,
    serviceResultLines: overrides.serviceResultLines ?? snapshot.serviceResultLines,
    downloadLinks: overrides.downloadLinks ?? snapshot.downloadLinks,
    videoResult: overrides.videoResult ?? snapshot.videoResult,
    inlineResources: overrides.inlineResources ?? getInlineResourcesFromSnapshot(snapshot),
    practiceBatch: overrides.practiceBatch ?? snapshot.practiceBatch,
    completedResources: overrides.completedResources ?? createCompletedResourcesFromSnapshot(snapshot),
    judgeResult: overrides.judgeResult ?? snapshot.judgeResult,
    createdAt: overrides.createdAt ?? now,
    updatedAt: overrides.updatedAt ?? now,
  };
}

function upsertTaskResultRecord(
  history: EngineTaskResultRecord[],
  taskId: string,
  snapshot: EngineTaskSnapshot,
  overrides: Partial<EngineTaskResultRecord> = {},
): EngineTaskResultRecord[] {
  if (!taskId) {
    return history;
  }
  const index = history.findIndex((item) => item.taskId === taskId);
  if (index < 0) {
    return [createTaskResultRecord(taskId, snapshot, overrides), ...history].slice(0, 8);
  }
  const current = history[index];
  const next: EngineTaskResultRecord = {
    ...current,
    ...overrides,
    taskId,
    title: overrides.title || current.title || snapshot.taskSummary || snapshot.taskStatus || `任务 ${taskId.slice(0, 8)}`,
    taskStatus: overrides.taskStatus ?? snapshot.taskStatus,
    engineState: overrides.engineState ?? snapshot.engineState,
    taskSummary: overrides.taskSummary ?? snapshot.taskSummary,
    serviceResultLines: overrides.serviceResultLines ?? snapshot.serviceResultLines,
    downloadLinks: overrides.downloadLinks ?? snapshot.downloadLinks,
    videoResult: overrides.videoResult ?? snapshot.videoResult,
    inlineResources: overrides.inlineResources ?? getInlineResourcesFromSnapshot(snapshot),
    practiceBatch: overrides.practiceBatch ?? snapshot.practiceBatch,
    completedResources: overrides.completedResources ?? createCompletedResourcesFromSnapshot(snapshot),
    judgeResult: overrides.judgeResult ?? snapshot.judgeResult,
    createdAt: current.createdAt,
    updatedAt: Date.now(),
  };
  return [next, ...history.slice(0, index), ...history.slice(index + 1)].slice(0, 8);
}

export function syncSnapshotResultRecord(snapshot: EngineTaskSnapshot): EngineTaskSnapshot {
  if (!snapshot.taskId) {
    return snapshot;
  }
  return {
    ...snapshot,
    resultHistory: upsertTaskResultRecord(snapshot.resultHistory, snapshot.taskId, snapshot),
    selectedResultTaskId: snapshot.selectedResultTaskId || snapshot.taskId,
  };
}

function sanitizeEngineSnapshot(snapshot: EngineTaskSnapshot): EngineTaskSnapshot {
  const normalized: EngineTaskSnapshot = {
    ...snapshot,
    serviceResultLines: dedupeResultLines(snapshot.serviceResultLines),
    inlineResources: getInlineResourcesFromSnapshot(snapshot),
    completedResources: createCompletedResourcesFromSnapshot(snapshot),
    resultHistory: Array.isArray(snapshot.resultHistory)
      ? snapshot.resultHistory.map(normalizeTaskResultRecord).filter((item): item is EngineTaskResultRecord => Boolean(item))
      : [],
    selectedResultTaskId: snapshot.selectedResultTaskId || snapshot.taskId || '',
  };
  if (normalized.engineState === 'ENGINE_SUBMITTING' && !normalized.taskId) {
    return createEmptyEngineTaskSnapshot('ENGINE_FORM_EDITING');
  }
  if (normalized.taskStatus === '任务已取消') {
    normalized.engineState = 'ENGINE_FAILED';
  }
  if (normalized.taskStatus === '任务完成') {
    normalized.engineState = 'ENGINE_COMPLETED';
  }
  return syncSnapshotResultRecord(normalized);
}

export function buildPersistedEngineSnapshots(
  selectedService: EngineService | null,
  snapshots: Record<EngineService, EngineTaskSnapshot>,
): Record<EngineService, EngineTaskSnapshot> {
  const next = createInitialEngineSnapshots();
  (Object.entries(snapshots) as Array<[EngineService, EngineTaskSnapshot]>).forEach(([service, snapshot]) => {
    const normalized = sanitizeEngineSnapshot(snapshot);
    const shouldPersist = service === selectedService || hasLockedTask(normalized);
    next[service] = shouldPersist ? normalized : createEmptyEngineTaskSnapshot();
  });
  return next;
}

export function normalizeRestoredQnaMessages(snapshot: PersistedQnaSnapshot): ChatMessage[] {
  if (!Array.isArray(snapshot.qnaMessages) || snapshot.qnaMessages.length === 0) {
    return [{ id: 'qna-greeting', role: 'assistant', content: QNA_GREETING }];
  }
  if (snapshot.qnaState !== 'QNA_STREAMING') {
    return snapshot.qnaMessages;
  }

  const normalized = [...snapshot.qnaMessages];
  const lastAssistantIndex = [...normalized]
    .map((item, index) => ({ item, index }))
    .reverse()
    .find(({ item }) => item.role === 'assistant' && !item.content.trim())?.index;

  if (lastAssistantIndex === undefined) {
    return normalized;
  }

  normalized[lastAssistantIndex] = {
    ...normalized[lastAssistantIndex],
    content: '上一条回复未完整加载，你可以继续提问，或重新发送上一条问题。',
  };
  return normalized;
}

export function buildConversationSyncSignature(messages: ChatMessage[]): string {
  return messages.map((item) => `${item.role}:${item.content}`).join('\u0001');
}
