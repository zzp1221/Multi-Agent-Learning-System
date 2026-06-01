import { API_BASE_URL, getAuthHeaders, request } from './request';
import type { AxiosRequestConfig } from 'axios';
import { streamSse } from './sse';

export type SmartEngineServiceType =
  | 'RESOURCE_GENERATION'
  | 'PATH_PLANNING'
  | 'RESOURCE_PUSH'
  | 'LEARNING_EVALUATION'
  | 'EVALUATION'
  | 'VIDEO_GENERATION'
  | 'TUTORING'
  | 'PROFILE_BUILD'
  | 'PRACTICE_JUDGE';

export interface SmartEngineSubmitRequest {
  conversationId: string;
  serviceType: SmartEngineServiceType;
  params: Record<string, unknown>;
}

export interface SmartEngineSubmitResponse {
  taskId: string;
  status?: string;
}

export interface SmartEngineTaskResponse {
  taskId: string;
  traceId?: string;
  serviceType?: string;
  status?: string;
  currentStage?: string;
  progress?: number;
  progressPercent?: number;
  errorCode?: string;
  errorMessage?: string;
  result?: unknown;
  responseSummary?: Record<string, unknown>;
}

export type SmartEngineStreamEventType =
  | 'progress'
  | 'result_chunk'
  | 'resource_file'
  | 'question_batch'
  | 'judge_result'
  | 'done'
  | 'error'
  | 'video_gen:start'
  | 'video_gen:script'
  | 'video_gen:speech'
  | 'video_gen:avatar'
  | 'video_gen:complete';

export interface SmartEngineStreamEvent {
  event: SmartEngineStreamEventType;
  data: string;
}

export interface UserProfileResponse {
  userId: string;
  profile?: Record<string, unknown>;
  summary?: string;
  updatedAt?: string;
  history?: Array<{
    version?: number;
    profile?: Record<string, unknown>;
    summary?: string;
    confidence?: number;
    updatedAt?: string;
  }>;
}

export interface ProfileBehaviorTrendPoint {
  date: string;
  conversationCount: number;
  serviceTaskCount: number;
  practiceSubmissionCount: number;
  practiceAccuracy: number | null;
  newMistakeCount: number;
  reviewCount: number;
}

export interface ProfileDataCoverage {
  activeDays: number;
  conversationCount: number;
  serviceTaskCount: number;
  practiceSubmissionCount: number;
  newMistakeCount: number;
  reviewCount: number;
  profileSkillCount: number;
  weakPointCount: number;
}

export interface ProfileSystemAnalysis {
  strongestSkill?: string | null;
  strongestSkillScore?: number | null;
  focusAreas: string[];
  coverage: ProfileDataCoverage;
  summary: string;
  dataAvailable: boolean;
}

export interface ProfileResourcePreference {
  type: string;
  label: string;
  identified: boolean;
  profileMentioned: boolean;
  requestCount: number;
  generatedCount: number;
  downloadCount: number;
  lastUsedAt?: string | null;
  evidenceLabel: string;
}

export interface ProfileExplanationPreferenceAnalytics {
  value: string;
  source: string;
  identified: boolean;
}

export interface ProfilePreferenceAnalytics {
  resourcePreferences: ProfileResourcePreference[];
  explanationPreference: ProfileExplanationPreferenceAnalytics;
}

export interface UserProfileAnalyticsResponse {
  userId: string;
  days: number;
  fromDate: string;
  toDate: string;
  behaviorTrend: ProfileBehaviorTrendPoint[];
  systemAnalysis: ProfileSystemAnalysis;
  preferenceAnalytics?: ProfilePreferenceAnalytics;
}

export interface KnowledgeGraphNode {
  key: string;
  topic: string;
  mastery: number;
  status: 'NOT_STARTED' | 'IN_PROGRESS' | 'MASTERED' | 'WEAK';
  source: string;
}

export interface KnowledgeGraphEdge {
  from: string;
  to: string;
  type: 'PREREQUISITE' | 'RELATED' | 'PART_OF';
  weight: number;
}

export interface KnowledgeGraphResponse {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  nextRecommended: string[];
}

export const smartEngineApi = {
  submit(payload: SmartEngineSubmitRequest): Promise<SmartEngineSubmitResponse> {
    return request.post<SmartEngineSubmitResponse>('/api/smart-engine/submit', payload);
  },

  getTask(taskId: string, config?: AxiosRequestConfig & { dedupe?: boolean; retry?: number }): Promise<SmartEngineTaskResponse> {
    return request.get<SmartEngineTaskResponse>(`/api/smart-engine/tasks/${taskId}`, config);
  },

  cancelTask(taskId: string): Promise<void> {
    return request.post<void>(`/api/smart-engine/tasks/${taskId}/cancel`);
  },

  getTaskStreamUrl(taskId: string): string {
    return `/api/smart-engine/tasks/${taskId}/stream`;
  },

  async streamTask(
    taskId: string,
    handlers: {
      onEvent: (event: SmartEngineStreamEvent) => void;
      onDone: () => void;
      onError: (error: Error) => void;
    },
    signal?: AbortSignal,
  ): Promise<void> {
    await streamSse(`${API_BASE_URL}${this.getTaskStreamUrl(taskId)}`, {
      init: {
        method: 'GET',
        headers: {
          Accept: 'text/event-stream',
          ...getAuthHeaders(),
        },
        signal,
      },
      missingBodyMessage: '无法读取实时任务连接',
      requestFailedMessage: (status) => status === 429
        ? '请求过于频繁 (429)，请稍后重试'
        : `实时任务连接请求失败 (${status})`,
      maxRetries: 2,
      defaultEvent: 'result_chunk',
      onEvent: (rawEvent) => {
        const parsed: SmartEngineStreamEvent = {
          event: rawEvent.event as SmartEngineStreamEventType,
          data: rawEvent.data,
        };
        handlers.onEvent(parsed);
        if (parsed.event === 'done') {
          handlers.onDone();
          return true;
        }
        if (parsed.event === 'error') {
          return true;
        }
        return false;
      },
      onDone: handlers.onDone,
      onError: handlers.onError,
    });
  },

  getCurrentProfile(userId: string): Promise<UserProfileResponse> {
    return request.get<UserProfileResponse>(`/api/users/${userId}/profile/current`);
  },

  getProfileAnalytics(userId: string, days = 30): Promise<UserProfileAnalyticsResponse> {
    return request.get<UserProfileAnalyticsResponse>(`/api/users/${userId}/profile/analytics`, {
      params: { days },
    });
  },

  getKnowledgeGraph(userId: string): Promise<KnowledgeGraphResponse> {
    return request.get<KnowledgeGraphResponse>(`/api/users/${userId}/knowledge-graph`);
  },
};
