import { useEffect, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardCheck,
  Loader2,
  RotateCcw,
  X,
  XCircle,
} from 'lucide-react';
import { conversationApi } from '../api/conversation';
import { getErrorMessage } from '../api/request';
import { smartEngineApi } from '../api/smartEngine';
import { readStreamMessage, readStreamPayload } from '../api/sse';
import {
  readPracticeJudgeResult,
} from './LearningStudioDemoPage.taskPayloadReaders';
import type { JudgeItemResult, PracticeJudgeResult, PracticeQuestion, PracticeQuestionBatch } from './LearningStudioDemoPage.types';
import {
  clearStageTestSession,
  closeStageTestSession,
  completeStageTestSession,
  failStageTestSession,
  setStageTestSubmitting,
  subscribeStageTestSession,
  updateStageTestAnswer,
  type StageTestSessionState,
} from './stageTestSessionStore';
import { dispatchStageTestCompleted, type StageTestCompletedDetail } from './stageTestEvents';

const PASS_SCORE = 80;

export default function StageTestExamPage() {
  const [session, setSession] = useState<StageTestSessionState>({
    open: false,
    status: 'idle',
    batch: null,
    answers: {},
    result: null,
    error: '',
  });
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => subscribeStageTestSession(setSession), []);
  useEffect(() => () => {
    abortRef.current?.abort();
  }, []);

  if (!session.open || !session.batch) {
    return null;
  }

  const submitExam = async () => {
    if (!session.batch || session.status === 'submitting') {
      return;
    }
    const currentBatch = session.batch;
    const missing = unansweredQuestions(currentBatch, session.answers);
    if (missing.length) {
      failStageTestSession(`还有 ${missing.length} 道题未作答，请完成后再提交`);
      return;
    }

    abortRef.current?.abort();
    const abortController = new AbortController();
    abortRef.current = abortController;
    setStageTestSubmitting();
    try {
      const targetConversationId = session.conversationId?.trim()
        || (await conversationApi.createConversation()).conversationId;
      const batchTopic = session.batch.topic || session.phaseTitle || session.batch.title || '阶段测试';
      const submitResp = await smartEngineApi.submit({
        conversationId: targetConversationId,
        serviceType: 'PRACTICE_JUDGE',
        params: {
          purpose: 'STAGE_TEST',
          topic: batchTopic,
          query: `${batchTopic} 阶段测试批改`,
          practiceQuestionBatch: currentBatch,
          practiceQuestions: currentBatch.questions,
          answers: session.answers,
          learningContext: {
            activeLearningStepId: session.phaseId,
            activeLearningStepTitle: session.phaseTitle || batchTopic,
            chapter: session.phaseTitle || batchTopic,
            questionCount: currentBatch.questions.length,
          },
        },
      });

      let receivedResult: PracticeJudgeResult | null = null;
      let streamError = '';
      let completedDetail: StageTestCompletedDetail | null = null;
      const completeExamWithResult = (result: PracticeJudgeResult) => {
        if (receivedResult) {
          return;
        }
        const normalizedResult = normalizeExamResult(result, currentBatch);
        const score = normalizeDisplayScore(normalizedResult);
        receivedResult = normalizedResult;
        completeStageTestSession(normalizedResult);
        completedDetail = {
          phaseId: session.phaseId || '',
          phaseTitle: session.phaseTitle || batchTopic,
          taskId: submitResp.taskId,
          score,
          passed: score >= PASS_SCORE,
        };
      };
      await smartEngineApi.streamTask(
        submitResp.taskId,
        {
          onEvent: (event) => {
            const payload = event.payload ?? readStreamPayload(event.data);
            if (event.event === 'judge_result') {
              const result = readPracticeJudgeResult(payload);
              if (result) {
                completeExamWithResult(result);
              }
            }
            if (event.event === 'error') {
              streamError = readStreamMessage(payload) || '阶段测试批改失败';
            }
          },
          onDone: () => undefined,
          onError: (error) => {
            streamError = getErrorMessage(error);
          },
        },
        abortController.signal,
      );
      if (abortController.signal.aborted) {
        return;
      }
      if (streamError) {
        throw new Error(streamError);
      }
      if (completedDetail) {
        dispatchStageTestCompleted(completedDetail);
        return;
      }
      if (!receivedResult) {
        const task = await smartEngineApi.getTask(submitResp.taskId, { dedupe: false, retry: 2 });
        const result = readPracticeJudgeResult(task.responseSummary);
        if (result) {
          completeExamWithResult(result);
          if (completedDetail) {
            dispatchStageTestCompleted(completedDetail);
          }
          return;
        }
        throw new Error('未收到完整批改结果');
      }
    } catch (error) {
      if (!abortController.signal.aborted) {
        failStageTestSession(getErrorMessage(error));
      }
    } finally {
      if (abortRef.current === abortController) {
        abortRef.current = null;
      }
    }
  };

  return (
    <StageTestOverlay
      session={session}
      onClose={closeStageTestSession}
      onClear={clearStageTestSession}
      onAnswer={updateStageTestAnswer}
      onSubmit={() => void submitExam()}
    />
  );
}

function StageTestOverlay(props: {
  session: StageTestSessionState;
  onClose: () => void;
  onClear: () => void;
  onAnswer: (questionId: string, answer: string) => void;
  onSubmit: () => void;
}) {
  const { session } = props;
  const batch = session.batch;
  if (!batch) {
    return null;
  }
  const answeredCount = batch.questions.filter((question) => session.answers[question.questionId]?.trim()).length;
  const totalCount = batch.questions.length;
  const progress = totalCount ? Math.round((answeredCount / totalCount) * 100) : 0;
  const busy = session.status === 'submitting';
  const completed = session.status === 'completed' && session.result;

  return (
    <div className="fixed inset-0 z-[200] overflow-y-auto bg-slate-950/55 backdrop-blur-sm">
      <section className="min-h-screen bg-slate-50 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
        <header className="sticky top-0 z-10 bg-white/92 px-4 py-3 shadow-[0_10px_30px_rgba(37,99,235,0.06)] backdrop-blur-xl dark:bg-slate-950/92 dark:shadow-slate-950/20 sm:px-6">
          <div className="mx-auto flex max-w-[1180px] items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="inline-flex items-center gap-1.5 rounded-full bg-primary-50 px-2.5 py-1 text-xs font-semibold text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
                  <ClipboardCheck className="h-3.5 w-3.5" />
                  阶段测试
                </span>
                <span className="text-xs text-slate-400 dark:text-slate-500">{answeredCount}/{totalCount} 已答</span>
              </div>
              <h1 className="mt-1 truncate text-base font-bold text-slate-950 dark:text-white sm:text-lg">
                {session.phaseTitle || batch.title}
              </h1>
            </div>
            <div className="flex items-center gap-2">
              {completed ? (
                <button
                  type="button"
                  onClick={props.onClear}
                  className="inline-flex h-9 items-center gap-2 rounded-xl bg-slate-50/80 px-3 text-sm font-semibold text-slate-600 transition hover:bg-primary-50/80 hover:text-primary-700 dark:bg-slate-900/70 dark:text-slate-300"
                >
                  <RotateCcw className="h-4 w-4" />
                  结束
                </button>
              ) : null}
              <button
                type="button"
                onClick={props.onClose}
                className="inline-flex h-9 w-9 items-center justify-center rounded-xl text-slate-500 transition hover:bg-slate-100 hover:text-slate-800 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
                title="收起"
                aria-label="收起阶段测试"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
        </header>

        <main className="mx-auto grid max-w-[1180px] gap-5 px-4 py-5 sm:px-6 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="space-y-4">
            {batch.questions.map((question, index) => (
              <QuestionBlock
                key={question.questionId}
                index={index}
                question={question}
                answer={session.answers[question.questionId] || ''}
                result={session.result?.items.find((item) => item.questionId === question.questionId)}
                locked={busy || Boolean(completed)}
                onAnswer={(answer) => props.onAnswer(question.questionId, answer)}
              />
            ))}
          </div>

          <aside className="h-fit rounded-2xl bg-white/86 p-4 shadow-[0_16px_40px_rgba(37,99,235,0.09)] backdrop-blur dark:bg-slate-900/82 dark:shadow-slate-950/20 lg:sticky lg:top-20">
            <div className="text-sm font-bold text-slate-900 dark:text-white">答题进度</div>
            <div className="mt-3 h-2 rounded-full bg-slate-200 dark:bg-slate-800">
              <div className="h-full rounded-full bg-primary-600" style={{ width: `${Math.max(progress, answeredCount ? 8 : 0)}%` }} />
            </div>
            <div className="mt-2 text-xs text-slate-500 dark:text-slate-400">{progress}% · {answeredCount}/{totalCount} 道</div>
            <QuestionGrid questions={batch.questions} answers={session.answers} result={session.result} />
            {session.error ? (
              <div className="mt-4 rounded-xl bg-rose-50 px-3 py-2 text-sm text-rose-700 shadow-sm shadow-rose-100/70 dark:bg-rose-500/10 dark:text-rose-200 dark:shadow-none">
                {session.error}
              </div>
            ) : null}
            {completed && session.result ? <ResultSummary result={session.result} /> : null}
            {!completed ? (
              <button
                type="button"
                disabled={busy || answeredCount !== totalCount}
                onClick={props.onSubmit}
                className="mt-4 inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-primary-600 px-4 text-sm font-semibold text-white shadow-lg shadow-primary-500/20 transition hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                {busy ? '正在统一批改' : '提交整套试卷'}
              </button>
            ) : null}
          </aside>
        </main>
      </section>
    </div>
  );
}

function QuestionBlock(props: {
  index: number;
  question: PracticeQuestion;
  answer: string;
  result?: JudgeItemResult;
  locked: boolean;
  onAnswer: (answer: string) => void;
}) {
  const { question, result } = props;
  const isChoice = question.questionType === 'SINGLE_CHOICE' && question.options?.length;
  return (
    <article className="rounded-2xl bg-white/86 p-4 shadow-[0_12px_34px_rgba(37,99,235,0.07)] backdrop-blur transition-colors hover:bg-white/92 dark:bg-slate-900/82 dark:shadow-slate-950/20 dark:hover:bg-slate-900/90">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400 dark:text-slate-500">
        <span>第 {props.index + 1} 题 · {questionTypeLabel(question.questionType)}</span>
        {question.difficultyLevel ? <span>{difficultyLabel(question.difficultyLevel)}</span> : null}
      </div>
      <div className="mt-3 text-sm font-semibold leading-7 text-slate-800 dark:text-slate-100">
        {question.stem}
      </div>
      {isChoice ? (
        <div className="mt-4 grid gap-2 sm:grid-cols-2">
          {(question.options ?? []).map((option, optionIndex) => {
            const label = String.fromCharCode(65 + optionIndex);
            const checked = props.answer === label;
            return (
              <button
                key={`${question.questionId}-${label}`}
                type="button"
                disabled={props.locked}
                onClick={() => props.onAnswer(label)}
                className={`min-h-11 rounded-xl px-3 py-2 text-left text-sm leading-6 transition disabled:cursor-default ${
                  checked
                    ? 'bg-primary-600 text-white shadow-sm shadow-primary-500/20 dark:bg-primary-500 dark:text-white'
                    : 'bg-slate-50/80 text-slate-600 hover:bg-primary-50/80 hover:text-primary-700 dark:bg-slate-950/70 dark:text-slate-300'
                }`}
              >
                <span className="font-semibold">{label}.</span> {option}
              </button>
            );
          })}
        </div>
      ) : (
        <textarea
          value={props.answer}
          disabled={props.locked}
          onChange={(event) => props.onAnswer(event.target.value)}
          rows={5}
          className="mt-4 w-full rounded-xl bg-slate-50/80 px-3.5 py-2.5 text-sm leading-6 outline-none transition focus:bg-white focus:shadow-md focus:shadow-primary-100/30 disabled:bg-slate-100 dark:bg-slate-950/70 dark:text-slate-100 dark:focus:bg-slate-950 dark:focus:shadow-primary-950/24 dark:disabled:bg-slate-900"
          placeholder="请输入你的答案"
        />
      )}
      {question.knowledgeTags?.length ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {question.knowledgeTags.map((tag) => (
            <span key={tag} className="rounded-full bg-slate-100 px-2 py-1 text-xs text-slate-500 dark:bg-slate-800 dark:text-slate-400">
              {tag}
            </span>
          ))}
        </div>
      ) : null}
      {result ? <QuestionResult result={result} /> : null}
    </article>
  );
}

function QuestionResult({ result }: { result: JudgeItemResult }) {
  return (
    <div className={`mt-4 rounded-xl px-3 py-2 text-sm ${
      result.isCorrect
        ? 'bg-emerald-50 text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-200'
        : 'bg-amber-50 text-amber-800 dark:bg-amber-500/10 dark:text-amber-100'
    }`}
    >
      <div className="flex items-center gap-2 font-semibold">
        {result.isCorrect ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
        {result.isCorrect ? '掌握情况良好' : '这里还需要补强'}
      </div>
      {result.correctAnswer ? <p className="mt-2">标准答案：{result.correctAnswer}</p> : null}
      {result.reason ? <p className="mt-1 leading-6">解析：{result.reason}</p> : null}
      {result.feedback ? <p className="mt-1 leading-6">建议：{result.feedback}</p> : null}
    </div>
  );
}

function QuestionGrid(props: {
  questions: PracticeQuestion[];
  answers: Record<string, string>;
  result: PracticeJudgeResult | null;
}) {
  return (
    <div className="mt-4 grid grid-cols-5 gap-2">
      {props.questions.map((question, index) => {
        const judged = props.result?.items.find((item) => item.questionId === question.questionId);
        const answered = props.answers[question.questionId]?.trim();
        return (
          <div
            key={question.questionId}
            className={`flex h-9 items-center justify-center rounded-xl text-xs font-bold ${
              judged
                ? judged.isCorrect
                  ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-200'
                  : 'bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-100'
                : answered
                  ? 'bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-200'
                  : 'bg-slate-100 text-slate-400 dark:bg-slate-800'
            }`}
          >
            {index + 1}
          </div>
        );
      })}
    </div>
  );
}

function ResultSummary({ result }: { result: PracticeJudgeResult }) {
  const score = normalizeDisplayScore(result);
  const passed = score >= PASS_SCORE;
  return (
    <div className={`mt-4 rounded-2xl p-4 ${
      passed
        ? 'bg-emerald-50/70 dark:bg-emerald-500/10'
        : 'bg-amber-50/70 dark:bg-amber-500/10'
    }`}
    >
      <div className="flex items-center gap-2 text-sm font-bold text-slate-900 dark:text-white">
        {passed ? <CheckCircle2 className="h-4 w-4 text-emerald-500" /> : <AlertTriangle className="h-4 w-4 text-amber-500" />}
        {passed ? '阶段测试通过' : '阶段测试未通过'}
      </div>
      <div className="mt-3 text-3xl font-bold text-slate-950 dark:text-white">{score}</div>
      <div className="text-xs text-slate-500 dark:text-slate-400">通过线 {PASS_SCORE} 分</div>
      {result.summary ? <p className="mt-3 text-sm leading-6 text-slate-600 dark:text-slate-300">{result.summary}</p> : null}
      {result.weakKnowledgeTags?.length ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {result.weakKnowledgeTags.map((tag) => (
            <span key={tag} className="rounded-full bg-white/86 px-2.5 py-1 text-xs font-semibold text-slate-600 dark:bg-slate-950/70 dark:text-slate-300">
              {tag}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function normalizeExamResult(result: PracticeJudgeResult, batch: PracticeQuestionBatch): PracticeJudgeResult {
  const rawScore = result.totalScore || 0;
  const accuracyScore = result.accuracy > 0 && result.accuracy <= 1
    ? Math.round(result.accuracy * 100)
    : undefined;
  const maxPerQuestionScore = Math.max(...result.items.map((item) => item.score), 0);
  const totalPossibleScore = Math.max(batch.questions.length * Math.max(maxPerQuestionScore, 1), 1);
  const normalizedScore = rawScore > 100
    ? Math.round((rawScore / totalPossibleScore) * 100)
    : accuracyScore !== undefined && maxPerQuestionScore > 10 && Math.abs(rawScore - accuracyScore) > 10
      ? accuracyScore
      : rawScore > 0
        ? Math.round(rawScore)
        : accuracyScore ?? 0;
  const totalScore = Math.max(0, Math.min(100, normalizedScore));
  return {
    ...result,
    totalScore,
    accuracy: totalScore / 100,
  };
}

function normalizeDisplayScore(result: PracticeJudgeResult): number {
  return result.totalScore > 1 ? Math.round(result.totalScore) : Math.round(result.totalScore * 100);
}

function questionTypeLabel(value?: string): string {
  switch (value) {
    case 'SINGLE_CHOICE':
      return '选择题';
    case 'SHORT_ANSWER':
      return '简答题';
    default:
      return value || '题目';
  }
}

function difficultyLabel(value?: string): string {
  switch (value) {
    case 'BASIC':
      return '基础';
    case 'INTERMEDIATE':
      return '中等';
    case 'ADVANCED':
      return '进阶';
    default:
      return value || '';
  }
}

function unansweredQuestions(batch: PracticeQuestionBatch, answers: Record<string, string>): PracticeQuestion[] {
  return batch.questions.filter((question) => !answers[question.questionId]?.trim());
}
