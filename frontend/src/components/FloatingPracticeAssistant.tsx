import { useEffect, useRef, useState } from 'react';
import {
  CheckCircle2,
  ClipboardList,
  Loader2,
  X,
  XCircle,
} from 'lucide-react';
import { conversationApi } from '../api/conversation';
import { getErrorMessage } from '../api/request';
import { smartEngineApi } from '../api/smartEngine';
import { readStreamMessage, readStreamPayload } from '../api/sse';
import {
  readPracticeJudgeResult,
} from '../pages/LearningStudioDemoPage.utils';
import type { AuthUser } from '../api/auth';
import type { JudgeItemResult, PracticeJudgeResult, PracticeQuestionBatch } from '../pages/LearningStudioDemoPage.types';
import {
  clearPracticeSession,
  getPracticeSessionState,
  recordPracticeJudgeResult,
  setPracticeJudgeStatus,
  setPracticeSessionOpen,
  subscribePracticeSession,
  type PracticeJudgeStatus,
  type PracticeSessionState,
} from '../pages/practiceSessionStore';

export default function FloatingPracticeAssistant(props: {
  isAuthenticated: boolean;
  currentUser: AuthUser | null;
  openAuthModal: (tab?: 'login' | 'register', hint?: string) => void;
}) {
  const currentUserId = normalizeUserId(props.currentUser);
  const [session, setSession] = useState<PracticeSessionState>(() => ({
    batch: null,
    open: false,
    source: '',
    perQuestionResults: {},
    judgeResult: null,
    judgeStatus: 'idle',
    error: '',
  }));
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => subscribePracticeSession(setSession), []);

  useEffect(() => {
    abortRef.current?.abort();
  }, [currentUserId]);

  useEffect(() => {
    if (!session.batch) {
      return;
    }
    if (!props.isAuthenticated || !currentUserId || session.ownerUserId !== currentUserId) {
      abortRef.current?.abort();
      clearPracticeSession();
    }
  }, [currentUserId, props.isAuthenticated, session.batch, session.ownerUserId]);

  useEffect(() => () => {
    abortRef.current?.abort();
  }, []);

  const submitAnswer = async (batch: PracticeQuestionBatch, questionId: string, answer: string) => {
    if (!props.isAuthenticated || !currentUserId) {
      props.openAuthModal('login', '请先登录');
      return;
    }
    if (!session.ownerUserId || session.ownerUserId !== currentUserId) {
      abortRef.current?.abort();
      clearPracticeSession();
      return;
    }
    abortRef.current?.abort();
    const abortController = new AbortController();
    abortRef.current = abortController;
    const requestOwnerUserId = currentUserId;
    setPracticeJudgeStatus('submitting');
    const question = batch.questions.find((item) => item.questionId === questionId);
    if (!question) {
      setPracticeJudgeStatus('failed', '未找到当前题目，请重新打开练习助手');
      return;
    }

    try {
      const targetConversationId = session.conversationId?.trim()
        || (await conversationApi.createConversation()).conversationId;
      const batchTopic = batch.topic || batch.title || '练习题';
      const singleQuestionBatch: PracticeQuestionBatch = {
        ...batch,
        title: `${batch.title} · ${questionId}`,
        questions: [question],
      };
      const submitResp = await smartEngineApi.submit({
        conversationId: targetConversationId,
        serviceType: 'PRACTICE_JUDGE',
        params: {
          topic: batchTopic,
          query: `${batchTopic} 练习题判题`,
          practiceQuestionBatch: singleQuestionBatch,
          practiceQuestions: singleQuestionBatch.questions,
          answers: { [questionId]: answer },
          learningContext: {
            chapter: batchTopic,
          },
        },
      });

      let received = false;
      setPracticeStatusForOwner(requestOwnerUserId, 'judging');
      await smartEngineApi.streamTask(
        submitResp.taskId,
        {
          onEvent: (event) => {
            const payload = event.payload ?? readStreamPayload(event.data);
            if (event.event === 'judge_result') {
              const result = readPracticeJudgeResult(payload);
              if (result) {
                received = true;
                recordPracticeResultForOwner(result, requestOwnerUserId);
              }
            }
            if (event.event === 'error') {
              setPracticeStatusForOwner(requestOwnerUserId, 'failed', readStreamMessage(payload) || '判题失败，请稍后重试');
            }
          },
          onDone: () => undefined,
          onError: (error) => {
            if (!abortController.signal.aborted) {
              setPracticeStatusForOwner(requestOwnerUserId, 'failed', getErrorMessage(error));
            }
          },
        },
        abortController.signal,
      );
      if (abortController.signal.aborted) {
        return;
      }
      if (!received) {
        const task = await smartEngineApi.getTask(submitResp.taskId, { dedupe: false, retry: 2 });
        const result = readPracticeJudgeResult(task.responseSummary);
        if (result) {
          recordPracticeResultForOwner(result, requestOwnerUserId);
          return;
        }
      }
      setPracticeStatusForOwner(requestOwnerUserId, 'failed', '未收到完整判题结果，请稍后在错题本或画像中查看同步结果');
    } catch (error) {
      if (!abortController.signal.aborted) {
        setPracticeStatusForOwner(requestOwnerUserId, 'failed', getErrorMessage(error));
      }
    } finally {
      if (abortRef.current === abortController) {
        abortRef.current = null;
      }
    }
  };

  return (
    <PracticeFloatingPanel
      batch={session.batch}
      open={session.open}
      perQuestionResults={session.perQuestionResults}
      judgeStatus={session.judgeStatus}
      errorMessage={session.error}
      onOpen={() => setPracticeSessionOpen(true)}
      onClose={() => setPracticeSessionOpen(false)}
      onSubmitAnswer={submitAnswer}
    />
  );
}

function PracticeFloatingPanel(props: {
  batch: PracticeQuestionBatch | null;
  open: boolean;
  perQuestionResults: Record<string, JudgeItemResult>;
  judgeStatus: PracticeJudgeStatus;
  errorMessage: string;
  onOpen: () => void;
  onClose: () => void;
  onSubmitAnswer: (batch: PracticeQuestionBatch, questionId: string, answer: string) => void;
}) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const batch = props.batch;
  useEffect(() => {
    setCurrentIndex(0);
    setAnswers({});
  }, [batch?.title, batch?.topic]);

  if (!batch) {
    return null;
  }

  const currentQuestion = batch.questions[Math.min(currentIndex, Math.max(batch.questions.length - 1, 0))];
  const currentAnswer = currentQuestion ? answers[currentQuestion.questionId] || '' : '';
  const currentResult = currentQuestion ? props.perQuestionResults[currentQuestion.questionId] : undefined;
  const judgedCount = batch.questions.filter((question) => props.perQuestionResults[question.questionId]).length;
  const busy = props.judgeStatus === 'submitting' || props.judgeStatus === 'judging';
  const canSubmit = Boolean(currentQuestion && currentAnswer.trim() && !busy && !currentResult);
  const canGoNext = Boolean(currentResult && currentIndex < batch.questions.length - 1);

  if (!props.open) {
    return (
      <button
        type="button"
        onClick={props.onOpen}
        className="practice-assistant-trigger fixed bottom-24 right-5 z-[120] inline-flex items-center gap-2 rounded-full bg-white/90 px-4 py-2 text-sm font-semibold text-amber-700 shadow-lg shadow-amber-100/60 backdrop-blur transition hover:bg-amber-50 dark:bg-slate-900/90 dark:text-amber-200 dark:shadow-none"
      >
        <ClipboardList className="h-4 w-4" />
        练习题 {judgedCount}/{batch.questions.length}
      </button>
    );
  }

  return (
    <div className="practice-assistant-panel fixed bottom-5 right-5 z-[120] w-[min(calc(100vw-40px),440px)]">
      <section className="practice-assistant-surface flex max-h-[78vh] flex-col overflow-hidden rounded-[24px] bg-white/92 shadow-2xl shadow-amber-100/70 backdrop-blur dark:bg-slate-950/92 dark:shadow-slate-950/40">
        <header className="bg-white/54 px-5 py-4 dark:bg-slate-950/24">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-full bg-amber-50 px-2.5 py-1 text-xs font-semibold text-amber-700 dark:bg-amber-500/10 dark:text-amber-200">
                  浮动练习助手
                </span>
                <span className="text-xs text-slate-400 dark:text-slate-500">
                  第 {currentIndex + 1}/{batch.questions.length} 题
                </span>
              </div>
              <h2 className="mt-2 truncate text-base font-bold text-slate-950 dark:text-white">{batch.title}</h2>
              <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-slate-400">
                {batch.topic || '随堂练习'} · 已完成 {judgedCount}/{batch.questions.length} 题
              </p>
            </div>
            <button
              type="button"
              onClick={props.onClose}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200"
              aria-label="关闭练习弹窗"
              title="关闭"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          {batch.description ? (
            <p className="mt-3 rounded-xl bg-amber-50/60 px-3 py-2 text-sm leading-6 text-amber-800 dark:bg-amber-500/10 dark:text-amber-100">
              {batch.description}
            </p>
          ) : null}
        </header>

        <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
          {currentQuestion ? (
            <div className="rounded-xl bg-slate-50/70 p-4 dark:bg-slate-900/50">
              <div className="flex items-center justify-between gap-3 text-xs text-slate-400 dark:text-slate-500">
                <span>{formatPracticeQuestionType(currentQuestion.questionType)}</span>
                {currentQuestion.difficultyLevel ? <span>{formatPracticeDifficulty(currentQuestion.difficultyLevel)}</span> : null}
              </div>
              <div className="mt-2 text-sm font-semibold leading-6 text-slate-800 dark:text-slate-100">
                {currentIndex + 1}. {currentQuestion.stem}
              </div>
              {currentQuestion.options?.length ? (
                <div className="mt-3 space-y-2">
                  {currentQuestion.options.map((option, optionIndex) => {
                    const optionLabel = String.fromCharCode(65 + optionIndex);
                    const checked = currentAnswer === optionLabel;
                    return (
                      <label
                        key={`${currentQuestion.questionId}-${optionLabel}`}
                        className={`flex cursor-pointer items-start gap-2 rounded-lg px-3 py-2 text-sm transition ${
                          checked
                            ? 'bg-primary-50 text-primary-800 shadow-sm shadow-primary-100/45 dark:bg-primary-500/10 dark:text-primary-100 dark:shadow-none'
                            : 'bg-white/86 text-slate-600 hover:bg-primary-50/60 dark:bg-slate-950/80 dark:text-slate-300'
                        }`}
                      >
                        <input
                          type="radio"
                          name={currentQuestion.questionId}
                          checked={checked}
                          disabled={Boolean(currentResult)}
                          onChange={() => setAnswers((prev) => ({ ...prev, [currentQuestion.questionId]: optionLabel }))}
                          className="mt-1"
                        />
                        <span>{optionLabel}. {option}</span>
                      </label>
                    );
                  })}
                </div>
              ) : (
                <textarea
                  value={currentAnswer}
                  disabled={Boolean(currentResult)}
                  onChange={(event) => setAnswers((prev) => ({ ...prev, [currentQuestion.questionId]: event.target.value }))}
                  rows={4}
                  className="mt-3 w-full rounded-xl bg-white/86 px-3.5 py-2.5 text-sm outline-none shadow-sm shadow-slate-200/18 transition focus:bg-white focus:shadow-md focus:shadow-primary-100/30 disabled:bg-slate-100 dark:bg-slate-950/80 dark:text-slate-100 dark:shadow-none dark:focus:bg-slate-950 dark:disabled:bg-slate-900"
                  placeholder="请输入你的答案"
                />
              )}
              {currentQuestion.knowledgeTags?.length ? (
                <div className="mt-3 flex flex-wrap gap-2">
                  {currentQuestion.knowledgeTags.map((tag) => (
                    <span key={tag} className="rounded-full bg-white/76 px-2 py-1 text-xs text-slate-500 dark:bg-slate-950/54 dark:text-slate-400">
                      {tag}
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}

          {props.errorMessage ? (
            <div className="mt-4 rounded-xl bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-200">
              {props.errorMessage}
            </div>
          ) : null}

          {currentResult ? <PracticeQuestionResultView result={currentResult} /> : null}
        </div>

        <footer className="bg-white/54 px-5 py-4 dark:bg-slate-950/24">
          {currentResult ? (
            <button
              type="button"
              disabled={!canGoNext}
              onClick={() => setCurrentIndex((index) => Math.min(index + 1, batch.questions.length - 1))}
              className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-primary-600 px-4 text-sm font-semibold text-white transition hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <CheckCircle2 className="h-4 w-4" />
              {canGoNext ? '下一题' : '已完成本套练习'}
            </button>
          ) : (
            <button
              type="button"
              disabled={!canSubmit}
              onClick={() => {
                if (currentQuestion) {
                  props.onSubmitAnswer(batch, currentQuestion.questionId, currentAnswer);
                }
              }}
              className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-primary-600 px-4 text-sm font-semibold text-white transition hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
              {busy ? '正在判题' : '提交本题'}
            </button>
          )}
        </footer>
      </section>
    </div>
  );
}

function PracticeQuestionResultView({ result }: { result: JudgeItemResult }) {
  return (
    <div className="mt-5 rounded-xl bg-emerald-50/70 p-4 dark:bg-emerald-500/10">
      <div className="flex items-center gap-2 text-sm font-semibold text-slate-800 dark:text-slate-100">
        {result.isCorrect ? <CheckCircle2 className="h-4 w-4 text-emerald-500" /> : <XCircle className="h-4 w-4 text-rose-500" />}
        {result.isCorrect ? '回答正确' : '需要修正'}
      </div>
      <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">你的答案：{result.learnerAnswer || '未作答'}</p>
      {result.correctAnswer ? <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">标准答案：{result.correctAnswer}</p> : null}
      {result.reason ? <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-slate-400">解析：{result.reason}</p> : null}
      {result.feedback ? <p className="mt-1 text-sm leading-6 text-slate-600 dark:text-slate-400">下一步建议：{result.feedback}</p> : null}
      {result.knowledgeTags?.length ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {result.knowledgeTags.map((tag) => (
            <span key={tag} className="rounded-full bg-white px-2.5 py-1 text-xs font-semibold text-emerald-700 dark:bg-slate-950 dark:text-emerald-200">
              {tag}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function formatPracticeQuestionType(value: string | undefined): string {
  switch ((value || '').trim().toUpperCase()) {
    case 'SINGLE_CHOICE':
      return '单选题';
    case 'SHORT_ANSWER':
      return '简答题';
    default:
      return value?.trim() || '题目';
  }
}

function formatPracticeDifficulty(value: string): string {
  switch (value.trim().toUpperCase()) {
    case 'BASIC':
    case 'EASY':
      return '基础';
    case 'INTERMEDIATE':
    case 'MEDIUM':
      return '中等';
    case 'ADVANCED':
    case 'HARD':
      return '进阶';
    default:
      return value;
  }
}

function normalizeUserId(user: AuthUser | null): string {
  const rawId = user?.userId ?? user?.id;
  if (rawId === undefined || rawId === null) {
    return '';
  }
  return String(rawId).trim();
}

function recordPracticeResultForOwner(result: PracticeJudgeResult, ownerUserId: string): void {
  const current = getPracticeSessionState();
  if (!ownerUserId || current.ownerUserId !== ownerUserId) {
    return;
  }
  recordPracticeJudgeResult(result);
}

function setPracticeStatusForOwner(ownerUserId: string, status: PracticeJudgeStatus, error = ''): void {
  const current = getPracticeSessionState();
  if (!ownerUserId || current.ownerUserId !== ownerUserId) {
    return;
  }
  setPracticeJudgeStatus(status, error);
}
