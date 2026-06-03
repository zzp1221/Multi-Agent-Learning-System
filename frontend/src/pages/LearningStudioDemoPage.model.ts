import type { ConversationMessageItem } from '../api/conversation';
import {
  QNA_GREETING,
  type AgentTraceStepView,
  type ChatMessage,
  type CompletedResourceView,
  type CriticReviewView,
  type EngineService,
  type EngineState,
  type EngineTaskResultRecord,
  type EngineTaskSnapshot,
  type InlineResourceView,
  type LearningPlanView,
  type MasteryDiagnosisView,
  type QnaState,
  type ResourcePushPlanView,
} from './LearningStudioDemoPage.types';
import { formatUserFacingTaskMessage, sanitizeConversationMessageContent } from './LearningStudioDemoPage.utils';

export const ENGINE_TASK_STORAGE_KEY = 'learning_studio_engine_tasks';
export const QNA_SNAPSHOT_STORAGE_KEY = 'learning_studio_qna_snapshot';
export const QNA_CONVERSATION_CACHE_STORAGE_KEY = 'learning_studio_qna_cache';
export const SELECTED_CONVERSATION_STORAGE_KEY = 'learning_studio_selected_conversation';
export const ACTIVE_CONVERSATION_ID_STORAGE_KEY = 'learning_studio_active_conversation_id';
export const DEFAULT_ENGINE_SERVICE: EngineService = 'personalized';

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

const transientAssistantPlaceholders = [
  '上一条回复未完整加载',
  '回复已中断',
];

export function mapConversationHistory(history: ConversationMessageItem[]): ChatMessage[] {
  const messages: ChatMessage[] = history
    .map((item, index) => ({
      id: item.messageId || `history-${index}`,
      role: (item.role === 'user' ? 'user' : 'assistant') as ChatMessage['role'],
      content: item.role === 'user'
        ? item.content?.trim() ?? ''
        : sanitizeConversationMessageContent(item.content ?? ''),
    }))
    .filter((item) => item.content && !(item.role === 'assistant' && isTransientAssistantPlaceholder(item.content)));

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
    masteryDiagnosis: null,
    learningPlan: null,
    resourcePushPlan: null,
    criticReview: null,
    agentTrace: [],
    resultHistory: [],
    selectedResultTaskId: '',
  };
}

export function createInitialEngineSnapshots(): Record<EngineService, EngineTaskSnapshot> {
  return {
    resource: createEmptyEngineTaskSnapshot(),
    personalized: createEmptyEngineTaskSnapshot(),
    path: createEmptyEngineTaskSnapshot(),
    push: createEmptyEngineTaskSnapshot(),
  };
}

export function hasLockedTask(snapshot: EngineTaskSnapshot): boolean {
  return snapshot.engineState === 'ENGINE_SUBMITTING' || snapshot.engineState === 'ENGINE_RUNNING';
}

function dedupeResultLines(lines: string[]): string[] {
  const seen = new Set<string>();
  const normalized: string[] = [];
  for (const raw of lines) {
    const line = formatUserFacingTaskMessage(String(raw).trim());
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

function normalizeLearningPlan(value: unknown): LearningPlanView | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  const record = value as Partial<LearningPlanView>;
  const steps = Array.isArray(record.steps)
    ? record.steps
        .filter((item): item is LearningPlanView['steps'][number] => Boolean(item && typeof item === 'object'))
        .map((step) => ({
          stepId: String(step.stepId || ''),
          title: String(step.title || ''),
          order: typeof step.order === 'number' ? step.order : undefined,
          intent: step.intent ? String(step.intent) : undefined,
          reason: step.reason ? String(step.reason) : undefined,
          targetKnowledgePoints: Array.isArray(step.targetKnowledgePoints)
            ? step.targetKnowledgePoints.map((item) => String(item)).filter(Boolean)
            : [],
          preferredResourceTypes: Array.isArray(step.preferredResourceTypes)
            ? step.preferredResourceTypes.map((item) => String(item)).filter(Boolean)
            : [],
          estimatedMinutes: typeof step.estimatedMinutes === 'number' ? step.estimatedMinutes : undefined,
          checkpoint: step.checkpoint ? String(step.checkpoint) : undefined,
          agentName: step.agentName ? String(step.agentName) : undefined,
          serviceType: step.serviceType ? String(step.serviceType) : undefined,
          status: step.status ? String(step.status) : undefined,
          qualityGate: step.qualityGate ? String(step.qualityGate) : undefined,
        }))
        .filter((step) => step.stepId || step.title)
    : [];
  if (!record.planId && !record.goal && steps.length === 0) {
    return null;
  }
  return {
    planId: String(record.planId || ''),
    goal: String(record.goal || ''),
    status: record.status ? String(record.status) : undefined,
    createdBy: record.createdBy ? String(record.createdBy) : undefined,
    provider: record.provider ? String(record.provider) : undefined,
    model: record.model ? String(record.model) : undefined,
    steps,
  };
}

function normalizeMasteryDiagnosis(value: unknown): MasteryDiagnosisView | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  const record = value as Partial<MasteryDiagnosisView>;
  const targetScope = record.targetScope && typeof record.targetScope === 'object' && !Array.isArray(record.targetScope)
    ? record.targetScope
    : undefined;
  const knowledgeDiagnoses = Array.isArray(record.knowledgeDiagnoses)
    ? record.knowledgeDiagnoses
        .filter((item): item is MasteryDiagnosisView['knowledgeDiagnoses'][number] => Boolean(item && typeof item === 'object'))
        .map((item) => ({
          knowledgePoint: String(item.knowledgePoint || ''),
          masteryScore: typeof item.masteryScore === 'number' ? item.masteryScore : undefined,
          status: item.status ? String(item.status) : undefined,
          priority: typeof item.priority === 'number' ? item.priority : undefined,
          evidence: Array.isArray(item.evidence) ? item.evidence.map((text) => String(text)).filter(Boolean) : [],
          errorPatterns: Array.isArray(item.errorPatterns) ? item.errorPatterns.map((text) => String(text)).filter(Boolean) : [],
          nextFocus: item.nextFocus ? String(item.nextFocus) : undefined,
          recommendedResourceTypes: Array.isArray(item.recommendedResourceTypes)
            ? item.recommendedResourceTypes.map((text) => String(text)).filter(Boolean)
            : [],
        }))
        .filter((item) => item.knowledgePoint || item.evidence.length > 0)
    : [];
  if (!record.summaryText && !record.overallLevel && knowledgeDiagnoses.length === 0) {
    return null;
  }
  return {
    diagnosisSource: record.diagnosisSource ? String(record.diagnosisSource) : undefined,
    primaryDimension: record.primaryDimension ? String(record.primaryDimension) : undefined,
    overallLevel: record.overallLevel ? String(record.overallLevel) : undefined,
    overallMasteryScore: typeof record.overallMasteryScore === 'number' ? record.overallMasteryScore : undefined,
    confidence: typeof record.confidence === 'number' ? record.confidence : undefined,
    targetScope: targetScope
      ? {
          course: targetScope.course ? String(targetScope.course) : undefined,
          chapter: targetScope.chapter ? String(targetScope.chapter) : undefined,
          knowledgePoints: Array.isArray(targetScope.knowledgePoints)
            ? targetScope.knowledgePoints.map((item) => String(item)).filter(Boolean)
            : [],
        }
      : undefined,
    knowledgeDiagnoses,
    behaviorSignals: normalizeDiagnosisBehaviorSignals(record.behaviorSignals),
    planAdjustmentHints: normalizePlanAdjustmentHints(record.planAdjustmentHints),
    summaryText: record.summaryText ? String(record.summaryText) : undefined,
  };
}

function normalizeDiagnosisBehaviorSignals(value: unknown): MasteryDiagnosisView['behaviorSignals'] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return undefined;
  }
  const record = value as NonNullable<MasteryDiagnosisView['behaviorSignals']>;
  return {
    practiceAccuracy: typeof record.practiceAccuracy === 'number' ? record.practiceAccuracy : undefined,
    recentQuestionCount: typeof record.recentQuestionCount === 'number' ? record.recentQuestionCount : undefined,
    reviewCount: typeof record.reviewCount === 'number' ? record.reviewCount : undefined,
    resourceDownloads: typeof record.resourceDownloads === 'number' ? record.resourceDownloads : undefined,
    messageCount: typeof record.messageCount === 'number' ? record.messageCount : undefined,
    recentMistakeCount: typeof record.recentMistakeCount === 'number' ? record.recentMistakeCount : undefined,
  };
}

function normalizePlanAdjustmentHints(value: unknown): MasteryDiagnosisView['planAdjustmentHints'] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return undefined;
  }
  const record = value as NonNullable<MasteryDiagnosisView['planAdjustmentHints']>;
  return {
    shouldRefreshPlan: typeof record.shouldRefreshPlan === 'boolean' ? record.shouldRefreshPlan : undefined,
    refreshReason: record.refreshReason ? String(record.refreshReason) : undefined,
    strategy: record.strategy ? String(record.strategy) : undefined,
  };
}

function normalizeResourcePushPlan(value: unknown): ResourcePushPlanView | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  const record = value as Partial<ResourcePushPlanView>;
  const stepResources = Array.isArray(record.stepResources)
    ? record.stepResources
        .filter((item): item is ResourcePushPlanView['stepResources'][number] => Boolean(item && typeof item === 'object'))
        .map((step) => ({
          stepId: String(step.stepId || ''),
          stepTitle: step.stepTitle ? String(step.stepTitle) : undefined,
          targetKnowledgePoints: Array.isArray(step.targetKnowledgePoints)
            ? step.targetKnowledgePoints.map((item) => String(item)).filter(Boolean)
            : [],
          resources: Array.isArray(step.resources)
            ? step.resources
                .filter((item): item is ResourcePushPlanView['stepResources'][number]['resources'][number] => Boolean(item && typeof item === 'object'))
                .map((resource) => ({
                  title: String(resource.title || ''),
                  resourceType: String(resource.resourceType || ''),
                  source: String(resource.source || resource.sourceName || ''),
                  sourceName: resource.sourceName ? String(resource.sourceName) : undefined,
                  matchReason: resource.matchReason ? String(resource.matchReason) : undefined,
                  downloadUrl: resource.downloadUrl ? String(resource.downloadUrl) : undefined,
                  summaryText: resource.summaryText ? String(resource.summaryText) : undefined,
                }))
                .filter((resource) => resource.title || resource.summaryText || resource.downloadUrl)
            : [],
        }))
        .filter((step) => step.stepId || step.stepTitle || step.resources.length > 0)
    : [];
  const coverageGaps = Array.isArray(record.coverageGaps)
    ? record.coverageGaps
        .filter((item): item is ResourcePushPlanView['coverageGaps'][number] => Boolean(item && typeof item === 'object'))
        .map((gap) => ({
          stepId: String(gap.stepId || ''),
          missingResourceTypes: Array.isArray(gap.missingResourceTypes)
            ? gap.missingResourceTypes.map((item) => String(item)).filter(Boolean)
            : [],
          reason: gap.reason ? String(gap.reason) : undefined,
        }))
        .filter((gap) => gap.stepId || gap.missingResourceTypes.length > 0)
    : [];
  if (stepResources.length === 0 && coverageGaps.length === 0) {
    return null;
  }
  return {
    stepResources,
    coverageGaps,
  };
}

function normalizeCriticReview(value: unknown): CriticReviewView | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  const record = value as Partial<CriticReviewView>;
  const issues = Array.isArray(record.issues) ? record.issues.map((item) => String(item)).filter(Boolean) : [];
  const suggestions = Array.isArray(record.suggestions) ? record.suggestions.map((item) => String(item)).filter(Boolean) : [];
  if (!record.verdict && !record.summaryText && issues.length === 0 && suggestions.length === 0) {
    return null;
  }
  return {
    verdict: record.verdict ? String(record.verdict) : undefined,
    coverageScore: typeof record.coverageScore === 'number' ? record.coverageScore : undefined,
    pathOrderScore: typeof record.pathOrderScore === 'number' ? record.pathOrderScore : undefined,
    resourceMatchScore: typeof record.resourceMatchScore === 'number' ? record.resourceMatchScore : undefined,
    factConsistency: record.factConsistency ? String(record.factConsistency) : undefined,
    difficultyMatch: record.difficultyMatch ? String(record.difficultyMatch) : undefined,
    sourceCoverage: record.sourceCoverage ? String(record.sourceCoverage) : undefined,
    issues,
    suggestions,
    summaryText: record.summaryText ? String(record.summaryText) : undefined,
  };
}

function normalizeAgentTrace(value: unknown): AgentTraceStepView[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .filter((item): item is Partial<AgentTraceStepView> => Boolean(item && typeof item === 'object'))
    .map((item) => ({
      agentName: String(item.agentName || ''),
      status: String(item.status || ''),
      stage: item.stage ? String(item.stage) : undefined,
      message: item.message ? String(item.message) : undefined,
    }))
    .filter((item) => item.agentName || item.stage || item.message);
}

function normalizeTaskResultRecord(record: Partial<EngineTaskResultRecord>): EngineTaskResultRecord | null {
  if (!record.taskId) {
    return null;
  }
  const now = Date.now();
  const taskSummary = formatUserFacingTaskMessage(record.taskSummary || '');
  return {
    taskId: record.taskId,
    title: formatUserFacingTaskMessage(record.title || taskSummary) || `任务 ${record.taskId.slice(0, 8)}`,
    taskStatus: record.taskStatus || '任务结果',
    engineState: record.engineState || 'ENGINE_COMPLETED',
    taskSummary,
    serviceResultLines: Array.isArray(record.serviceResultLines) ? dedupeResultLines(record.serviceResultLines) : [],
    downloadLinks: Array.isArray(record.downloadLinks) ? record.downloadLinks : [],
    videoResult: record.videoResult ?? null,
    inlineResources: Array.isArray(record.inlineResources) ? record.inlineResources : [],
    practiceBatch: record.practiceBatch ?? null,
    completedResources: createCompletedResourcesFromSnapshot(record),
    judgeResult: record.judgeResult ?? null,
    masteryDiagnosis: normalizeMasteryDiagnosis(record.masteryDiagnosis),
    learningPlan: normalizeLearningPlan(record.learningPlan),
    resourcePushPlan: normalizeResourcePushPlan(record.resourcePushPlan),
    criticReview: normalizeCriticReview(record.criticReview),
    agentTrace: normalizeAgentTrace(record.agentTrace),
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
  const taskSummary = formatUserFacingTaskMessage(overrides.taskSummary ?? snapshot.taskSummary);
  return {
    taskId,
    title: formatUserFacingTaskMessage(overrides.title || taskSummary || snapshot.taskStatus) || `任务 ${taskId.slice(0, 8)}`,
    taskStatus: overrides.taskStatus ?? snapshot.taskStatus,
    engineState: overrides.engineState ?? snapshot.engineState,
    taskSummary,
    serviceResultLines: dedupeResultLines(overrides.serviceResultLines ?? snapshot.serviceResultLines),
    downloadLinks: overrides.downloadLinks ?? snapshot.downloadLinks,
    videoResult: overrides.videoResult ?? snapshot.videoResult,
    inlineResources: overrides.inlineResources ?? getInlineResourcesFromSnapshot(snapshot),
    practiceBatch: overrides.practiceBatch ?? snapshot.practiceBatch,
    completedResources: overrides.completedResources ?? createCompletedResourcesFromSnapshot(snapshot),
    judgeResult: overrides.judgeResult ?? snapshot.judgeResult,
    masteryDiagnosis: overrides.masteryDiagnosis ?? snapshot.masteryDiagnosis,
    learningPlan: overrides.learningPlan ?? snapshot.learningPlan,
    resourcePushPlan: overrides.resourcePushPlan ?? snapshot.resourcePushPlan,
    criticReview: overrides.criticReview ?? snapshot.criticReview,
    agentTrace: overrides.agentTrace ?? snapshot.agentTrace,
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
    title: formatUserFacingTaskMessage(overrides.title || current.title || snapshot.taskSummary || snapshot.taskStatus) || `任务 ${taskId.slice(0, 8)}`,
    taskStatus: overrides.taskStatus ?? snapshot.taskStatus,
    engineState: overrides.engineState ?? snapshot.engineState,
    taskSummary: formatUserFacingTaskMessage(overrides.taskSummary ?? snapshot.taskSummary),
    serviceResultLines: dedupeResultLines(overrides.serviceResultLines ?? snapshot.serviceResultLines),
    downloadLinks: overrides.downloadLinks ?? snapshot.downloadLinks,
    videoResult: overrides.videoResult ?? snapshot.videoResult,
    inlineResources: overrides.inlineResources ?? getInlineResourcesFromSnapshot(snapshot),
    practiceBatch: overrides.practiceBatch ?? snapshot.practiceBatch,
    completedResources: overrides.completedResources ?? createCompletedResourcesFromSnapshot(snapshot),
    judgeResult: overrides.judgeResult ?? snapshot.judgeResult,
    masteryDiagnosis: overrides.masteryDiagnosis ?? snapshot.masteryDiagnosis,
    learningPlan: overrides.learningPlan ?? snapshot.learningPlan,
    resourcePushPlan: overrides.resourcePushPlan ?? snapshot.resourcePushPlan,
    criticReview: overrides.criticReview ?? snapshot.criticReview,
    agentTrace: overrides.agentTrace ?? snapshot.agentTrace,
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
    masteryDiagnosis: normalizeMasteryDiagnosis(snapshot.masteryDiagnosis),
    learningPlan: normalizeLearningPlan(snapshot.learningPlan),
    resourcePushPlan: normalizeResourcePushPlan(snapshot.resourcePushPlan),
    criticReview: normalizeCriticReview(snapshot.criticReview),
    agentTrace: normalizeAgentTrace(snapshot.agentTrace),
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
  return snapshot.qnaMessages.filter((item, index) => {
    const isLastMessage = index === snapshot.qnaMessages.length - 1;
    if (item.role !== 'assistant') {
      return true;
    }
    if (isTransientAssistantPlaceholder(item.content)) {
      return false;
    }
    return !(snapshot.qnaState === 'QNA_STREAMING' && isLastMessage && !item.content.trim());
  });
}

export function buildConversationSyncSignature(messages: ChatMessage[]): string {
  return messages.map((item) => `${item.role}:${item.content}`).join('\u0001');
}

function isTransientAssistantPlaceholder(content: string): boolean {
  const normalized = content.trim();
  return transientAssistantPlaceholders.some((placeholder) => normalized.startsWith(placeholder));
}
