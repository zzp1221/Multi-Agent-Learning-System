import type {
  PracticeJudgeResult,
  PracticeQuestionBatch,
} from './LearningStudioDemoPage.types';

export type StageTestSessionStatus = 'idle' | 'answering' | 'submitting' | 'completed' | 'failed';

export interface StageTestSessionState {
  open: boolean;
  status: StageTestSessionStatus;
  batch: PracticeQuestionBatch | null;
  phaseId?: string;
  phaseTitle?: string;
  conversationId?: string;
  taskId?: string;
  answers: Record<string, string>;
  result: PracticeJudgeResult | null;
  error: string;
}

const STORAGE_KEY = 'learning_studio_stage_test_session';
const DEFAULT_STATE: StageTestSessionState = {
  open: false,
  status: 'idle',
  batch: null,
  answers: {},
  result: null,
  error: '',
};

const listeners = new Set<(state: StageTestSessionState) => void>();
let state: StageTestSessionState = loadStoredState();

export function getStageTestSessionState(): StageTestSessionState {
  return state;
}

export function subscribeStageTestSession(listener: (state: StageTestSessionState) => void): () => void {
  listeners.add(listener);
  listener(state);
  return () => {
    listeners.delete(listener);
  };
}

export function openStageTestSession(options: {
  batch: PracticeQuestionBatch;
  phaseId?: string;
  phaseTitle?: string;
  conversationId?: string;
  taskId?: string;
}): StageTestSessionState {
  return setStageTestSession({
    open: true,
    status: 'answering',
    batch: options.batch,
    phaseId: options.phaseId,
    phaseTitle: options.phaseTitle,
    conversationId: options.conversationId,
    taskId: options.taskId,
    answers: {},
    result: null,
    error: '',
  });
}

export function updateStageTestAnswer(questionId: string, answer: string): StageTestSessionState {
  return setStageTestSession((current) => ({
    ...current,
    answers: {
      ...current.answers,
      [questionId]: answer,
    },
  }));
}

export function setStageTestSubmitting(): StageTestSessionState {
  return setStageTestSession((current) => ({
    ...current,
    status: 'submitting',
    error: '',
  }));
}

export function completeStageTestSession(result: PracticeJudgeResult): StageTestSessionState {
  return setStageTestSession((current) => ({
    ...current,
    status: 'completed',
    result,
    error: '',
  }));
}

export function failStageTestSession(error: string): StageTestSessionState {
  return setStageTestSession((current) => ({
    ...current,
    status: 'failed',
    error,
  }));
}

export function closeStageTestSession(): StageTestSessionState {
  return setStageTestSession((current) => ({
    ...current,
    open: false,
  }));
}

export function clearStageTestSession(): StageTestSessionState {
  return setStageTestSession(DEFAULT_STATE);
}

function setStageTestSession(
  updater: StageTestSessionState | ((current: StageTestSessionState) => StageTestSessionState),
): StageTestSessionState {
  state = typeof updater === 'function' ? updater(state) : updater;
  persistState(state);
  listeners.forEach((listener) => listener(state));
  return state;
}

function loadStoredState(): StageTestSessionState {
  if (typeof window === 'undefined') {
    return DEFAULT_STATE;
  }
  try {
    const parsed = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) || 'null') as StageTestSessionState | null;
    if (!parsed || typeof parsed !== 'object' || !parsed.batch) {
      return DEFAULT_STATE;
    }
    return {
      ...DEFAULT_STATE,
      ...parsed,
      open: Boolean(parsed.open),
      answers: parsed.answers && typeof parsed.answers === 'object' ? parsed.answers : {},
    };
  } catch {
    window.sessionStorage.removeItem(STORAGE_KEY);
    return DEFAULT_STATE;
  }
}

function persistState(nextState: StageTestSessionState): void {
  if (typeof window === 'undefined') {
    return;
  }
  if (!nextState.batch) {
    window.sessionStorage.removeItem(STORAGE_KEY);
    return;
  }
  window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(nextState));
}
