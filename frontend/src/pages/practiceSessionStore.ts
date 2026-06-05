import type {
  JudgeItemResult,
  PracticeJudgeResult,
  PracticeQuestionBatch,
} from './LearningStudioDemoPage.types';

export type PracticeJudgeStatus = 'idle' | 'submitting' | 'judging' | 'completed' | 'failed';

export interface PracticeSessionState {
  batch: PracticeQuestionBatch | null;
  open: boolean;
  source: 'conversation' | 'stage_test' | 'engine' | '';
  ownerUserId?: string;
  phaseId?: string;
  phaseTitle?: string;
  perQuestionResults: Record<string, JudgeItemResult>;
  judgeResult: PracticeJudgeResult | null;
  judgeStatus: PracticeJudgeStatus;
  error: string;
  conversationId?: string;
}

const DEFAULT_STATE: PracticeSessionState = {
  batch: null,
  open: false,
  source: '',
  perQuestionResults: {},
  judgeResult: null,
  judgeStatus: 'idle',
  error: '',
};

const listeners = new Set<(state: PracticeSessionState) => void>();
let state: PracticeSessionState = DEFAULT_STATE;

export function getPracticeSessionState(): PracticeSessionState {
  return state;
}

export function subscribePracticeSession(listener: (state: PracticeSessionState) => void): () => void {
  listeners.add(listener);
  listener(state);
  return () => {
    listeners.delete(listener);
  };
}

export function setPracticeSession(updater: PracticeSessionState | ((current: PracticeSessionState) => PracticeSessionState)): PracticeSessionState {
  state = typeof updater === 'function' ? updater(state) : updater;
  listeners.forEach((listener) => listener(state));
  return state;
}

export function openPracticeSession(options: {
  batch: PracticeQuestionBatch;
  source: PracticeSessionState['source'];
  ownerUserId?: number | string;
  phaseId?: string;
  phaseTitle?: string;
  conversationId?: string;
}): PracticeSessionState {
  return setPracticeSession({
    batch: options.batch,
    open: true,
    source: options.source,
    ownerUserId: normalizeOwnerUserId(options.ownerUserId),
    phaseId: options.phaseId,
    phaseTitle: options.phaseTitle,
    conversationId: options.conversationId,
    perQuestionResults: {},
    judgeResult: null,
    judgeStatus: 'idle',
    error: '',
  });
}

export function setPracticeSessionOpen(open: boolean): PracticeSessionState {
  return setPracticeSession((current) => ({
    ...current,
    open: current.batch ? open : false,
  }));
}

export function setPracticeJudgeStatus(judgeStatus: PracticeJudgeStatus, error = ''): PracticeSessionState {
  return setPracticeSession((current) => ({
    ...current,
    judgeStatus,
    error,
  }));
}

export function recordPracticeJudgeResult(result: PracticeJudgeResult): PracticeSessionState {
  const item = result.items[0];
  return setPracticeSession((current) => ({
    ...current,
    judgeResult: result,
    judgeStatus: 'completed',
    error: '',
    perQuestionResults: item?.questionId
      ? {
        ...current.perQuestionResults,
        [item.questionId]: item,
      }
      : current.perQuestionResults,
  }));
}

export function clearPracticeSession(): PracticeSessionState {
  return setPracticeSession(DEFAULT_STATE);
}

function normalizeOwnerUserId(value: number | string | undefined): string | undefined {
  if (value === undefined || value === null) {
    return undefined;
  }
  const normalized = String(value).trim();
  return normalized || undefined;
}
