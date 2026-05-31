import type { ComponentType } from 'react';
import { ChartColumn, Compass, Sparkles, Target } from 'lucide-react';
import type { SmartEngineServiceType, SmartEngineStreamEvent, SmartEngineTaskResponse, UserProfileResponse } from '../api/smartEngine';
import type { ConversationStreamEventPayload } from '../api/conversation';
import type { VideoCardStyle } from '../components/VideoCard';

export type EngineService = 'resource' | 'path' | 'push' | 'assessment';
export type ResourceType = 'EXPLANATION' | 'CODE_CASE' | 'QUIZ' | 'MINDMAP' | 'SLIDES' | 'VIDEO';
export type PushResourceType = 'EXPLANATION' | 'CODE_CASE' | 'PRACTICAL_CASE' | 'READING' | 'VIDEO';
export type QnaState = 'QNA_IDLE' | 'QNA_STREAMING';
export type EngineState =
  | 'ENGINE_IDLE'
  | 'ENGINE_SERVICE_SELECTED'
  | 'ENGINE_FORM_EDITING'
  | 'ENGINE_SUBMITTING'
  | 'ENGINE_RUNNING'
  | 'ENGINE_COMPLETED'
  | 'ENGINE_FAILED';

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  imageUrls?: string[];
  localImagePreviews?: string[];
  webSearchEnabled?: boolean;
  deepReasoningEnabled?: boolean;
}

export interface PendingChatImage {
  id: string;
  file: File;
  previewUrl: string;
  uploadStatus: 'pending' | 'uploading' | 'uploaded' | 'failed';
  uploadProgress: number;
  uploadedUrl?: string;
  errorMessage?: string;
}

export interface TempDownloadLink {
  title: string;
  url: string;
  fileName?: string;
  expiresHint: string;
  resourceType?: string;
  mimeType?: string;
  summary?: string;
  sourceName?: string;
  thumbnailUrl?: string;
  duration?: number;
  style?: VideoCardStyle;
  knowledgePoint?: string;
}

export interface VideoResult {
  title: string;
  videoUrl: string;
  thumbnailUrl?: string;
  duration?: number;
  style?: VideoCardStyle;
  knowledgePoint?: string;
  expiresHint?: string;
  fileName?: string;
}

export interface InlineResourceView {
  kind: 'markdown' | 'code' | 'mermaid';
  title: string;
  summary?: string;
  content: string;
  language?: string;
  explanation?: string;
}

export type CompletedResourceView =
  | { kind: 'inline'; key: string; resource: InlineResourceView }
  | { kind: 'question_batch'; key: string; batch: PracticeQuestionBatch };

export interface LearningPlanStepView {
  stepId: string;
  title: string;
  intent?: string;
  agentName?: string;
  serviceType?: string;
  status?: string;
  qualityGate?: string;
}

export interface LearningPlanView {
  planId: string;
  goal: string;
  status?: string;
  createdBy?: string;
  provider?: string;
  model?: string;
  steps: LearningPlanStepView[];
}

export interface CriticReviewView {
  verdict?: string;
  factConsistency?: string;
  difficultyMatch?: string;
  sourceCoverage?: string;
  issues: string[];
  suggestions: string[];
  summaryText?: string;
}

export interface EngineTaskResultRecord {
  taskId: string;
  title: string;
  taskStatus: string;
  engineState: EngineState;
  taskSummary: string;
  serviceResultLines: string[];
  downloadLinks: TempDownloadLink[];
  videoResult: VideoResult | null;
  inlineResources: InlineResourceView[];
  practiceBatch: PracticeQuestionBatch | null;
  completedResources: CompletedResourceView[];
  judgeResult: PracticeJudgeResult | null;
  learningPlan: LearningPlanView | null;
  criticReview: CriticReviewView | null;
  createdAt: number;
  updatedAt: number;
}

export interface PracticeQuestion {
  questionId: string;
  questionType: 'SINGLE_CHOICE' | 'SHORT_ANSWER' | string;
  stem: string;
  options?: string[];
  answer?: string;
  knowledgeTags?: string[];
  difficultyLevel?: string;
  explanation?: string;
}

export interface PracticeQuestionBatch {
  title: string;
  topic: string;
  difficulty: string;
  description?: string;
  assessmentDimension?: string;
  submitLabel?: string;
  generatedBy?: string;
  contentOrigin?: string;
  provider?: string;
  model?: string;
  agentName?: string;
  evidenceIds?: string[];
  fallback?: boolean;
  fromCache?: boolean;
  questions: PracticeQuestion[];
}

export interface JudgeItemResult {
  questionId: string;
  questionType: string;
  learnerAnswer: string;
  correctAnswer?: string;
  isCorrect: boolean;
  score: number;
  knowledgeTags?: string[];
  reason: string;
  feedback: string;
}

export interface PracticeJudgeResult {
  title: string;
  summary: string;
  totalScore: number;
  accuracy: number;
  assessmentDimension?: string;
  weakKnowledgeTags?: string[];
  specializedAnalysis?: {
    title: string;
    summary: string;
    dimension?: string;
    strengths: string[];
    weaknesses: string[];
    nextActions: string[];
    markdown?: string;
  };
  items: JudgeItemResult[];
}

export interface EngineTaskSnapshot {
  engineState: EngineState;
  taskId: string;
  taskProgress: number;
  taskStatus: string;
  taskSummary: string;
  serviceResultLines: string[];
  downloadLinks: TempDownloadLink[];
  videoResult: VideoResult | null;
  inlineResource: InlineResourceView | null;
  inlineResources: InlineResourceView[];
  practiceBatch: PracticeQuestionBatch | null;
  completedResources: CompletedResourceView[];
  judgeResult: PracticeJudgeResult | null;
  learningPlan: LearningPlanView | null;
  criticReview: CriticReviewView | null;
  resultHistory: EngineTaskResultRecord[];
  selectedResultTaskId: string;
}

export interface ResourceForm {
  resourceType: ResourceType;
  resourceTypes: ResourceType[];
  course: string;
  difficulty: 'basic' | 'intermediate' | 'advanced';
  keyPoints: string;
}

export interface PathForm {
  targetPeriod: string;
  weeklyHours: string;
  currentProgress: string;
}

export interface PushForm {
  preferredType: PushResourceType;
}

export interface AssessmentForm {
  dimensions: string[];
}

export interface ProfileSnapshot {
  major: string;
  goal: string;
  knowledgeBase: string;
  weakPoints: string[];
  preference: string[];
  cognitiveStyle: string;
  learningPace: string;
  currentGoal: ProfileCurrentGoal;
  learningHabits: ProfileLearningHabits;
  skillMastery: ProfileSkillMastery[];
  confidenceLevel: string;
  confidenceScore: number;
  explanationPreference: string;
  inferredRecommendations: string[];
  dimensionScores: ProfileDimensionScore[];
  weakPointRanks: WeakPointRank[];
  history: ProfileHistoryPoint[];
}

export interface ProfileCurrentGoal {
  shortTerm: string;
  midTerm: string;
  context: string;
  urgency: string;
}

export interface ProfileLearningHabits {
  studyFrequency: string;
  preferredTime: string;
  avgSessionDuration: number;
  noteTaking: boolean;
  selfTesting: boolean;
}

export interface ProfileSkillMastery {
  topic: string;
  score: number;
}

export interface ProfileHistoryPoint {
  version: number;
  updatedAt: string;
  confidenceScore: number;
  knowledgeBase: string;
  weakPointCount: number;
  learningPace: string;
}

export interface ProfileDimensionScore {
  key: string;
  subject: string;
  score: number;
  fullMark: number;
  hint: string;
  description: string;
}

export interface WeakPointRank {
  topic: string;
  severity: number;
  lastError: string;
  errorPattern?: string;
  severityInferred?: boolean;
}

export interface TaskRunHandlers {
  onProgress: (progress: number, statusHint?: string) => void;
  onLine: (line: string) => void;
  onSummary: (summary: string) => void;
  onDownload: (item: TempDownloadLink) => void;
  onVideo: (item: VideoResult) => void;
  onInlineResource: (item: InlineResourceView) => void;
  onQuestionBatch: (item: PracticeQuestionBatch) => void;
  onJudgeResult: (item: PracticeJudgeResult) => void;
  onLearningPlan: (item: LearningPlanView) => void;
  onCriticReview: (item: CriticReviewView) => void;
}

export interface RunByApiTaskArgs {
  service: EngineService;
  currentTaskId: string;
  streamQueueRef: React.MutableRefObject<string[]>;
  streamFlushTimerRef: React.MutableRefObject<number | null>;
  streamRafRef: React.MutableRefObject<number | null>;
  setServiceResultLines: (value: React.SetStateAction<string[]>) => void;
  setTaskProgress: (value: React.SetStateAction<number>) => void;
  setTaskStatus: (value: React.SetStateAction<string>) => void;
  setTaskSummary: (value: React.SetStateAction<string>) => void;
  setDownloadLinks: (value: React.SetStateAction<TempDownloadLink[]>) => void;
  setVideoResult: (value: React.SetStateAction<VideoResult | null>) => void;
  setInlineResource: (value: React.SetStateAction<InlineResourceView | null>) => void;
  setInlineResources: (value: React.SetStateAction<InlineResourceView[]>) => void;
  setCompletedResources: (value: React.SetStateAction<CompletedResourceView[]>) => void;
  setPracticeBatch: (value: React.SetStateAction<PracticeQuestionBatch | null>) => void;
  setJudgeResult: (value: React.SetStateAction<PracticeJudgeResult | null>) => void;
  setLearningPlan: (value: React.SetStateAction<LearningPlanView | null>) => void;
  setCriticReview: (value: React.SetStateAction<CriticReviewView | null>) => void;
  taskStreamAbortRef: React.MutableRefObject<AbortController | null>;
}

export interface ServiceFormsPayload {
  resourceForm: ResourceForm;
  pathForm: PathForm;
  pushForm: PushForm;
  assessmentForm: AssessmentForm;
}

export interface ServiceButtonConfig {
  id: EngineService;
  label: string;
  icon: ComponentType<{ className?: string }>;
}

export interface ResourceTypeButtonConfig {
  type: ResourceType;
  label: string;
}

export interface PushResourceTypeButtonConfig {
  type: PushResourceType;
  label: string;
}

export type {
  ConversationStreamEventPayload,
  SmartEngineServiceType,
  SmartEngineStreamEvent,
  SmartEngineTaskResponse,
  UserProfileResponse,
  VideoCardStyle,
};

export const QNA_GREETING = '你好。你现在有什么要求？';
export const EMPTY_VALUE = '--';
export const defaultAssessmentDimensions = ['知识基础'];
export const assessmentDimensionOptions = ['知识基础', '案例迁移', '练习掌握', '学习主动性', '复盘闭环'];

export const serviceButtons: ServiceButtonConfig[] = [
  { id: 'resource', label: '资源生成', icon: Sparkles },
  { id: 'path', label: '学习路径规划', icon: Compass },
  { id: 'push', label: '资源推送', icon: Target },
  { id: 'assessment', label: '学习效果评估', icon: ChartColumn },
];

export const resourceTypeButtons: ResourceTypeButtonConfig[] = [
  { type: 'EXPLANATION', label: '讲解文档' },
  { type: 'CODE_CASE', label: '代码案例' },
  { type: 'QUIZ', label: '练习题' },
  { type: 'MINDMAP', label: '思维导图' },
  { type: 'SLIDES', label: '演示课件' },
  { type: 'VIDEO', label: '数字人视频' },
];

export const pushResourceTypeOptions: PushResourceTypeButtonConfig[] = [
  { type: 'EXPLANATION', label: '讲解文档' },
  { type: 'CODE_CASE', label: '代码案例' },
  { type: 'PRACTICAL_CASE', label: '实操案例' },
  { type: 'READING', label: '拓展阅读' },
  { type: 'VIDEO', label: '视频' },
];

export const serviceTypeMap: Record<EngineService, SmartEngineServiceType> = {
  resource: 'RESOURCE_GENERATION',
  path: 'PATH_PLANNING',
  push: 'RESOURCE_PUSH',
  assessment: 'LEARNING_EVALUATION',
};

export const defaultResourceForm: ResourceForm = {
  resourceType: 'EXPLANATION',
  resourceTypes: ['EXPLANATION'],
  course: '',
  difficulty: 'intermediate',
  keyPoints: '',
};
