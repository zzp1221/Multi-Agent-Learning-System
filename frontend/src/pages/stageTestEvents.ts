export const STAGE_TEST_COMPLETED_EVENT = 'app:stage-test-completed';

export interface StageTestCompletedDetail {
  phaseId: string;
  phaseTitle: string;
  taskId: string;
  score: number;
  passed: boolean;
}

export function dispatchStageTestCompleted(detail: StageTestCompletedDetail): void {
  if (typeof window === 'undefined') {
    return;
  }
  window.dispatchEvent(new CustomEvent<StageTestCompletedDetail>(STAGE_TEST_COMPLETED_EVENT, { detail }));
}
