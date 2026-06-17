import {
  readRecord as readSharedRecord,
  readString as readSharedString,
  readStringArray as readSharedStringArray,
} from '../utils/valueReaders';
import type {
  AgentTraceStepView,
  CriticReviewView,
  LearningPlanView,
  MasteryDiagnosisView,
  PracticeJudgeResult,
  PracticeQuestionBatch,
  ResourcePushPlanView,
} from './LearningStudioDemoPage.types';

function readString(value: unknown): string {
  return readSharedString(value);
}

function readRecord(value: unknown): Record<string, unknown> | null {
  return readSharedRecord(value);
}

function readStringArray(...values: unknown[]): string[] {
  return readSharedStringArray(...values);
}
export function readPracticeQuestionBatch(payload: Record<string, unknown> | undefined): PracticeQuestionBatch | null {
  const record = readRecord(payload);
  const source = readRecord(record?.practiceQuestionBatch)
    ?? readRecord(record?.questionBatch)
    ?? record;
  const questions = Array.isArray(source?.questions) ? source.questions : null;
  if (!source || !questions) {
    return null;
  }
  return {
    title: readString(source.title) || '练习题',
    topic: readString(source.topic),
    difficulty: readString(source.difficulty),
    description: readString(source.description),
    assessmentDimension: readString(source.assessmentDimension),
    submitLabel: readString(source.submitLabel),
    generatedBy: readString(source.generatedBy),
    contentOrigin: readString(source.contentOrigin),
    provider: readString(source.provider),
    model: readString(source.model),
    agentName: readString(source.agentName),
    evidenceIds: Array.isArray(source.evidenceIds) ? source.evidenceIds.map((id) => readString(id)).filter(Boolean) : undefined,
    fallback: typeof source.fallback === 'boolean' ? source.fallback : undefined,
    fromCache: typeof source.fromCache === 'boolean' ? source.fromCache : undefined,
    questions: questions
      .map((item) => readRecord(item))
      .filter((item): item is Record<string, unknown> => Boolean(item))
      .map((item, index) => ({
        questionId: readString(item.questionId) || `question-${index + 1}`,
        questionType: readString(item.questionType) || 'SHORT_ANSWER',
        stem: readString(item.stem),
        options: Array.isArray(item.options) ? item.options.map((option) => readString(option)).filter(Boolean) : undefined,
        answer: readString(item.answer),
        knowledgeTags: Array.isArray(item.knowledgeTags) ? item.knowledgeTags.map((tag) => readString(tag)).filter(Boolean) : undefined,
        difficultyLevel: readString(item.difficultyLevel),
        explanation: readString(item.explanation),
      })),
  };
}

export function readPracticeJudgeResult(payload: Record<string, unknown> | undefined): PracticeJudgeResult | null {
  const record = readRecord(payload);
  const source = readRecord(record?.judgeResult) ?? record;
  const items = Array.isArray(source?.items) ? source.items : null;
  if (!source || !items) {
    return null;
  }
  return {
    title: readString(source.title) || '判题结果',
    summary: readString(source.summary),
    totalScore: readNumericRaw(source.totalScore) ?? 0,
    accuracy: readNumericRaw(source.accuracy) ?? 0,
    weakKnowledgeTags: Array.isArray(source.weakKnowledgeTags)
      ? source.weakKnowledgeTags.map((tag) => readString(tag)).filter(Boolean)
      : undefined,
    items: items
      .map((item) => readRecord(item))
      .filter((item): item is Record<string, unknown> => Boolean(item))
      .map((item) => ({
        questionId: readString(item.questionId),
        questionType: readString(item.questionType),
        learnerAnswer: readString(item.learnerAnswer),
        correctAnswer: readString(item.correctAnswer),
        isCorrect: Boolean(item.isCorrect),
        score: readNumericRaw(item.score) ?? 0,
        knowledgeTags: Array.isArray(item.knowledgeTags) ? item.knowledgeTags.map((tag) => readString(tag)).filter(Boolean) : undefined,
        reason: readString(item.reason),
        feedback: readString(item.feedback),
      })),
  };
}

export function readLearningPlan(payload: Record<string, unknown> | undefined): LearningPlanView | null {
  const record = readRecord(payload?.learningPlan) ?? readRecord(payload?.learningPath);
  if (!record) {
    return null;
  }
  const rawSteps = Array.isArray(record.steps) ? record.steps : [];
  const steps = rawSteps
    .map((item) => readRecord(item))
    .filter((item): item is Record<string, unknown> => Boolean(item))
    .map((item) => ({
      stepId: readString(item.stepId),
      title: readString(item.title),
      order: readNumericRaw(item.order),
      intent: readString(item.intent) || readString(item.objective) || readString(item.reason) || undefined,
      reason: readString(item.reason) || undefined,
      targetKnowledgePoints: readStringArray(item.targetKnowledgePoints),
      preferredResourceTypes: readStringArray(item.preferredResourceTypes),
      estimatedMinutes: readNumericRaw(item.estimatedMinutes),
      checkpoint: readString(item.checkpoint) || undefined,
      agentName: readString(item.agentName) || undefined,
      serviceType: readString(item.serviceType) || undefined,
      status: readString(item.status) || undefined,
    }))
    .filter((step) => step.stepId || step.title);
  if (!readString(record.planId) && !readString(record.goal) && steps.length === 0) {
    return null;
  }
  return {
    planId: readString(record.planId) || readString(record.goal),
    goal: readString(record.goal),
    status: readString(record.status) || undefined,
    createdBy: readString(record.createdBy) || undefined,
    provider: readString(record.provider) || undefined,
    model: readString(record.model) || undefined,
    steps,
  };
}

export function readMasteryDiagnosis(payload: Record<string, unknown> | undefined): MasteryDiagnosisView | null {
  const record = readRecord(payload?.masteryDiagnosis);
  if (!record) {
    return null;
  }
  const rawTargetScope = readRecord(record.targetScope);
  const knowledgeDiagnoses = Array.isArray(record.knowledgeDiagnoses)
    ? record.knowledgeDiagnoses
        .map((item) => readRecord(item))
        .filter((item): item is Record<string, unknown> => Boolean(item))
        .map((item) => ({
          knowledgePoint: readString(item.knowledgePoint),
          masteryScore: readNumericRaw(item.masteryScore),
          status: readString(item.status) || undefined,
          priority: readNumericRaw(item.priority),
          evidence: readStringArray(item.evidence),
          errorPatterns: readStringArray(item.errorPatterns),
          nextFocus: readString(item.nextFocus) || undefined,
          recommendedResourceTypes: readStringArray(item.recommendedResourceTypes),
        }))
        .filter((item) => item.knowledgePoint || item.evidence.length > 0)
    : [];
  const summaryText = readString(record.summaryText);
  const overallLevel = readString(record.overallLevel);
  if (!summaryText && !overallLevel && knowledgeDiagnoses.length === 0) {
    return null;
  }
  return {
    diagnosisSource: readString(record.diagnosisSource) || undefined,
    primaryDimension: readString(record.primaryDimension) || undefined,
    overallLevel: overallLevel || undefined,
    overallMasteryScore: readNumericRaw(record.overallMasteryScore),
    confidence: readNumericRaw(record.confidence),
    targetScope: rawTargetScope
      ? {
          course: readString(rawTargetScope.course) || undefined,
          chapter: readString(rawTargetScope.chapter) || undefined,
          knowledgePoints: readStringArray(rawTargetScope.knowledgePoints),
        }
      : undefined,
    knowledgeDiagnoses,
    behaviorSignals: readBehaviorSignals(record.behaviorSignals),
    planAdjustmentHints: readPlanAdjustmentHints(record.planAdjustmentHints),
    summaryText: summaryText || undefined,
  };
}

function readBehaviorSignals(value: unknown): MasteryDiagnosisView['behaviorSignals'] {
  const record = readRecord(value);
  if (!record) {
    return undefined;
  }
  return {
    practiceAccuracy: readNumericRaw(record.practiceAccuracy),
    recentQuestionCount: readNumericRaw(record.recentQuestionCount),
    reviewCount: readNumericRaw(record.reviewCount),
    resourceDownloads: readNumericRaw(record.resourceDownloads),
    messageCount: readNumericRaw(record.messageCount),
    recentMistakeCount: readNumericRaw(record.recentMistakeCount),
  };
}

function readPlanAdjustmentHints(value: unknown): MasteryDiagnosisView['planAdjustmentHints'] {
  const record = readRecord(value);
  if (!record) {
    return undefined;
  }
  return {
    shouldRefreshPlan: typeof record.shouldRefreshPlan === 'boolean' ? record.shouldRefreshPlan : undefined,
    refreshReason: readString(record.refreshReason) || undefined,
    strategy: readString(record.strategy) || undefined,
  };
}

export function readResourcePushPlan(payload: Record<string, unknown> | undefined): ResourcePushPlanView | null {
  const record = readRecord(payload?.resourcePushPlan);
  if (!record) {
    return readPushedResourcesAsPlan(payload?.pushedResources);
  }
  const stepResources = Array.isArray(record.stepResources)
    ? record.stepResources
        .map((item) => readRecord(item))
        .filter((item): item is Record<string, unknown> => Boolean(item))
        .map((item) => ({
          stepId: readString(item.stepId),
          stepTitle: readString(item.stepTitle) || undefined,
          targetKnowledgePoints: readStringArray(item.targetKnowledgePoints),
          resources: Array.isArray(item.resources)
            ? item.resources
                .map((resource) => readRecord(resource))
                .filter((resource): resource is Record<string, unknown> => Boolean(resource))
                .map((resource) => ({
                  title: readString(resource.title),
                  resourceType: readString(resource.resourceType),
                  source: readString(resource.source) || readString(resource.sourceName),
                  sourceName: readString(resource.sourceName) || undefined,
                  matchReason: readString(resource.matchReason) || readString(resource.rerankReason) || undefined,
                  downloadUrl: readString(resource.downloadUrl) || undefined,
                  summaryText: readString(resource.summaryText) || readString(resource.summary) || undefined,
                }))
                .filter((resource) => resource.title || resource.downloadUrl || resource.summaryText)
            : [],
        }))
        .filter((item) => item.stepId || item.stepTitle || item.resources.length > 0)
    : [];
  const coverageGaps = Array.isArray(record.coverageGaps)
    ? record.coverageGaps
        .map((item) => readRecord(item))
        .filter((item): item is Record<string, unknown> => Boolean(item))
        .map((item) => ({
          stepId: readString(item.stepId),
          missingResourceTypes: readStringArray(item.missingResourceTypes),
          reason: readString(item.reason) || undefined,
        }))
        .filter((item) => item.stepId || item.missingResourceTypes.length > 0)
    : [];
  if (stepResources.length === 0 && coverageGaps.length === 0) {
    return readPushedResourcesAsPlan(payload?.pushedResources);
  }
  return {
    stepResources,
    coverageGaps,
  };
}

function readPushedResourcesAsPlan(value: unknown): ResourcePushPlanView | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const resources = value
    .map((item) => readRecord(item))
    .filter((item): item is Record<string, unknown> => Boolean(item))
    .map((item) => ({
      title: readString(item.title),
      resourceType: readString(item.resourceType),
      source: readString(item.source) || readString(item.sourceName),
      sourceName: readString(item.sourceName) || undefined,
      matchReason: readString(item.matchReason) || readString(item.rerankReason) || undefined,
      downloadUrl: readString(item.downloadUrl) || undefined,
      summaryText: readString(item.summaryText) || readString(item.summary) || undefined,
    }))
    .filter((item) => item.title || item.downloadUrl || item.summaryText);
  if (resources.length === 0) {
    return null;
  }
  return {
    stepResources: [
      {
        stepId: 'resources',
        stepTitle: '推荐资源',
        targetKnowledgePoints: [],
        resources,
      },
    ],
    coverageGaps: [],
  };
}

export function readCriticReview(payload: Record<string, unknown> | undefined): CriticReviewView | null {
  const record = readRecord(payload?.criticReview);
  if (!record) {
    return null;
  }
  const issues = readStringArray(record.issues);
  const suggestions = readStringArray(record.suggestions);
  const summaryText = readString(record.summaryText);
  const verdict = readString(record.verdict);
  if (!verdict && !summaryText && issues.length === 0 && suggestions.length === 0) {
    return null;
  }
  return {
    verdict: verdict || undefined,
    coverageScore: readNumericRaw(record.coverageScore),
    pathOrderScore: readNumericRaw(record.pathOrderScore),
    resourceMatchScore: readNumericRaw(record.resourceMatchScore),
    factConsistency: readString(record.factConsistency) || undefined,
    difficultyMatch: readString(record.difficultyMatch) || undefined,
    sourceCoverage: readString(record.sourceCoverage) || undefined,
    issues,
    suggestions,
    summaryText: summaryText || undefined,
  };
}

export function readAgentTrace(payload: Record<string, unknown> | undefined): AgentTraceStepView[] {
  const rawTrace = payload?.agentTrace;
  if (!Array.isArray(rawTrace)) {
    return [];
  }
  return rawTrace
    .map((item) => readRecord(item))
    .filter((item): item is Record<string, unknown> => Boolean(item))
    .map((item) => ({
      agentName: readString(item.agentName),
      status: readString(item.status),
      stage: readString(item.stage) || undefined,
      message: readString(item.message) || undefined,
    }))
    .filter((item) => item.agentName || item.stage || item.message);
}

export function readNumericRaw(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return undefined;
}
