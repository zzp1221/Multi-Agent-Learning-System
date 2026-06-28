import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link, useOutletContext } from 'react-router-dom';
import {
  BookOpenCheck,
  CheckCircle2,
  Clock3,
  ClipboardList,
  Filter,
  LoaderCircle,
  Play,
  RotateCcw,
  Save,
  Search,
  Sparkles,
  Target,
  TriangleAlert,
  XCircle,
} from 'lucide-react';
import { getErrorMessage } from '../api/request';
import { conversationApi } from '../api/conversation';
import { smartEngineApi } from '../api/smartEngine';
import { readStreamMessage, readStreamPayload } from '../api/sse';
import {
  mistakesApi,
  type MistakeCampGroup,
  type MistakeListResponse,
  type MistakeRecordResponse,
  type MistakeReviewSessionResponse,
  type MistakeStatus,
  type MistakeTrainingCampResponse,
  type TrainingMicroPractice,
  type MistakeUpdateRequest,
} from '../api/mistakes';
import type { LayoutOutletContext } from '../components/Layout';
import type { PracticeQuestionBatch } from './LearningStudioDemoPage.types';
import { readPracticeQuestionBatch } from './LearningStudioDemoPage.taskPayloadReaders';
import { buildPracticeJudgeParams } from './practiceSemanticScope';
import { openPracticeSession } from './practiceSessionStore';
import {
  VOICE_PAGE_ACTION_EVENT,
  consumeQueuedVoicePageAction,
  isVoicePageActionEvent,
} from '../utils/voicePageActions';

const STATUS_OPTIONS: Array<{ value: MistakeStatus; label: string }> = [
  { value: 'active', label: '未掌握' },
  { value: 'due', label: '今日复习' },
  { value: 'mastered', label: '已掌握' },
  { value: 'all', label: '全部' },
];

const DIFFICULTY_OPTIONS = [
  { value: '', label: '全部难度' },
  { value: 'BASIC', label: '基础' },
  { value: 'INTERMEDIATE', label: '中等' },
  { value: 'ADVANCED', label: '进阶' },
  { value: 'MIXED', label: '综合' },
];

const MISTAKE_TYPE_OPTIONS = [
  { value: '', label: '未分类' },
  { value: 'conceptual', label: '概念理解' },
  { value: 'procedural', label: '步骤方法' },
  { value: 'careless', label: '粗心失误' },
];

const QUALITY_OPTIONS = [
  { value: 0, label: '不会' },
  { value: 1, label: '有点模糊' },
  { value: 3, label: '基本会' },
  { value: 5, label: '很稳' },
];

const PAGE_SIZE = 12;

export default function MistakeBookPage() {
  const { isAuthenticated, currentUser, openAuthModal } = useOutletContext<LayoutOutletContext>();
  const [status, setStatus] = useState<MistakeStatus>('due');
  const [difficulty, setDifficulty] = useState('');
  const [tagInput, setTagInput] = useState('');
  const [knowledgeTag, setKnowledgeTag] = useState('');
  const [showAdvancedFilters, setShowAdvancedFilters] = useState(false);
  const [page, setPage] = useState(0);
  const [data, setData] = useState<MistakeListResponse | null>(null);
  const [mistakes, setMistakes] = useState<MistakeRecordResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [noteDrafts, setNoteDrafts] = useState<Record<string, string>>({});
  const [typeDrafts, setTypeDrafts] = useState<Record<string, string>>({});
  const [savingId, setSavingId] = useState('');
  const [reviewSession, setReviewSession] = useState<MistakeReviewSessionResponse | null>(null);
  const [reviewQualities, setReviewQualities] = useState<Record<string, number>>({});
  const [reviewBusy, setReviewBusy] = useState(false);
  const [reviewMessage, setReviewMessage] = useState('');
  const [trainingCamps, setTrainingCamps] = useState<MistakeTrainingCampResponse | null>(null);
  const [trainingLoading, setTrainingLoading] = useState(false);
  const [trainingError, setTrainingError] = useState('');
  const [selectedCampId, setSelectedCampId] = useState('');
  const [microPracticeGeneratingId, setMicroPracticeGeneratingId] = useState('');
  const loadRequestIdRef = useRef(0);
  const trainingLoadRequestIdRef = useRef(0);
  const microPracticeAbortRef = useRef<AbortController | null>(null);

  const loadMistakes = useCallback(async (options?: { pageOverride?: number; replace?: boolean }) => {
    if (!isAuthenticated) {
      loadRequestIdRef.current += 1;
      setLoading(false);
      setData(null);
      setMistakes([]);
      setNoteDrafts({});
      setTypeDrafts({});
      setReviewSession(null);
      setReviewQualities({});
      setReviewMessage('');
      setTrainingCamps(null);
      setTrainingError('');
      setSelectedCampId('');
      setError('');
      return;
    }
    const requestId = loadRequestIdRef.current + 1;
    loadRequestIdRef.current = requestId;
    const pageToLoad = options?.pageOverride ?? page;
    const shouldReplace = options?.replace ?? pageToLoad === 0;
    setLoading(true);
    setError('');
    try {
      const nextData = await mistakesApi.list({
        status,
        knowledgeTag: knowledgeTag || undefined,
        difficulty: difficulty || undefined,
        page: pageToLoad,
        size: PAGE_SIZE,
      });
      if (loadRequestIdRef.current !== requestId) {
        return;
      }
      setData(nextData);
      setMistakes((current) => {
        if (shouldReplace) {
          return nextData.items;
        }
        const seenIds = new Set(current.map((item) => item.id));
        return [...current, ...nextData.items.filter((item) => !seenIds.has(item.id))];
      });
      setNoteDrafts((current) => {
        if (shouldReplace) {
          return Object.fromEntries(nextData.items.map((item) => [item.id, item.userNote || '']));
        }
        const nextDrafts = { ...current };
        nextData.items.forEach((item) => {
          if (nextDrafts[item.id] === undefined) {
            nextDrafts[item.id] = item.userNote || '';
          }
        });
        return nextDrafts;
      });
      setTypeDrafts((current) => {
        if (shouldReplace) {
          return Object.fromEntries(nextData.items.map((item) => [item.id, item.mistakeType || '']));
        }
        const nextDrafts = { ...current };
        nextData.items.forEach((item) => {
          if (nextDrafts[item.id] === undefined) {
            nextDrafts[item.id] = item.mistakeType || '';
          }
        });
        return nextDrafts;
      });
    } catch (loadError) {
      if (loadRequestIdRef.current === requestId) {
        setError(getErrorMessage(loadError));
      }
    } finally {
      if (loadRequestIdRef.current === requestId) {
        setLoading(false);
      }
    }
  }, [difficulty, isAuthenticated, knowledgeTag, page, status]);

  useEffect(() => {
    void loadMistakes();
  }, [loadMistakes]);

  const loadTrainingCamps = useCallback(async () => {
    if (!isAuthenticated) {
      trainingLoadRequestIdRef.current += 1;
      setTrainingLoading(false);
      setTrainingCamps(null);
      setTrainingError('');
      setSelectedCampId('');
      return;
    }
    const requestId = trainingLoadRequestIdRef.current + 1;
    trainingLoadRequestIdRef.current = requestId;
    setTrainingLoading(true);
    setTrainingError('');
    try {
      const nextCamps = await mistakesApi.trainingCamps();
      if (trainingLoadRequestIdRef.current !== requestId) {
        return;
      }
      setTrainingCamps(nextCamps);
      setSelectedCampId((current) => {
        if (current && nextCamps.camps.some((camp) => camp.campId === current)) {
          return current;
        }
        return nextCamps.camps[0]?.campId ?? '';
      });
    } catch (loadError) {
      if (trainingLoadRequestIdRef.current === requestId) {
        setTrainingError(getErrorMessage(loadError));
      }
    } finally {
      if (trainingLoadRequestIdRef.current === requestId) {
        setTrainingLoading(false);
      }
    }
  }, [isAuthenticated]);

  useEffect(() => {
    void loadTrainingCamps();
  }, [loadTrainingCamps]);

  useEffect(() => () => {
    microPracticeAbortRef.current?.abort();
  }, []);

  const stats = data?.stats ?? { dueCount: 0, activeCount: 0, masteredCount: 0 };
  const hasDueMistakes = stats.dueCount > 0;
  const hasMore = Boolean(data && mistakes.length < data.total);
  const activeFilterLabels = useMemo(() => {
    const labels: string[] = [];
    if (status !== 'due') {
      labels.push(statusLabel(status));
    }
    if (difficulty) {
      labels.push(difficultyLabel(difficulty));
    }
    if (knowledgeTag) {
      labels.push(`知识点：${knowledgeTag}`);
    }
    return labels;
  }, [difficulty, knowledgeTag, status]);

  const handleApplyFilters = () => {
    const nextKnowledgeTag = tagInput.trim();
    if (page === 0 && nextKnowledgeTag === knowledgeTag) {
      setShowAdvancedFilters(false);
      return;
    }
    setPage(0);
    setKnowledgeTag(nextKnowledgeTag);
    setShowAdvancedFilters(false);
    setMistakes([]);
    setData(null);
  };

  const handleClearAdvancedFilters = () => {
    const shouldReload = status !== 'due' || Boolean(difficulty) || Boolean(knowledgeTag) || page !== 0;
    setTagInput('');
    setKnowledgeTag('');
    setDifficulty('');
    setStatus('due');
    setPage(0);
    setShowAdvancedFilters(false);
    if (shouldReload) {
      setMistakes([]);
      setData(null);
    }
  };

  const handleStatusChange = (nextStatus: MistakeStatus) => {
    if (nextStatus === status) {
      return;
    }
    setStatus(nextStatus);
    setPage(0);
    setMistakes([]);
    setData(null);
  };

  const handleSave = async (item: MistakeRecordResponse) => {
    setSavingId(item.id);
    setError('');
    try {
      const payload: MistakeUpdateRequest = {
        userNote: noteDrafts[item.id] ?? '',
      };
      const selectedType = typeDrafts[item.id] as MistakeUpdateRequest['mistakeType'] | '';
      if (selectedType) {
        payload.mistakeType = selectedType;
      }
      await mistakesApi.update(item.id, payload);
      setPage(0);
      setMistakes([]);
      setData(null);
      await loadMistakes({ pageOverride: 0, replace: true });
      await loadTrainingCamps();
    } catch (saveError) {
      setError(getErrorMessage(saveError));
    } finally {
      setSavingId('');
    }
  };

  const handleToggleMastered = async (item: MistakeRecordResponse) => {
    setSavingId(item.id);
    setError('');
    try {
      await mistakesApi.update(item.id, { mastered: !item.mastered });
      setPage(0);
      setMistakes([]);
      setData(null);
      await loadMistakes({ pageOverride: 0, replace: true });
      await loadTrainingCamps();
    } catch (saveError) {
      setError(getErrorMessage(saveError));
    } finally {
      setSavingId('');
    }
  };

  const handleStartReview = useCallback(async () => {
    if (reviewBusy) {
      return;
    }
    if (!hasDueMistakes) {
      setReviewSession(null);
      setReviewMessage('当前没有可复习的错题');
      return;
    }
    setReviewBusy(true);
    setReviewMessage('');
    try {
      const session = await mistakesApi.createReviewSession({ limit: 8 });
      setReviewSession(session);
      setReviewQualities({});
    } catch (reviewError) {
      setReviewMessage(getErrorMessage(reviewError));
    } finally {
      setReviewBusy(false);
    }
  }, [hasDueMistakes, reviewBusy]);

  useEffect(() => {
    const startReview = () => {
      void handleStartReview();
    };
    if (consumeQueuedVoicePageAction('start_review')) {
      startReview();
    }
    const handleVoiceAction = (event: Event) => {
      if (isVoicePageActionEvent(event, 'start_review')) {
        startReview();
      }
    };
    window.addEventListener(VOICE_PAGE_ACTION_EVENT, handleVoiceAction);
    return () => {
      window.removeEventListener(VOICE_PAGE_ACTION_EVENT, handleVoiceAction);
    };
  }, [handleStartReview]);

  const handleSubmitReview = async () => {
    if (!reviewSession) {
      return;
    }
    const missing = reviewSession.items.some((item) => reviewQualities[item.id] === undefined);
    if (missing) {
      setReviewMessage('请先为每道错题选择掌握评分');
      return;
    }
    setReviewBusy(true);
    setReviewMessage('');
    try {
      const nextSession = await mistakesApi.submitReviewSession(reviewSession.sessionId, {
        items: reviewSession.items.map((item) => ({
          mistakeRecordId: item.id,
          quality: reviewQualities[item.id],
          isCorrect: reviewQualities[item.id] >= 3,
          answer: { quality: reviewQualities[item.id] },
        })),
      });
      setReviewSession(nextSession);
      setReviewMessage('复习结果已保存，下次复习时间已更新');
      setPage(0);
      setMistakes([]);
      setData(null);
      await loadMistakes({ pageOverride: 0, replace: true });
      await loadTrainingCamps();
    } catch (reviewError) {
      setReviewMessage(getErrorMessage(reviewError));
    } finally {
      setReviewBusy(false);
    }
  };

  const handleStartMicroPractice = async (camp: MistakeCampGroup, practice: TrainingMicroPractice) => {
    if (!currentUser || microPracticeGeneratingId) {
      return;
    }
    microPracticeAbortRef.current?.abort();
    const abortController = new AbortController();
    microPracticeAbortRef.current = abortController;
    const practiceId = `${camp.campId}:${practice.id}`;
    setMicroPracticeGeneratingId(practiceId);
    setTrainingError('');
    let receivedBatch: PracticeQuestionBatch | null = null;
    try {
      const conversation = await conversationApi.createConversation();
      const response = await smartEngineApi.submit({
        conversationId: conversation.conversationId,
        serviceType: 'PRACTICE_JUDGE',
        params: buildPracticeJudgeParams({
          source: 'MISTAKE_TRAINING_CAMP',
          purpose: 'MISTAKE_CAUSE_TRAINING',
          topic: camp.knowledgeTag,
          query: practice.prompt,
          count: 5,
          questionCount: 5,
          difficulty: practice.difficulty,
          knowledgeTags: practice.knowledgeTags,
          evidence: [
            practice.description,
            camp.explanation,
            ...camp.representativeMistakes.slice(0, 3).map((item) => item.stem),
          ],
          learningContext: {
            ...camp.practiceContext,
            chapter: camp.knowledgeTag,
            mistakeCampId: camp.campId,
            mistakeType: camp.mistakeType,
            knowledgeTags: practice.knowledgeTags,
            representativeMistakeIds: camp.representativeMistakes.map((item) => item.id),
            questionCount: 5,
          },
        }),
      });
      let streamError = '';
      await smartEngineApi.streamTask(response.taskId, {
        onEvent: (event) => {
          const payload = event.payload ?? readStreamPayload(event.data);
          if (event.event === 'question_batch') {
            const batch = readPracticeQuestionBatch(payload);
            if (batch) {
              receivedBatch = batch;
            }
          }
          if (event.event === 'error') {
            streamError = readStreamMessage(payload) || '微练习生成失败';
          }
        },
        onDone: () => undefined,
        onError: (streamFailure) => {
          streamError = getErrorMessage(streamFailure);
        },
      }, abortController.signal);
      if (abortController.signal.aborted) {
        return;
      }
      if (streamError) {
        throw new Error(streamError);
      }
      if (!receivedBatch) {
        const task = await smartEngineApi.getTask(response.taskId, { dedupe: false, retry: 2 });
        receivedBatch = readPracticeQuestionBatch(task.responseSummary);
      }
      if (!receivedBatch) {
        throw new Error('未收到完整微练习题目');
      }
      openPracticeSession({
        batch: receivedBatch,
        source: 'engine',
        ownerUserId: currentUser.userId ?? currentUser.id,
        phaseId: camp.campId,
        phaseTitle: camp.title,
        conversationId: conversation.conversationId,
      });
    } catch (practiceError) {
      if (!abortController.signal.aborted) {
        setTrainingError(getErrorMessage(practiceError));
      }
    } finally {
      if (microPracticeAbortRef.current === abortController) {
        microPracticeAbortRef.current = null;
      }
      setMicroPracticeGeneratingId('');
    }
  };

  const handleLoadMore = () => {
    if (loading || !hasMore) {
      return;
    }
    setPage((prev) => prev + 1);
  };

  if (!isAuthenticated) {
    return (
      <div className="mistake-page mx-auto max-w-[980px] px-1 pb-10 md:px-0">
        <div className="rounded-[24px] bg-white/76 p-8 text-center shadow-[0_14px_42px_rgba(54,86,140,0.08)] backdrop-blur dark:bg-slate-900/70 dark:shadow-slate-950/20">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary-50 text-primary-600 dark:bg-primary-500/10 dark:text-primary-300">
            <BookOpenCheck className="h-6 w-6" />
          </div>
          <h1 className="text-2xl font-semibold text-slate-800 dark:text-white">错题本</h1>
          <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">登录后可以查看自动沉淀的错题和复习计划。</p>
          <button
            type="button"
            onClick={() => openAuthModal('login', '登录后查看错题本')}
            className="mt-6 rounded-xl bg-primary-600 px-5 py-2.5 text-sm font-medium text-white shadow-lg shadow-primary-500/20 transition-colors hover:bg-primary-700"
          >
            登录查看
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="mistake-page mx-auto max-w-[1180px] space-y-5 px-1 pb-10 md:px-0">
      <section className="overflow-hidden rounded-[28px] bg-white/74 shadow-[0_18px_50px_rgba(37,99,235,0.10)] backdrop-blur dark:bg-slate-900/66 dark:shadow-slate-950/20">
        <div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_280px] md:items-center md:p-6">
          <div>
            <div className="inline-flex items-center gap-2 rounded-full bg-primary-50 px-3 py-1 text-xs font-medium text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
              <BookOpenCheck className="h-3.5 w-3.5" />
              今日复习
            </div>
            <h1 className="mt-3 text-2xl font-semibold tracking-tight text-slate-800 dark:text-white md:text-[32px]">
              先把今天该看的题过一遍
            </h1>
            <p className="mt-1.5 max-w-2xl text-sm leading-6 text-slate-500 dark:text-slate-400">
              判错后的练习会自动排队。这里默认只看今天该复习的题，想整理旧题再打开高级筛选。
            </p>
            <div className="mt-5 grid gap-3 sm:grid-cols-3">
              <FocusStat icon={Clock3} label="今天要看" value={stats.dueCount} tone="amber" />
              <FocusStat icon={Target} label="还不稳" value={stats.activeCount} tone="blue" />
              <FocusStat icon={CheckCircle2} label="已经掌握" value={stats.masteredCount} tone="emerald" />
            </div>
          </div>
          <button
            type="button"
            onClick={handleStartReview}
            disabled={reviewBusy || !hasDueMistakes}
            className="inline-flex min-h-14 items-center justify-center gap-2 rounded-2xl bg-primary-600 px-5 text-base font-semibold text-white shadow-lg shadow-primary-500/20 transition-all hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50 md:min-h-16"
          >
            {reviewBusy ? <LoaderCircle className="h-5 w-5 animate-spin" /> : <RotateCcw className="h-5 w-5" />}
            开始今日复习
          </button>
        </div>
      </section>

      <section className="rounded-[24px] bg-white/70 p-4 shadow-[0_14px_42px_rgba(37,99,235,0.08)] backdrop-blur dark:bg-slate-900/62 dark:shadow-slate-950/20 md:p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap gap-2">
            {STATUS_OPTIONS.map((item) => (
              <button
                key={item.value}
                type="button"
                onClick={() => handleStatusChange(item.value)}
                className={`rounded-full px-4 py-2 text-sm font-medium transition-colors ${
                  status === item.value
                    ? 'bg-primary-600 text-white shadow-sm shadow-primary-500/20'
                    : 'bg-white/72 text-slate-600 hover:bg-primary-50/80 hover:text-primary-700 dark:bg-slate-950/55 dark:text-slate-300 dark:hover:text-primary-300'
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => setShowAdvancedFilters((prev) => !prev)}
            className="inline-flex items-center justify-center gap-2 rounded-xl bg-white/72 px-3.5 py-2 text-sm font-medium text-slate-600 transition-colors hover:bg-primary-50/80 hover:text-primary-700 dark:bg-slate-950/55 dark:text-slate-300 dark:hover:text-primary-300"
          >
            <Filter className="h-4 w-4" />
            高级筛选
          </button>
        </div>

        {activeFilterLabels.length > 0 ? (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
            <span>当前范围</span>
            {activeFilterLabels.map((label) => (
              <span key={label} className="rounded-full bg-slate-100 px-2.5 py-1 text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                {label}
              </span>
            ))}
            <button type="button" onClick={handleClearAdvancedFilters} className="text-primary-600 hover:text-primary-700 dark:text-primary-300">
              清空
            </button>
          </div>
        ) : null}

        {showAdvancedFilters ? (
          <div className="mt-4 grid gap-3 pt-2 lg:grid-cols-[1fr_180px_auto]">
            <label className="flex items-center rounded-xl bg-slate-50/80 px-3.5 py-2.5 transition-all focus-within:bg-white focus-within:shadow-md focus-within:shadow-primary-100/30 dark:bg-slate-950/55 dark:focus-within:bg-slate-950/80 dark:focus-within:shadow-primary-950/24">
              <Search className="mr-2 h-4 w-4 text-slate-400" />
              <input
                value={tagInput}
                onChange={(event) => setTagInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    handleApplyFilters();
                  }
                }}
                placeholder="按知识点找题"
                className="w-full bg-transparent text-sm text-slate-700 outline-none placeholder:text-slate-400 dark:text-slate-200"
              />
            </label>
            <select
              value={difficulty}
              onChange={(event) => {
                setPage(0);
                setDifficulty(event.target.value);
                setMistakes([]);
                setData(null);
              }}
              className="rounded-xl bg-slate-50/80 px-3.5 py-2.5 text-sm outline-none transition-all focus:bg-white focus:shadow-md focus:shadow-primary-100/30 dark:bg-slate-950/55 dark:text-slate-200 dark:focus:bg-slate-950/80 dark:focus:shadow-primary-950/24"
            >
              {DIFFICULTY_OPTIONS.map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </select>
            <button
              type="button"
              onClick={handleApplyFilters}
              className="rounded-xl bg-primary-50 px-4 py-2.5 text-sm font-medium text-primary-700 transition-colors hover:bg-primary-100 dark:bg-primary-500/10 dark:text-primary-300"
            >
              应用
            </button>
          </div>
        ) : null}
      </section>

      {error ? <Notice tone="error" message={error} /> : null}
      {reviewMessage ? <Notice tone={reviewSession?.status === 'DONE' ? 'success' : 'warning'} message={reviewMessage} /> : null}
      {trainingError ? <Notice tone="error" message={trainingError} /> : null}

      {reviewSession ? (
        <ReviewPanel
          session={reviewSession}
          qualities={reviewQualities}
          busy={reviewBusy}
          onQualityChange={(id, quality) => setReviewQualities((prev) => ({ ...prev, [id]: quality }))}
          onSubmit={handleSubmitReview}
          onClose={() => {
            setReviewSession(null);
            setReviewQualities({});
          }}
        />
      ) : null}

      <TrainingCampPanel
        data={trainingCamps}
        loading={trainingLoading}
        selectedCampId={selectedCampId}
        generatingId={microPracticeGeneratingId}
        onSelectCamp={setSelectedCampId}
        onRetry={() => void loadTrainingCamps()}
        onStartMicroPractice={(camp, practice) => void handleStartMicroPractice(camp, practice)}
      />

      <div className="space-y-3">
        {loading && mistakes.length === 0 ? (
          <div className="flex items-center justify-center gap-2 rounded-[22px] bg-white/74 p-8 text-sm text-slate-500 shadow-[0_12px_34px_rgba(54,86,140,0.07)] backdrop-blur dark:bg-slate-900/64 dark:text-slate-400 dark:shadow-slate-950/20">
            <LoaderCircle className="h-4 w-4 animate-spin text-primary-500" />
            正在加载错题
          </div>
        ) : mistakes.length > 0 ? (
          mistakes.map((item) => (
            <MistakeCard
              key={item.id}
              item={item}
              noteDraft={noteDrafts[item.id] ?? ''}
              typeDraft={typeDrafts[item.id] ?? ''}
              saving={savingId === item.id}
              onNoteChange={(value) => setNoteDrafts((prev) => ({ ...prev, [item.id]: value }))}
              onTypeChange={(value) => setTypeDrafts((prev) => ({ ...prev, [item.id]: value }))}
              onSave={() => void handleSave(item)}
              onToggleMastered={() => void handleToggleMastered(item)}
            />
          ))
        ) : (
          <div className="rounded-[22px] bg-white/74 p-8 text-center shadow-[0_12px_34px_rgba(54,86,140,0.07)] backdrop-blur dark:bg-slate-900/64 dark:shadow-slate-950/20">
            <div className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-2xl bg-slate-100 text-slate-400 dark:bg-slate-800">
              <Sparkles className="h-5 w-5" />
            </div>
            <div className="text-sm font-semibold text-slate-700 dark:text-slate-300">暂无匹配错题</div>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">练习判错后会自动进入错题本。</p>
          </div>
        )}
      </div>

      {hasMore || (loading && mistakes.length > 0) ? (
        <div className="flex justify-center">
          <button
            type="button"
            disabled={loading || !hasMore}
            onClick={handleLoadMore}
            className="inline-flex min-w-36 items-center justify-center gap-2 rounded-xl bg-white/76 px-4 py-2.5 text-sm font-medium text-slate-600 shadow-sm shadow-slate-200/70 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-slate-900/70 dark:text-slate-300 dark:shadow-none"
          >
            {loading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : null}
            {loading ? '加载中' : '再看一些'}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function MistakeCard(props: {
  item: MistakeRecordResponse;
  noteDraft: string;
  typeDraft: string;
  saving: boolean;
  onNoteChange: (value: string) => void;
  onTypeChange: (value: string) => void;
  onSave: () => void;
  onToggleMastered: () => void;
}) {
  const { item } = props;
  const [detailsOpen, setDetailsOpen] = useState(false);
  const feedback = asText(item.judgeResult.feedback) || asText(item.judgeResult.reason);

  return (
    <article className="overflow-hidden rounded-[24px] bg-white/78 shadow-[0_14px_38px_rgba(54,86,140,0.08)] backdrop-blur transition-colors hover:bg-white/88 dark:bg-slate-900/68 dark:shadow-slate-950/20 dark:hover:bg-slate-900/78">
      <div className="px-4 py-3 md:px-5">
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium ${
                item.mastered
                  ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300'
                  : isDue(item.nextReviewAt)
                    ? 'bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300'
                    : 'bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300'
              }`}>
                {item.mastered ? <CheckCircle2 className="h-3.5 w-3.5" /> : <Clock3 className="h-3.5 w-3.5" />}
                {item.mastered ? '已掌握' : isDue(item.nextReviewAt) ? '今日复习' : '待复习'}
              </span>
              <span className="text-xs text-slate-400 dark:text-slate-500">错 {item.wrongCount} 次 · 复习 {item.reviewCount} 次 · {difficultyLabel(item.difficultyLevel)}</span>
            </div>
            <h2 className="mt-3 text-base font-semibold leading-7 text-slate-800 dark:text-slate-100">{item.stem}</h2>
          </div>
          <button
            type="button"
            onClick={props.onToggleMastered}
            disabled={props.saving}
            className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-slate-50/80 px-3 py-2 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-slate-950/45 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            {item.mastered ? <RotateCcw className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
            {item.mastered ? '移回复习' : '标记掌握'}
          </button>
        </div>
      </div>
      <div className="space-y-4 p-4 md:p-5">
        <OptionList itemId={item.id} options={item.options} />

        <div className="grid gap-3 md:grid-cols-2">
          <AnswerBlock label="你的答案" value={formatChoiceAnswer(item.learnerAnswer, item.options) || '未作答'} tone="danger" />
          <AnswerBlock label="参考答案" value={formatChoiceAnswer(formatAnswer(item.standardAnswer), item.options)} tone="success" />
        </div>

        {feedback ? (
          <div className="rounded-xl bg-primary-50/70 px-3.5 py-3 text-sm leading-6 text-primary-800 dark:bg-primary-500/10 dark:text-primary-200">
            {feedback}
          </div>
        ) : null}

        <div className="flex flex-col gap-3 pt-1 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap gap-2">
            {item.knowledgeTags.length > 0 ? item.knowledgeTags.slice(0, 3).map((tag) => (
              <span key={tag} className="rounded-full bg-slate-100/80 px-2.5 py-1 text-xs text-slate-500 dark:bg-slate-800/70 dark:text-slate-400">
                {tag}
              </span>
            )) : (
              <span className="rounded-full bg-slate-100/60 px-2.5 py-1 text-xs text-slate-400 dark:bg-slate-800/50">未标注知识点</span>
            )}
            <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-500 dark:bg-slate-800 dark:text-slate-400">
              下次 {formatDate(item.nextReviewAt)}
            </span>
          </div>
          <button
            type="button"
            onClick={() => setDetailsOpen((prev) => !prev)}
            className="inline-flex items-center justify-center rounded-xl bg-slate-50/80 px-3 py-2 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-100 dark:bg-slate-950/45 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            {detailsOpen ? '收起整理' : '整理错因'}
          </button>
        </div>

        {detailsOpen ? (
          <div className="grid gap-3 rounded-2xl bg-slate-50/76 p-3 dark:bg-slate-950/40 md:grid-cols-[220px_minmax(0,1fr)_auto] md:items-start">
            <select
              value={props.typeDraft}
              onChange={(event) => props.onTypeChange(event.target.value)}
              className="rounded-xl bg-white/88 px-3 py-2.5 text-sm outline-none transition-all focus:bg-white focus:shadow-md focus:shadow-primary-100/30 dark:bg-slate-900/70 dark:text-slate-200 dark:focus:bg-slate-900 dark:focus:shadow-primary-950/24"
            >
              {MISTAKE_TYPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
            <textarea
              value={props.noteDraft}
              onChange={(event) => props.onNoteChange(event.target.value)}
              rows={3}
              placeholder="这题错在哪？下次先检查什么？"
              className="w-full rounded-xl bg-white/88 px-3.5 py-3 text-sm leading-6 outline-none transition-all focus:bg-white focus:shadow-md focus:shadow-primary-100/30 dark:bg-slate-900/70 dark:text-slate-200 dark:focus:bg-slate-900 dark:focus:shadow-primary-950/24"
            />
            <button
              type="button"
              onClick={props.onSave}
              disabled={props.saving}
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-primary-600 px-4 text-sm font-medium text-white transition-colors hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {props.saving ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              保存
            </button>
          </div>
        ) : null}
      </div>
    </article>
  );
}

function ReviewPanel(props: {
  session: MistakeReviewSessionResponse;
  qualities: Record<string, number>;
  busy: boolean;
  onQualityChange: (id: string, quality: number) => void;
  onSubmit: () => void;
  onClose: () => void;
}) {
  const done = props.session.status === 'DONE';
  const [currentIndex, setCurrentIndex] = useState(0);
  const currentItem = props.session.items[currentIndex];
  const answeredCount = props.session.items.filter((item) => props.qualities[item.id] !== undefined).length;
  return (
    <section className="overflow-hidden rounded-[24px] bg-white/82 shadow-[0_16px_42px_rgba(37,99,235,0.10)] backdrop-blur dark:bg-slate-900/74 dark:shadow-slate-950/20">
      <div className="flex flex-col gap-3 px-4 py-3 md:flex-row md:items-center md:justify-between md:px-5">
        <div>
          <div className="text-sm font-semibold text-slate-800 dark:text-slate-100">{done ? '今日复习完成' : '今日复习中'}</div>
          <div className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
            {done ? '已记录本次掌握情况' : `${answeredCount} / ${props.session.items.length} 道已判断`}
          </div>
        </div>
        <button
          type="button"
          onClick={props.onClose}
          className="inline-flex items-center gap-2 rounded-xl bg-slate-50/80 px-3 py-2 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-100 dark:bg-slate-950/45 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          <XCircle className="h-3.5 w-3.5" />
          关闭
        </button>
      </div>
      <div className="p-4 md:p-5">
        {currentItem ? (
          <div className="rounded-2xl bg-slate-50/76 p-4 dark:bg-slate-950/40">
            <div className="mb-3 flex items-center justify-between gap-3 text-xs text-slate-500 dark:text-slate-400">
              <span>第 {currentIndex + 1} 题 / 共 {props.session.items.length} 题</span>
              <span>{props.qualities[currentItem.id] !== undefined ? '已选择' : '还没判断'}</span>
            </div>
            <div className="text-base font-semibold leading-7 text-slate-800 dark:text-slate-100">{currentItem.stem}</div>
            <OptionList itemId={currentItem.id} options={currentItem.options} className="mt-4" />
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              <AnswerBlock label="你的错答" value={formatChoiceAnswer(currentItem.learnerAnswer, currentItem.options) || '未作答'} tone="danger" />
              <AnswerBlock label="参考答案" value={formatChoiceAnswer(formatAnswer(currentItem.standardAnswer), currentItem.options)} tone="success" />
            </div>
            {!done ? (
              <div className="mt-5">
                <div className="mb-2 text-sm font-medium text-slate-700 dark:text-slate-300">这题你现在有多稳？</div>
                <div className="grid gap-2 sm:grid-cols-4">
                  {QUALITY_OPTIONS.map((quality) => {
                    const active = props.qualities[currentItem.id] === quality.value;
                    return (
                      <button
                        key={quality.value}
                        type="button"
                        onClick={() => props.onQualityChange(currentItem.id, quality.value)}
                        className={`min-h-11 rounded-xl px-3 py-2 text-sm font-medium transition-colors ${
                          active
                            ? 'bg-primary-600 text-white shadow-sm shadow-primary-500/20 dark:bg-primary-500 dark:text-white'
                            : 'bg-white/85 text-slate-500 hover:bg-primary-50/70 hover:text-primary-600 dark:bg-slate-900/70 dark:text-slate-400'
                        }`}
                      >
                        {quality.label}
                      </button>
                    );
                  })}
                </div>
              </div>
            ) : null}
          </div>
        ) : (
          <EmptyState title="暂无可复习题" description="今天没有排队的错题，可以回到列表整理旧题。" />
        )}
        {props.session.items.length > 1 ? (
          <div className="mt-4 flex items-center justify-between gap-3">
            <button
              type="button"
              disabled={currentIndex <= 0}
              onClick={() => setCurrentIndex((prev) => Math.max(0, prev - 1))}
              className="rounded-xl bg-white/76 px-4 py-2 text-sm font-medium text-slate-600 shadow-sm shadow-slate-200/70 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:bg-slate-900/60 dark:text-slate-300 dark:shadow-none dark:hover:bg-slate-800"
            >
              上一题
            </button>
            <button
              type="button"
              disabled={currentIndex + 1 >= props.session.items.length}
              onClick={() => setCurrentIndex((prev) => Math.min(props.session.items.length - 1, prev + 1))}
              className="rounded-xl bg-white/76 px-4 py-2 text-sm font-medium text-slate-600 shadow-sm shadow-slate-200/70 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:bg-slate-900/60 dark:text-slate-300 dark:shadow-none dark:hover:bg-slate-800"
            >
              下一题
            </button>
          </div>
        ) : null}
        {!done ? (
          <button
            type="button"
            onClick={props.onSubmit}
            disabled={props.busy}
            className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-primary-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {props.busy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
            保存本次复习
          </button>
        ) : null}
      </div>
    </section>
  );
}

function TrainingCampPanel(props: {
  data: MistakeTrainingCampResponse | null;
  loading: boolean;
  selectedCampId: string;
  generatingId: string;
  onSelectCamp: (campId: string) => void;
  onRetry: () => void;
  onStartMicroPractice: (camp: MistakeCampGroup, practice: TrainingMicroPractice) => void;
}) {
  const camps = props.data?.camps ?? [];
  const selectedCamp = camps.find((camp) => camp.campId === props.selectedCampId) ?? camps[0] ?? null;

  return (
    <section className="overflow-hidden rounded-[24px] bg-white/76 shadow-[0_16px_44px_rgba(37,99,235,0.09)] backdrop-blur dark:bg-slate-900/66 dark:shadow-slate-950/20">
      <div className="flex flex-col gap-3 px-4 py-4 md:flex-row md:items-center md:justify-between md:px-5">
        <div>
          <div className="inline-flex items-center gap-2 rounded-full bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-700 dark:bg-amber-500/10 dark:text-amber-200">
            <ClipboardList className="h-3.5 w-3.5" />
            错因训练营
          </div>
          <h2 className="mt-2 text-lg font-semibold text-slate-800 dark:text-slate-100">按错因和知识点集中修正</h2>
          <p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">
            训练营来自后端错题记录聚类，复习和标记掌握后会同步更新错因解释、复习计划和掌握度变化。
          </p>
        </div>
        <button
          type="button"
          onClick={props.onRetry}
          disabled={props.loading}
          className="inline-flex items-center justify-center gap-2 rounded-xl bg-white/72 px-3.5 py-2 text-sm font-medium text-slate-600 transition-colors hover:bg-primary-50/80 hover:text-primary-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-slate-950/55 dark:text-slate-300 dark:hover:text-primary-300"
        >
          {props.loading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
          刷新训练营
        </button>
      </div>

      {props.loading && camps.length === 0 ? (
        <div className="flex items-center justify-center gap-2 px-5 py-8 text-sm text-slate-500 dark:text-slate-400">
          <LoaderCircle className="h-4 w-4 animate-spin text-primary-500" />
          正在生成错因训练营
        </div>
      ) : camps.length === 0 ? (
        <div className="px-5 pb-5">
          <EmptyState
            title="暂无错因训练营"
            description="完成练习并产生错题后，系统会按错因和知识点自动聚类。"
            actions={
              <div className="mt-4 flex flex-col items-center justify-center gap-2 sm:flex-row">
                <Link
                  to="/profile"
                  className="inline-flex h-10 items-center justify-center rounded-xl bg-primary-600 px-4 text-sm font-semibold text-white shadow-sm shadow-primary-500/20 transition hover:bg-primary-700"
                >
                  去画像找薄弱点
                </Link>
                <Link
                  to="/resources"
                  className="inline-flex h-10 items-center justify-center rounded-xl bg-slate-100 px-4 text-sm font-semibold text-slate-600 transition hover:bg-primary-50 hover:text-primary-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-primary-500/10"
                >
                  打开资源库
                </Link>
              </div>
            }
          />
        </div>
      ) : (
        <div className="grid gap-4 px-4 pb-5 md:grid-cols-[300px_minmax(0,1fr)] md:px-5">
          <div className="space-y-2">
            <div className="grid grid-cols-2 gap-2 text-xs">
              <CampSummaryPill label="训练营" value={props.data?.summary.campCount ?? camps.length} />
              <CampSummaryPill label="待修正" value={props.data?.summary.activeMistakeCount ?? 0} />
              <CampSummaryPill label="今日到期" value={props.data?.summary.dueMistakeCount ?? 0} />
              <CampSummaryPill label="已掌握" value={props.data?.summary.masteredMistakeCount ?? 0} />
            </div>
            {camps.map((camp) => {
              const active = selectedCamp?.campId === camp.campId;
              return (
                <button
                  key={camp.campId}
                  type="button"
                  onClick={() => props.onSelectCamp(camp.campId)}
                  className={`w-full rounded-2xl px-3.5 py-3 text-left transition ${
                    active
                      ? 'bg-primary-600 text-white shadow-lg shadow-primary-500/16'
                      : 'bg-slate-50/78 text-slate-600 hover:bg-primary-50/72 hover:text-primary-700 dark:bg-slate-950/40 dark:text-slate-300 dark:hover:text-primary-300'
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold">{camp.title}</div>
                      <div className={`mt-1 text-xs ${active ? 'text-primary-50/90' : 'text-slate-400 dark:text-slate-500'}`}>
                        {camp.mistakeCount} 题 · 到期 {camp.dueCount} · {formatMasteryChange(camp.masteryChange)}
                      </div>
                    </div>
                    {camp.dueCount > 0 ? (
                      <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${active ? 'bg-white/18 text-white' : 'bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-200'}`}>
                        due
                      </span>
                    ) : null}
                  </div>
                </button>
              );
            })}
          </div>

          {selectedCamp ? (
            <div className="space-y-4 rounded-2xl bg-slate-50/70 p-4 dark:bg-slate-950/38">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div>
                  <h3 className="text-base font-semibold text-slate-800 dark:text-slate-100">{selectedCamp.title}</h3>
                  <p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">{selectedCamp.explanation}</p>
                </div>
                <div className="grid grid-cols-3 gap-2 text-center text-xs lg:w-[260px]">
                  <CampMetric label="错题" value={selectedCamp.mistakeCount} />
                  <CampMetric label="复习" value={selectedCamp.totalReviewCount} />
                  <CampMetric label="变化" value={formatMasteryChange(selectedCamp.masteryChange)} />
                </div>
              </div>

              <div className="grid gap-3 lg:grid-cols-2">
                <div className="rounded-xl bg-white/78 p-3 dark:bg-slate-900/62">
                  <div className="mb-2 text-sm font-semibold text-slate-700 dark:text-slate-200">代表题</div>
                  <div className="space-y-2">
                    {selectedCamp.representativeMistakes.length > 0 ? selectedCamp.representativeMistakes.map((item) => (
                      <div key={item.id} className="rounded-lg bg-slate-50/86 px-3 py-2 dark:bg-slate-950/45">
                        <div className="line-clamp-2 text-sm leading-6 text-slate-700 dark:text-slate-200">{item.stem}</div>
                        <div className="mt-1 text-xs text-slate-400 dark:text-slate-500">
                          错 {item.wrongCount} 次 · 复习 {item.reviewCount} 次 · 下次 {formatDate(item.nextReviewAt)}
                        </div>
                      </div>
                    )) : (
                      <div className="rounded-lg bg-slate-50/86 px-3 py-2 text-sm text-slate-500 dark:bg-slate-950/45 dark:text-slate-400">
                        暂无代表题，先完成一次练习或补充错因分类。
                      </div>
                    )}
                  </div>
                </div>

                <div className="rounded-xl bg-white/78 p-3 dark:bg-slate-900/62">
                  <div className="mb-2 text-sm font-semibold text-slate-700 dark:text-slate-200">复习计划</div>
                  <div className="space-y-2 text-sm leading-6 text-slate-600 dark:text-slate-300">
                    <div className="rounded-lg bg-slate-50/86 px-3 py-2 dark:bg-slate-950/45">
                      下一次复习：{formatDate(selectedCamp.nextReviewAt || undefined)}
                    </div>
                    <div className="rounded-lg bg-slate-50/86 px-3 py-2 dark:bg-slate-950/45">
                      优先顺序：{selectedCamp.dueCount > 0 ? '先处理今日到期错题，再做迁移题。' : '先做错因定位，再安排下一次间隔复习。'}
                    </div>
                    <div className="rounded-lg bg-slate-50/86 px-3 py-2 dark:bg-slate-950/45">
                      复测标准：微练习连续答对后，再把代表题标记为掌握。
                    </div>
                  </div>
                </div>
              </div>

              <div className="rounded-xl bg-white/78 p-3 dark:bg-slate-900/62">
                <div className="mb-2 flex items-center justify-between gap-3">
                  <div className="text-sm font-semibold text-slate-700 dark:text-slate-200">针对性微练习</div>
                  <span className="text-xs text-slate-400 dark:text-slate-500">生成后在浮动练习助手中作答</span>
                </div>
                <div className="grid gap-2 md:grid-cols-2">
                  {selectedCamp.microPractices.map((practice) => {
                    const generating = props.generatingId === `${selectedCamp.campId}:${practice.id}`;
                    return (
                      <button
                        key={practice.id}
                        type="button"
                        onClick={() => props.onStartMicroPractice(selectedCamp, practice)}
                        disabled={Boolean(props.generatingId)}
                        className="flex min-h-24 items-start gap-3 rounded-xl bg-slate-50/86 px-3 py-3 text-left transition hover:bg-primary-50/76 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-slate-950/45 dark:hover:bg-primary-500/10"
                      >
                        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-primary-50 text-primary-600 dark:bg-primary-500/10 dark:text-primary-200">
                          {generating ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                        </span>
                        <span className="min-w-0">
                          <span className="block text-sm font-semibold text-slate-800 dark:text-slate-100">{practice.title}</span>
                          <span className="mt-1 block text-xs leading-5 text-slate-500 dark:text-slate-400">{practice.description}</span>
                          <span className="mt-2 inline-flex rounded-full bg-white/80 px-2 py-0.5 text-xs text-slate-500 dark:bg-slate-900/70 dark:text-slate-400">
                            {difficultyLabel(practice.difficulty)}
                          </span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

function OptionList(props: { itemId: string; options: string[]; className?: string }) {
  if (props.options.length === 0) {
    return null;
  }
  return (
    <div className={`space-y-2 ${props.className ?? ''}`.trim()}>
      {props.options.map((option, index) => (
        <div key={`${props.itemId}-${index}`} className="rounded-xl bg-slate-50/72 px-3 py-2 text-sm text-slate-600 dark:bg-slate-900/55 dark:text-slate-300">
          {optionLabel(index)}. {option}
        </div>
      ))}
    </div>
  );
}

function FocusStat(props: { icon: typeof Clock3; label: string; value: number; tone: 'amber' | 'blue' | 'emerald' }) {
  const Icon = props.icon;
  const toneClass = {
    amber: 'bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300',
    blue: 'bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300',
    emerald: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300',
  }[props.tone];
  return (
    <div className="flex items-center gap-3 rounded-2xl bg-slate-50/70 px-4 py-3 dark:bg-slate-950/40">
      <div className={`flex h-10 w-10 items-center justify-center rounded-2xl ${toneClass}`}>
        <Icon className="h-5 w-5" />
      </div>
      <div>
        <div className="text-2xl font-semibold text-slate-800 dark:text-white">{props.value}</div>
        <div className="text-xs text-slate-500 dark:text-slate-400">{props.label}</div>
      </div>
    </div>
  );
}

function CampSummaryPill(props: { label: string; value: number }) {
  return (
    <div className="rounded-xl bg-slate-50/78 px-3 py-2 dark:bg-slate-950/40">
      <div className="text-base font-semibold text-slate-800 dark:text-white">{props.value}</div>
      <div className="text-xs text-slate-500 dark:text-slate-400">{props.label}</div>
    </div>
  );
}

function CampMetric(props: { label: string; value: number | string }) {
  return (
    <div className="rounded-xl bg-white/76 px-2.5 py-2 dark:bg-slate-900/62">
      <div className="text-sm font-semibold text-slate-800 dark:text-white">{props.value}</div>
      <div className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{props.label}</div>
    </div>
  );
}

function EmptyState(props: { title: string; description: string; actions?: ReactNode }) {
  return (
    <div className="rounded-[22px] bg-white/74 p-8 text-center shadow-[0_12px_34px_rgba(54,86,140,0.07)] backdrop-blur dark:bg-slate-900/64 dark:shadow-slate-950/20">
      <div className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-2xl bg-slate-100 text-slate-400 dark:bg-slate-800">
        <Sparkles className="h-5 w-5" />
      </div>
      <div className="text-sm font-semibold text-slate-700 dark:text-slate-300">{props.title}</div>
      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{props.description}</p>
      {props.actions}
    </div>
  );
}

function AnswerBlock(props: { label: string; value: string; tone: 'danger' | 'success' }) {
  const toneClass = props.tone === 'danger'
    ? 'bg-rose-50/72 text-rose-800 dark:bg-rose-500/10 dark:text-rose-200'
    : 'bg-emerald-50/72 text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-200';
  return (
    <div className={`rounded-xl px-3.5 py-3 ${toneClass}`}>
      <div className="mb-1 text-xs font-semibold opacity-75">{props.label}</div>
      <div className="whitespace-pre-wrap text-sm leading-6">{props.value}</div>
    </div>
  );
}

function Notice(props: { tone: 'error' | 'warning' | 'success'; message: string }) {
  const toneClass = props.tone === 'error'
    ? 'bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-200'
    : props.tone === 'success'
      ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-200'
      : 'bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-200';
  return (
    <div className={`flex items-start gap-2 rounded-xl px-4 py-3 text-sm shadow-sm shadow-slate-200/50 dark:shadow-none ${toneClass}`}>
      <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
      <span>{props.message}</span>
    </div>
  );
}

function formatAnswer(value: Record<string, unknown>): string {
  const answer = value.answer ?? value.correctAnswer ?? value;
  if (typeof answer === 'string') {
    return answer;
  }
  return JSON.stringify(answer, null, 2);
}

function formatChoiceAnswer(value: string, options: string[]): string {
  const answer = value.trim();
  if (!answer || options.length === 0) {
    return answer;
  }
  const labels = answer
    .toUpperCase()
    .replace(/[，、；;|/]/g, ',')
    .split(/[\s,]+/)
    .flatMap((part) => (/^[A-Z]+$/.test(part) && part.length > 1 ? [...part] : [part]))
    .filter(Boolean);
  if (labels.length === 0) {
    return answer;
  }
  const displayValues = labels.map((label) => {
    const index = label.charCodeAt(0) - 65;
    return /^[A-Z]$/.test(label) && options[index] ? `${label}. ${options[index]}` : '';
  });
  return displayValues.every(Boolean) ? displayValues.join('\n') : answer;
}

function optionLabel(index: number): string {
  return String.fromCharCode(65 + index);
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function difficultyLabel(value: string): string {
  return DIFFICULTY_OPTIONS.find((item) => item.value === value)?.label ?? value;
}

function formatMasteryChange(value: number): string {
  if (!Number.isFinite(value)) {
    return '0%';
  }
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}

function statusLabel(value: MistakeStatus): string {
  return STATUS_OPTIONS.find((item) => item.value === value)?.label ?? value;
}

function formatDate(value?: string): string {
  if (!value) {
    return '--';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return '--';
  }
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function isDue(value?: string): boolean {
  if (!value) {
    return false;
  }
  return new Date(value).getTime() <= Date.now();
}
