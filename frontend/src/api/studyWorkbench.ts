import { request } from './request';
import type {
  KnowledgeGraphEdge,
  KnowledgeGraphNode,
  KnowledgeGraphResponse,
  LearningPathCurrentResponse,
  UserProfileResponse,
} from './smartEngine';
import type { MistakeRecordResponse } from './mistakes';
import type { ResourceItem } from './resources';

export interface DailyTaskItem {
  id: string;
  type: 'STAGE' | 'STAGE_TEST' | 'MISTAKE_REVIEW' | 'RESOURCE' | 'KNOWLEDGE' | string;
  title: string;
  description: string;
  status: 'READY' | 'PENDING' | 'IN_PROGRESS' | 'COMPLETED' | string;
  progress?: number | null;
  actionLabel: string;
  actionRoute: string;
  actionPayload: Record<string, unknown>;
  dueAt?: string | null;
}

export interface WorkbenchSummary {
  totalTasks: number;
  completedTasks: number;
  dueMistakeCount: number;
  recommendedResourceCount: number;
  weakKnowledgeCount: number;
  progressPercent: number;
  nextAction: string;
  stageTestReady: boolean;
}

export interface LearningSessionStep {
  id: string;
  phase: string;
  title: string;
  description: string;
  status: 'READY' | 'PENDING' | 'IN_PROGRESS' | 'COMPLETED' | string;
  minutes?: number | null;
  actionLabel: string;
  actionRoute: string;
  sourceTaskId?: string | null;
  sourceTaskType?: string | null;
}

export interface PlanSupportItem {
  id: string;
  type: 'STAGE' | 'MISTAKE_REVIEW' | 'RESOURCE' | 'KNOWLEDGE' | string;
  title: string;
  description: string;
  actionRoute: string;
}

export interface DailyExecutionPlan {
  title: string;
  subtitle: string;
  focusReason: string;
  successCriteria: string;
  estimatedMinutes: number;
  primaryTask: DailyTaskItem;
  steps: LearningSessionStep[];
  supportItems: PlanSupportItem[];
}

export interface DailyStudyWorkbenchResponse {
  userId: string;
  workDate: string;
  generatedAt: string;
  summary: WorkbenchSummary;
  learningPath: LearningPathCurrentResponse;
  activeStep?: Record<string, unknown> | null;
  executionPlan?: DailyExecutionPlan | null;
  tasks: DailyTaskItem[];
  dueMistakes: MistakeRecordResponse[];
  recommendedResources: ResourceItem[];
  knowledgeGraph: KnowledgeGraphResponse;
  profile: UserProfileResponse;
  dataAvailable: boolean;
}

export interface KnowledgeNodeDetailResponse {
  userId: string;
  node: KnowledgeGraphNode;
  prerequisites: KnowledgeGraphNode[];
  nextNodes: KnowledgeGraphNode[];
  relatedNodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  relatedMistakes: MistakeRecordResponse[];
  relatedResources: ResourceItem[];
  recommendedNextActions: string[];
  practiceContext: Record<string, unknown>;
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

export const studyWorkbenchApi = {
  daily(): Promise<DailyStudyWorkbenchResponse> {
    return request.get<DailyStudyWorkbenchResponse>('/api/study-workbench/daily', { dedupe: false });
  },

  refreshDaily(): Promise<DailyStudyWorkbenchResponse> {
    return request.post<DailyStudyWorkbenchResponse>('/api/study-workbench/daily/refresh');
  },

  knowledgeNodeDetail(userId: string, nodeKey: string): Promise<KnowledgeNodeDetailResponse> {
    return request.get<KnowledgeNodeDetailResponse>(
      `/api/users/${userId}/knowledge-graph/${encodeURIComponent(nodeKey)}`,
      { dedupe: false },
    );
  },

  trainingCamps(): Promise<MistakeTrainingCampResponse> {
    return request.get<MistakeTrainingCampResponse>('/api/mistakes/training-camps', { dedupe: false });
  },
};
