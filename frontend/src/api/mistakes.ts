import { request } from './request';

export type MistakeStatus = 'due' | 'active' | 'mastered' | 'all';

export interface MistakeStatsResponse {
  dueCount: number;
  activeCount: number;
  masteredCount: number;
}

export interface MistakeRecordResponse {
  id: string;
  practiceItemId: string;
  lastSubmissionId: string;
  questionType: string;
  stem: string;
  options: string[];
  standardAnswer: Record<string, unknown>;
  learnerAnswer: string;
  judgeResult: Record<string, unknown>;
  score?: number;
  submittedAt?: string;
  knowledgeTags: string[];
  difficultyLevel: string;
  mistakeType?: string;
  userNote: string;
  wrongCount: number;
  reviewCount: number;
  nextReviewAt: string;
  easeFactor: number;
  intervalDays: number;
  mastered: boolean;
  firstWrongAt: string;
  lastWrongAt: string;
  createdAt: string;
  updatedAt: string;
}

export interface MistakeListResponse {
  items: MistakeRecordResponse[];
  total: number;
  page: number;
  size: number;
  stats: MistakeStatsResponse;
}

export interface MistakeUpdateRequest {
  userNote?: string;
  mistakeType?: 'conceptual' | 'procedural' | 'careless';
  mastered?: boolean;
}

export interface CreateReviewSessionRequest {
  mistakeIds?: string[];
  limit?: number;
}

export interface MistakeReviewSessionResponse {
  sessionId: string;
  status: 'IN_PROGRESS' | 'DONE' | 'CANCELLED';
  score?: number;
  items: MistakeRecordResponse[];
  createdAt: string;
  completedAt?: string;
}

export interface TrainingMicroPractice {
  id: string;
  title: string;
  description: string;
  difficulty: string;
  knowledgeTags: string[];
  prompt: string;
}

export interface MistakeCampGroup {
  campId: string;
  title: string;
  mistakeType: string;
  knowledgeTag: string;
  explanation: string;
  mistakeCount: number;
  dueCount: number;
  masteredCount: number;
  totalWrongCount: number;
  totalReviewCount: number;
  masteryChange: number;
  nextReviewAt?: string | null;
  representativeMistakes: MistakeRecordResponse[];
  microPractices: TrainingMicroPractice[];
  practiceContext: Record<string, unknown>;
}

export interface MistakeTrainingCampResponse {
  userId: string;
  generatedAt: string;
  summary: {
    campCount: number;
    activeMistakeCount: number;
    dueMistakeCount: number;
    masteredMistakeCount: number;
    topFocus: string;
  };
  camps: MistakeCampGroup[];
}

export interface SubmitReviewSessionRequest {
  items: Array<{
    mistakeRecordId: string;
    quality: number;
    isCorrect?: boolean;
    answer?: Record<string, unknown>;
  }>;
}

export const mistakesApi = {
  list(params: {
    status?: MistakeStatus;
    knowledgeTag?: string;
    difficulty?: string;
    page?: number;
    size?: number;
  }): Promise<MistakeListResponse> {
    return request.get<MistakeListResponse>('/api/mistakes', { params, dedupe: false });
  },

  update(id: string, payload: MistakeUpdateRequest): Promise<MistakeRecordResponse> {
    return request.patch<MistakeRecordResponse>(`/api/mistakes/${id}`, payload);
  },

  createReviewSession(payload: CreateReviewSessionRequest = {}): Promise<MistakeReviewSessionResponse> {
    return request.post<MistakeReviewSessionResponse>('/api/mistakes/review', payload);
  },

  getReviewSession(sessionId: string): Promise<MistakeReviewSessionResponse> {
    return request.get<MistakeReviewSessionResponse>(`/api/mistakes/review/${sessionId}`, { dedupe: false });
  },

  submitReviewSession(sessionId: string, payload: SubmitReviewSessionRequest): Promise<MistakeReviewSessionResponse> {
    return request.post<MistakeReviewSessionResponse>(`/api/mistakes/review/${sessionId}/submit`, payload);
  },

  trainingCamps(): Promise<MistakeTrainingCampResponse> {
    return request.get<MistakeTrainingCampResponse>('/api/mistakes/training-camps', { dedupe: false });
  },
};
