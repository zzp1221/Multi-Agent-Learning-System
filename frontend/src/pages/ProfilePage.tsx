import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useOutletContext } from 'react-router-dom';
import {
  BookOpen,
  Brain,
  CalendarClock,
  Clock3,
  LineChart,
  LoaderCircle,
  Lock,
  Network,
  Target,
  TriangleAlert,
  UserRoundSearch,
} from 'lucide-react';
import RadarChart from '../components/RadarChart';
import { getErrorMessage } from '../api/request';
import {
  smartEngineApi,
  type KnowledgeGraphResponse,
  type ProfileBehaviorTrendPoint,
  type ProfileResourcePreference,
  type UserProfileAnalyticsResponse,
} from '../api/smartEngine';
import type { LayoutOutletContext } from '../components/Layout';
import {
  EMPTY_VALUE,
  type ProfileLearningHabits,
  type ProfileSnapshot,
  type WeakPointRank,
} from './LearningStudioDemoPage.types';
import { mapProfileResponse } from './LearningStudioDemoPage.utils';

const navItems = [
  { id: 'overview', label: '当前阶段' },
  { id: 'key-weak', label: '关键薄弱点' },
  { id: 'next-actions', label: '下一步行动' },
  { id: 'knowledge-graph', label: '知识图谱' },
  { id: 'more-details', label: '更多分析' },
];

const defaultResourcePreferences: ProfileResourcePreference[] = [
  { type: 'EXPLANATION', label: '讲解文档', identified: false, profileMentioned: false, requestCount: 0, generatedCount: 0, downloadCount: 0, lastUsedAt: null, evidenceLabel: '暂无真实证据' },
  { type: 'READING', label: '拓展阅读', identified: false, profileMentioned: false, requestCount: 0, generatedCount: 0, downloadCount: 0, lastUsedAt: null, evidenceLabel: '暂无真实证据' },
  { type: 'CODE_CASE', label: '代码案例', identified: false, profileMentioned: false, requestCount: 0, generatedCount: 0, downloadCount: 0, lastUsedAt: null, evidenceLabel: '暂无真实证据' },
  { type: 'MINDMAP', label: '思维导图', identified: false, profileMentioned: false, requestCount: 0, generatedCount: 0, downloadCount: 0, lastUsedAt: null, evidenceLabel: '暂无真实证据' },
  { type: 'QUIZ', label: '练习题', identified: false, profileMentioned: false, requestCount: 0, generatedCount: 0, downloadCount: 0, lastUsedAt: null, evidenceLabel: '暂无真实证据' },
  { type: 'VIDEO', label: '数字人视频', identified: false, profileMentioned: false, requestCount: 0, generatedCount: 0, downloadCount: 0, lastUsedAt: null, evidenceLabel: '暂无真实证据' },
];

export default function ProfilePage() {
  const { isAuthenticated, currentUser, openAuthModal } = useOutletContext<LayoutOutletContext>();
  const [profile, setProfile] = useState<ProfileSnapshot | null>(null);
  const [updatedAt, setUpdatedAt] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [analytics, setAnalytics] = useState<UserProfileAnalyticsResponse | null>(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [analyticsError, setAnalyticsError] = useState('');
  const [showAllWeakPoints, setShowAllWeakPoints] = useState(false);
  const [knowledgeGraph, setKnowledgeGraph] = useState<KnowledgeGraphResponse | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [graphError, setGraphError] = useState('');

  const loadProfile = useCallback(async () => {
    if (!isAuthenticated || !currentUser) {
      setProfile(null);
      setUpdatedAt('');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const response = await smartEngineApi.getCurrentProfile(String(currentUser.id));
      const hasProfilePayload = Boolean(response.profile && Object.keys(response.profile).length > 0);
      setProfile(hasProfilePayload ? mapProfileResponse(response) : null);
      setUpdatedAt(response.updatedAt ?? '');
    } catch (loadError) {
      setProfile(null);
      setUpdatedAt('');
      setError(getErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [currentUser, isAuthenticated]);

  const loadAnalytics = useCallback(async () => {
    if (!isAuthenticated || !currentUser) {
      setAnalytics(null);
      setAnalyticsError('');
      return;
    }
    setAnalyticsLoading(true);
    setAnalyticsError('');
    try {
      const response = await smartEngineApi.getProfileAnalytics(String(currentUser.id), 30);
      setAnalytics(response);
    } catch (loadError) {
      setAnalytics(null);
      setAnalyticsError(getErrorMessage(loadError));
    } finally {
      setAnalyticsLoading(false);
    }
  }, [currentUser, isAuthenticated]);

  const loadKnowledgeGraph = useCallback(async () => {
    if (!isAuthenticated || !currentUser) {
      setKnowledgeGraph(null);
      setGraphError('');
      return;
    }
    setGraphLoading(true);
    setGraphError('');
    try {
      const response = await smartEngineApi.getKnowledgeGraph(String(currentUser.id));
      setKnowledgeGraph(response);
    } catch (loadError) {
      setKnowledgeGraph(null);
      setGraphError(getErrorMessage(loadError));
    } finally {
      setGraphLoading(false);
    }
  }, [currentUser, isAuthenticated]);

  useEffect(() => {
    void loadProfile();
    void loadAnalytics();
    void loadKnowledgeGraph();
  }, [loadAnalytics, loadKnowledgeGraph, loadProfile]);

  const displayName = currentUser?.fullName || currentUser?.loginId || currentUser?.username || '同学';

  const metrics = useMemo(() => {
    if (!profile) {
      return null;
    }
    const masteryAverage = profile.skillMastery.length > 0
      ? Math.round(profile.skillMastery.reduce((sum, item) => sum + item.score, 0) / profile.skillMastery.length)
      : null;
    const weakPointCount = profile.weakPointRanks.length || profile.weakPoints.length;
    const behaviorSignals = countBehaviorSignals(profile.learningHabits);
    const goalCount = [
      profile.currentGoal.shortTerm || profile.goal,
      profile.currentGoal.midTerm,
      profile.currentGoal.context,
    ].filter(Boolean).length;
    const preferenceCount = profile.preference.length + (profile.explanationPreference ? 1 : 0);
    return {
      masteryAverage,
      weakPointCount,
      behaviorSignals,
      goalCount,
      preferenceCount,
    };
  }, [profile]);

  if (!isAuthenticated) {
    return (
      <ProfileShell>
        <ProfileAccessState
          icon={<Lock className="h-6 w-6" />}
          title="登录后查看个人画像"
          description="个人画像只读取你的真实学习记录和画像快照。"
          actionLabel="去登录"
          onAction={() => openAuthModal('login', '登录后查看个人画像')}
        />
      </ProfileShell>
    );
  }

  if (loading && !profile) {
    return (
      <ProfileShell>
        <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-blue-100 bg-white/80 text-sm text-slate-500 shadow-sm shadow-blue-100/60 dark:border-slate-800 dark:bg-slate-900/70 dark:text-slate-400">
          <LoaderCircle className="mr-2 h-4 w-4 animate-spin text-primary-500" />
          正在读取真实画像数据
        </div>
      </ProfileShell>
    );
  }

  if (error) {
    return (
      <ProfileShell>
        <ProfileAccessState
          icon={<TriangleAlert className="h-6 w-6" />}
          title="画像读取失败"
          description={error}
          actionLabel="重新加载"
          onAction={() => void loadProfile()}
        />
      </ProfileShell>
    );
  }

  if (!profile || !metrics) {
    return (
      <ProfileShell>
        <ProfileAccessState
          icon={<UserRoundSearch className="h-6 w-6" />}
          title="暂无个人画像"
          description="完成对话、练习或学习服务后，系统会基于真实记录生成画像。"
          actionLabel="刷新画像"
          onAction={() => void loadProfile()}
        />
      </ProfileShell>
    );
  }

  const weakPointItems = buildWeakPointItems(profile);
  const keyWeakPoints = weakPointItems.slice(0, 3);
  const visibleWeakPoints = showAllWeakPoints ? weakPointItems : weakPointItems.slice(0, 5);
  const recommendations = profile.inferredRecommendations.slice(0, 3);
  const resourcePreferenceCards = buildResourcePreferenceCards(analytics, analyticsLoading, analyticsError);
  const explanationPreference = analytics?.preferenceAnalytics?.explanationPreference;
  const explanationValue = formatProfileDisplayValue(explanationPreference?.value || profile.explanationPreference);
  const explanationIdentified = Boolean(explanationPreference?.identified || profile.explanationPreference);
  const explanationDetail = analyticsLoading
    ? '正在读取讲解方式证据'
    : analyticsError
      ? '讲解方式证据读取失败'
      : explanationPreference?.identified
        ? `来源：${formatProfileSourceLabel(explanationPreference.source)}`
        : profile.explanationPreference
          ? `来源：${formatProfileSourceLabel('profile_json.explanationPreference')}`
          : '暂无真实证据';

  return (
    <ProfileShell>
      <div className="grid gap-5 xl:grid-cols-[176px_minmax(0,1fr)]">
        <ProfileSubnav updatedAt={updatedAt} />

        <div className="min-w-0 space-y-5">
          <header className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
            <div>
              <h1 className="text-2xl font-semibold text-slate-900 dark:text-white md:text-3xl">
                你好，{displayName}
              </h1>
              <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
                这是基于真实学习记录生成的个人画像，用来辅助你了解当前状态。
              </p>
            </div>
            <button
              type="button"
              onClick={() => {
                void loadProfile();
                void loadAnalytics();
                void loadKnowledgeGraph();
              }}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-blue-100 bg-white px-4 text-sm font-medium text-primary-600 shadow-sm shadow-blue-100/60 transition-colors hover:bg-primary-50 dark:border-slate-700 dark:bg-slate-900 dark:text-primary-300 dark:hover:bg-slate-800"
            >
              <CalendarClock className="h-4 w-4" />
              刷新画像
            </button>
          </header>

          <section id="overview" className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(280px,0.7fr)]">
            <article className="rounded-2xl border border-blue-100/80 bg-white/85 p-5 shadow-sm shadow-blue-100/60 dark:border-slate-800 dark:bg-slate-900/80">
              <div className="flex flex-col gap-5 md:flex-row md:items-start md:justify-between">
                <div className="min-w-0">
                  <div className="mb-3 inline-flex rounded-xl bg-primary-50 p-2 text-primary-600 dark:bg-primary-500/10 dark:text-primary-300">
                    <Target className="h-5 w-5" />
                  </div>
                  <div className="text-sm font-medium text-slate-500 dark:text-slate-400">当前阶段</div>
                  <h2 className="mt-2 text-2xl font-semibold text-slate-900 dark:text-white">{formatProfileDisplayValue(profile.knowledgeBase)}</h2>
                  <p className="mt-3 text-sm leading-6 text-slate-500 dark:text-slate-400">
                    {formatProfileDisplayValue(profile.goal || profile.currentGoal.shortTerm) || '暂无明确学习目标，建议先完成一次学习评估。'}
                  </p>
                </div>
                <div className="grid min-w-[220px] gap-3 sm:grid-cols-2 md:grid-cols-1">
                  <StageMiniStat label="知识掌握" value={metrics.masteryAverage === null ? EMPTY_VALUE : `${metrics.masteryAverage}%`} detail={profile.skillMastery.length > 0 ? `${profile.skillMastery.length} 个知识点` : '暂无真实数据'} />
                  <StageMiniStat label="学习节奏" value={formatProfileDisplayValue(profile.learningPace || profile.learningHabits.studyFrequency)} detail={analyticsTrendSummary(analytics) || '等待更多记录'} />
                </div>
              </div>
            </article>

            <article className="rounded-2xl border border-blue-100/80 bg-white/85 p-5 shadow-sm shadow-blue-100/60 dark:border-slate-800 dark:bg-slate-900/80">
              <SectionTitle title="画像摘要" subtitle="先看会影响下一步学习的关键信号" />
              <div className="mt-4 grid gap-3 sm:grid-cols-3 lg:grid-cols-1">
                <StageMiniStat label="薄弱点" value={`${metrics.weakPointCount} 个`} detail={metrics.weakPointCount > 0 ? '优先处理 Top3' : '暂无明确薄弱点'} />
                <StageMiniStat label="画像可靠度" value={`${profile.confidenceScore}%`} detail={`${profile.history.length} 次画像快照`} />
                <StageMiniStat label="讲解偏好" value={explanationValue} detail={explanationIdentified ? '已识别偏好' : '暂无真实证据'} />
              </div>
            </article>
          </section>

          <div className="space-y-5">
            <section id="key-weak" className="rounded-2xl border border-blue-100/80 bg-white/85 p-5 shadow-sm shadow-blue-100/60 dark:border-slate-800 dark:bg-slate-900/80">
              <SectionTitle title="关键薄弱点 Top3" subtitle="先处理最影响学习推进的内容" />
              <div className="mt-4 grid gap-3 lg:grid-cols-3">
                {keyWeakPoints.length > 0 ? keyWeakPoints.map((item, index) => (
                  <WeakPointCard key={`${item.topic}-${index}`} item={item} rank={index + 1} compact />
                )) : (
                  <EmptyInline text="暂无明确薄弱点，继续完成练习或学习评估后会更新。" />
                )}
              </div>
            </section>

            <section id="next-actions" className="rounded-2xl border border-blue-100/80 bg-white/85 p-5 shadow-sm shadow-blue-100/60 dark:border-slate-800 dark:bg-slate-900/80">
              <SectionTitle title="下一步行动 Top3" subtitle="把学习建议整理成可以立刻执行的小任务" />
              {recommendations.length > 0 ? (
                <div className="mt-4 grid gap-3 md:grid-cols-3">
                  {recommendations.map((item, index) => (
                    <RecommendationCard key={`${item}-${index}`} index={index + 1} text={item} />
                  ))}
                </div>              ) : (
                <EmptyInline text="当前画像暂无学习建议。" />
              )}
            </section>

            <section id="knowledge-graph" className="rounded-2xl border border-blue-100/80 bg-white/85 p-5 shadow-sm shadow-blue-100/60 dark:border-slate-800 dark:bg-slate-900/80">
              <div className="flex items-start justify-between gap-4">
                <SectionTitle title="知识掌握图谱" subtitle="按掌握状态整理当前知识点，优先展示薄弱项和下一步建议" />
                <button
                  type="button"
                  onClick={() => void loadKnowledgeGraph()}
                  className="shrink-0 rounded-xl border border-blue-100 px-3 py-1.5 text-sm font-medium text-primary-600 transition-colors hover:bg-primary-50 dark:border-slate-700 dark:text-primary-300 dark:hover:bg-slate-800"
                >
                  刷新
                </button>
              </div>
              <KnowledgeGraphPanel
                graph={knowledgeGraph}
                loading={graphLoading}
                error={graphError}
              />
            </section>

            <details id="more-details" className="group rounded-2xl border border-blue-100/80 bg-white/85 p-5 shadow-sm shadow-blue-100/60 dark:border-slate-800 dark:bg-slate-900/80">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-4">
                <SectionTitle title="展开更多分析" subtitle="雷达图、来源说明、完整薄弱点和行为趋势默认收起" />
                <span className="shrink-0 rounded-xl border border-blue-100 px-3 py-1.5 text-sm font-medium text-primary-600 transition-colors group-open:bg-primary-50 dark:border-slate-700 dark:text-primary-300 dark:group-open:bg-slate-800">
                  <span className="group-open:hidden">展开</span>
                  <span className="hidden group-open:inline">收起</span>
                </span>
              </summary>

              <div className="mt-5 space-y-5">
                <section id="goals" className="rounded-2xl border border-blue-100/80 bg-slate-50/60 p-4 dark:border-slate-800 dark:bg-slate-950/30">
                  <SectionTitle title="学习维度总览" subtitle="所有数量均来自当前画像字段" />
                  <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                    <DimensionCard icon={<Target className="h-5 w-5" />} title="学习目标" value={`${metrics.goalCount}项`} detail={formatProfileDisplayValue(profile.currentGoal.shortTerm || profile.goal) || '暂无短期目标'} href="#goals-detail" />
                    <DimensionCard icon={<Brain className="h-5 w-5" />} title="知识基础" value={`${profile.skillMastery.length}项维度`} detail={formatProfileDisplayValue(profile.knowledgeBase) || '待分析'} href="#knowledge" />
                    <DimensionCard
                      icon={<LineChart className="h-5 w-5" />}
                      title="学习行为"
                      value={analyticsLoading ? '读取中' : analytics ? `${analytics.systemAnalysis.coverage.activeDays}天记录` : `${metrics.behaviorSignals}项信号`}
                      detail={analytics ? `近 ${analytics.days} 天真实行为聚合` : metrics.behaviorSignals > 0 ? '来自学习习惯画像' : '暂无真实行为记录'}
                      href="#behavior"
                    />
                    <DimensionCard icon={<BookOpen className="h-5 w-5" />} title="讲解偏好" value={`${metrics.preferenceCount}项偏好`} detail={formatProfileDisplayValue(profile.explanationPreference || profile.preference.join('、')) || '暂无偏好'} href="#preference" />
                  </div>
                </section>

                <section id="knowledge" className="rounded-2xl border border-blue-100/80 bg-slate-50/60 p-4 dark:border-slate-800 dark:bg-slate-950/30">
                  <SectionTitle title="画像维度可视化" subtitle="分数由前端根据真实画像字段推断，仅供参考" />
                  <div className="mt-4 grid gap-5 lg:grid-cols-[minmax(0,0.95fr)_minmax(280px,0.75fr)]">
                    <RadarChart
                      data={profile.dimensionScores.map((item) => ({
                        subject: item.subject,
                        score: item.score,
                        fullMark: item.fullMark,
                        description: item.description,
                      }))}
                      height={320}
                      className="min-h-[320px]"
                    />
                    <div className="space-y-3">
                      {profile.dimensionScores.map((item) => (
                        <ScoreLine key={item.key} label={item.subject} detail={item.hint} score={item.score} />
                      ))}
                    </div>
                  </div>
                </section>

                <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                  <InfoCard title="偏好学习方式" value={formatProfileDisplayValue(profile.preference.join('、'))} detail="来自学习资源偏好" />
                  <InfoCard title="认知风格" value={formatProfileDisplayValue(profile.cognitiveStyle)} detail="来自学习风格画像" />
                  <InfoCard title="当前薄弱点" value={`${metrics.weakPointCount}个知识点`} detail="来自薄弱点识别结果" />
                  <InfoCard
                    title="擅长领域"
                    value={analytics?.systemAnalysis.strongestSkill || EMPTY_VALUE}
                    detail={analyticsLoading
                      ? '正在读取系统分析'
                      : analyticsError
                        ? '分析接口读取失败'
                        : analytics?.systemAnalysis.strongestSkillScore
                          ? `来自系统分析，掌握度 ${analytics.systemAnalysis.strongestSkillScore}%`
                          : '真实证据不足，暂不判断'}
                    muted={!analytics?.systemAnalysis.strongestSkill}
                  />
                </section>

                <section className="rounded-2xl border border-blue-100/80 bg-slate-50/60 p-4 dark:border-slate-800 dark:bg-slate-950/30">
                  <SectionTitle title="完整薄弱点列表" subtitle="默认展示前 5 个，避免一次看太多" />
                  <div className="mt-4 grid gap-3 lg:grid-cols-2">
                    {visibleWeakPoints.length > 0 ? visibleWeakPoints.map((item, index) => (
                      <WeakPointCard key={`${item.topic}-${index}`} item={item} rank={index + 1} />
                    )) : (
                      <EmptyInline text="暂无薄弱点排序。" />
                    )}
                  </div>
                  {weakPointItems.length > 5 ? (
                    <button
                      type="button"
                      onClick={() => setShowAllWeakPoints((prev) => !prev)}
                      className="mt-4 w-full rounded-xl border border-blue-100 px-3 py-2 text-sm font-medium text-primary-600 transition-colors hover:bg-primary-50 dark:border-slate-700 dark:text-primary-300 dark:hover:bg-slate-800"
                    >
                      {showAllWeakPoints ? '收起薄弱点' : `查看全部薄弱点 (${weakPointItems.length})`}
                    </button>
                  ) : null}
                </section>

                <section id="preference" className="rounded-2xl border border-blue-100/80 bg-slate-50/60 p-4 dark:border-slate-800 dark:bg-slate-950/30">
                  <SectionTitle title="讲解偏好详情" subtitle="展示真实证据次数，不展示偏好百分比" />
                  <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                    {resourcePreferenceCards.map((item) => (
                      <PreferenceCard
                        key={item.type}
                        title={item.label}
                        detail={item.evidenceLabel}
                        meta={formatPreferenceEvidenceMeta(item)}
                        muted={!item.identified || Boolean(analyticsError)}
                        status={preferenceStatusLabel(item, analyticsLoading, analyticsError)}
                      />
                    ))}
                    <PreferenceCard
                      title="讲解方式"
                      value={explanationValue}
                      detail={explanationDetail}
                      muted={!explanationIdentified || Boolean(analyticsError)}
                      status={analyticsLoading ? '读取中' : analyticsError ? '读取失败' : explanationIdentified ? '已识别' : '暂无证据'}
                    />
                  </div>
                </section>

                <section className="grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(300px,0.8fr)]">
                  <BehaviorTrendPanel
                    analytics={analytics}
                    loading={analyticsLoading}
                    error={analyticsError}
                    onRetry={() => void loadAnalytics()}
                  />
                  <SystemAnalysisPanel
                    analytics={analytics}
                    loading={analyticsLoading}
                    error={analyticsError}
                    onRetry={() => void loadAnalytics()}
                  />
                </section>

                <section id="goals-detail" className="rounded-2xl border border-blue-100/80 bg-slate-50/60 p-4 dark:border-slate-800 dark:bg-slate-950/30">
                  <SectionTitle title="目标与节奏" subtitle="来自学习目标与学习习惯画像" />
                  <div className="mt-4 grid gap-3 text-sm md:grid-cols-2 xl:grid-cols-5">
                    <FactRow label="短期目标" value={formatProfileDisplayValue(profile.currentGoal.shortTerm || profile.goal)} />
                    <FactRow label="中期目标" value={formatProfileDisplayValue(profile.currentGoal.midTerm)} />
                    <FactRow label="学习频率" value={formatProfileDisplayValue(profile.learningHabits.studyFrequency)} />
                    <FactRow label="平均时长" value={profile.learningHabits.avgSessionDuration > 0 ? `${profile.learningHabits.avgSessionDuration} 分钟` : EMPTY_VALUE} />
                    <FactRow label="画像历史" value={`${profile.history.length} 次快照`} />
                  </div>
                </section>
              </div>
            </details>
          </div>
          <footer className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1 pb-2 text-xs text-slate-400 dark:text-slate-500">
            <span>数据基于你的学习行为分析，仅供参考</span>
            <span>更新时间：{updatedAt ? new Date(updatedAt).toLocaleString('zh-CN') : EMPTY_VALUE}</span>
            <span>画像可靠度：{profile.confidenceScore}%</span>
          </footer>
        </div>
      </div>
    </ProfileShell>
  );
}

function ProfileShell({ children }: { children: ReactNode }) {
  return (
    <div className="mx-auto max-w-[1440px] px-1 pb-10">
      {children}
    </div>
  );
}

function ProfileSubnav({ updatedAt }: { updatedAt: string }) {
  return (
    <aside className="hidden xl:block">
      <div className="sticky top-24 rounded-2xl border border-blue-100/80 bg-white/85 p-3 shadow-sm shadow-blue-100/60 dark:border-slate-800 dark:bg-slate-900/80">
        <div className="mb-3 flex items-center gap-2 px-2 py-2 text-base font-semibold text-slate-900 dark:text-white">
          <UserRoundSearch className="h-5 w-5 text-primary-500" />
          学习画像
        </div>
        <nav className="space-y-1">
          {navItems.map((item) => (
            <a
              key={item.id}
              href={`#${item.id}`}
              className="flex items-center rounded-xl px-3 py-2 text-sm font-medium text-slate-600 transition-colors hover:bg-primary-50 hover:text-primary-700 dark:text-slate-400 dark:hover:bg-primary-500/10 dark:hover:text-primary-300"
            >
              {item.label}
            </a>
          ))}
        </nav>
        <div className="mt-6 rounded-xl bg-slate-50 px-3 py-2 text-xs text-slate-500 dark:bg-slate-800/70 dark:text-slate-400">
          <div className="mb-1 flex items-center gap-1.5">
            <Clock3 className="h-3.5 w-3.5" />
            更新时间
          </div>
          {updatedAt ? new Date(updatedAt).toLocaleString('zh-CN') : EMPTY_VALUE}
        </div>
      </div>
    </aside>
  );
}

function SectionTitle({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div>
      <h2 className="text-base font-semibold text-slate-900 dark:text-white">{title}</h2>
      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{subtitle}</p>
    </div>
  );
}

function DimensionCard(props: {
  icon: ReactNode;
  title: string;
  value: string;
  detail: string;
  href?: string;
}) {
  return (
    <article className="rounded-2xl border border-blue-100 bg-white px-4 py-4 shadow-sm shadow-blue-100/50 dark:border-slate-800 dark:bg-slate-950/40">
      <div className="mb-3 inline-flex rounded-xl bg-primary-50 p-2 text-primary-600 dark:bg-primary-500/10 dark:text-primary-300">
        {props.icon}
      </div>
      <div className="text-sm font-medium text-slate-500 dark:text-slate-400">{props.title}</div>
      <div className="mt-2 text-lg font-semibold text-slate-900 dark:text-white">{props.value}</div>
      <div className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500 dark:text-slate-400">{props.detail}</div>
      {props.href ? (
        <a href={props.href} className="mt-3 inline-flex text-xs font-medium text-primary-600 hover:text-primary-700 dark:text-primary-300">
          查看详情
        </a>
      ) : null}
    </article>
  );
}

function ScoreLine({ label, detail, score }: { label: string; detail: string; score: number }) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-3 text-sm">
        <span className="font-medium text-slate-700 dark:text-slate-300">{label}</span>
        <span className="text-slate-500 dark:text-slate-400">{score}/100</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div className="h-full rounded-full bg-primary-500" style={{ width: `${Math.max(0, Math.min(100, score))}%` }} />
      </div>
      <div className="mt-1 line-clamp-1 text-xs text-slate-400 dark:text-slate-500">{detail}</div>
    </div>
  );
}

function StageMiniStat({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="rounded-2xl bg-slate-50 px-4 py-3 dark:bg-slate-800/60">
      <div className="text-xs text-slate-400 dark:text-slate-500">{label}</div>
      <div className="mt-1 line-clamp-1 text-base font-semibold text-slate-900 dark:text-white">{value}</div>
      <div className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500 dark:text-slate-400">{detail}</div>
    </div>
  );
}

function RecommendationCard({ index, text }: { index: number; text: string }) {
  return (
    <article className="rounded-2xl border border-blue-100 bg-primary-50/50 px-4 py-4 transition-colors hover:bg-primary-50 dark:border-primary-900/60 dark:bg-primary-500/10">
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-primary-700 dark:text-primary-200">
        <span className="grid h-7 w-7 place-items-center rounded-full bg-white text-xs shadow-sm shadow-blue-100/70 dark:bg-slate-900 dark:shadow-none">
          {index}
        </span>
        <span>行动建议</span>
      </div>
      <p className="text-sm leading-6 text-slate-600 dark:text-slate-300">{text}</p>
    </article>
  );
}

function InfoCard({ title, value, detail, muted = false }: { title: string; value: string; detail: string; muted?: boolean }) {
  return (
    <article className={`rounded-2xl border px-4 py-4 shadow-sm ${muted ? 'border-slate-200 bg-slate-50 text-slate-400 shadow-none dark:border-slate-800 dark:bg-slate-800/50 dark:text-slate-500' : 'border-blue-100 bg-white/85 shadow-blue-100/50 dark:border-slate-800 dark:bg-slate-900/80'}`}>
      <div className="text-sm text-slate-500 dark:text-slate-400">{title}</div>
      <div className="mt-3 text-base font-semibold text-slate-900 dark:text-white">{value}</div>
      <div className="mt-2 text-xs text-slate-400 dark:text-slate-500">{detail}</div>
    </article>
  );
}

function PreferenceCard(props: {
  title: string;
  detail: string;
  value?: string;
  meta?: string;
  muted?: boolean;
  status: string;
}) {
  const iconTone = props.muted
    ? 'bg-slate-100 text-slate-400 dark:bg-slate-800 dark:text-slate-500'
    : 'bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-300';
  return (
    <article className={`rounded-2xl border px-4 py-4 ${props.muted ? 'border-slate-200 bg-slate-50/70 dark:border-slate-800 dark:bg-slate-800/40' : 'border-blue-100 bg-white dark:border-slate-800 dark:bg-slate-950/40'}`}>
      <div className={`mb-3 inline-flex rounded-xl p-2 ${iconTone}`}>
        <BookOpen className="h-4 w-4" />
      </div>
      <div className="flex items-start justify-between gap-3">
        <div className="text-sm font-semibold text-slate-900 dark:text-white">{props.title}</div>
        <span className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-400">
          {props.status}
        </span>
      </div>
      {props.value ? (
        <div className="mt-2 text-base font-semibold text-slate-800 dark:text-slate-100">{props.value}</div>
      ) : null}
      <div className="mt-2 text-sm leading-6 text-slate-500 dark:text-slate-400">{props.detail}</div>
      {props.meta ? (
        <div className="mt-3 text-xs leading-5 text-slate-400 dark:text-slate-500">{props.meta}</div>
      ) : null}
    </article>
  );
}

function WeakPointCard({ item, rank, compact = false }: { item: WeakPointRank; rank: number; compact?: boolean }) {
  return (
    <article className="rounded-2xl border border-blue-100 bg-white px-4 py-4 dark:border-slate-800 dark:bg-slate-950/40">
      <div className="flex items-start gap-3">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-orange-50 text-sm font-semibold text-orange-600 dark:bg-orange-500/10 dark:text-orange-300">
          {rank}
        </div>
        <div className="min-w-0 flex-1">
          <div className="font-semibold text-slate-900 dark:text-white">{item.topic}</div>
          {item.errorPattern ? (
            <div className="mt-1 inline-flex rounded-full bg-orange-50 px-2 py-0.5 text-[11px] font-medium text-orange-600 dark:bg-orange-500/10 dark:text-orange-300">
              {item.errorPattern}
            </div>
          ) : null}
          <p className={`mt-2 text-sm leading-6 text-slate-500 dark:text-slate-400 ${compact ? 'line-clamp-2' : ''}`}>
            {item.lastError || '暂无错误样本说明'}
          </p>
          {compact ? null : item.severityInferred ? (
            <div className="mt-3 text-xs text-slate-400 dark:text-slate-500">强度待接入真实数据</div>
          ) : (
            <div className="mt-3">
              <div className="mb-1 flex justify-between text-xs text-slate-500 dark:text-slate-400">
                <span>薄弱强度</span>
                <span>{item.severity}/100</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                <div className="h-full rounded-full bg-gradient-to-r from-orange-400 to-rose-500" style={{ width: `${Math.max(0, Math.min(100, item.severity))}%` }} />
              </div>
            </div>
          )}
        </div>
      </div>
    </article>
  );
}

function FactRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-slate-50 px-3 py-2 dark:bg-slate-800/60">
      <div className="text-xs text-slate-400 dark:text-slate-500">{label}</div>
      <div className="mt-1 text-slate-700 dark:text-slate-300">{value}</div>
    </div>
  );
}

function BehaviorTrendPanel(props: {
  analytics: UserProfileAnalyticsResponse | null;
  loading: boolean;
  error: string;
  onRetry: () => void;
}) {
  const trend = props.analytics?.behaviorTrend ?? [];
  const hasData = trend.some((point) => sumTrendActivity(point) > 0);
  const visibleTrend = trend.slice(-14);
  const maxActivity = Math.max(1, ...visibleTrend.map(sumTrendActivity));
  const coverage = props.analytics?.systemAnalysis.coverage;
  const practiceAccuracy = trend.length > 0 && coverage && coverage.practiceSubmissionCount > 0
    ? trend.reduce((sum, point) => sum + ((point.practiceAccuracy ?? 0) * point.practiceSubmissionCount), 0) / coverage.practiceSubmissionCount
    : null;

  return (
    <section id="behavior" className="rounded-2xl border border-blue-100/80 bg-white/85 p-5 shadow-sm shadow-blue-100/60 dark:border-slate-800 dark:bg-slate-900/80">
      <SectionTitle title="学习行为趋势" subtitle="来自近 30 天真实行为表聚合，不包含学习时长推测" />
      {props.loading ? (
        <AnalyticsStateMessage kind="loading" text="正在读取行为趋势" />
      ) : props.error ? (
        <AnalyticsStateMessage kind="error" text={props.error} onRetry={props.onRetry} />
      ) : !props.analytics || !hasData ? (
        <EmptyInline text={props.analytics ? `近 ${props.analytics.days} 天暂无真实行为记录。` : '暂无行为趋势数据。'} />
      ) : (
        <>
          <div className="mt-4 overflow-x-auto pb-1">
            <div
              className="grid min-w-[560px] gap-2"
              style={{ gridTemplateColumns: `repeat(${visibleTrend.length}, minmax(0, 1fr))` }}
            >
              {visibleTrend.map((point) => {
                const activity = sumTrendActivity(point);
                const height = activity === 0 ? 0 : Math.max(8, Math.round((activity / maxActivity) * 100));
                return (
                  <div key={point.date} className="flex min-h-[144px] flex-col items-center justify-end gap-2">
                    <div
                      className="flex h-24 w-full items-end rounded-xl bg-slate-100 px-1.5 py-1.5 dark:bg-slate-800"
                      title={`${point.date}：${activity} 次真实行为`}
                    >
                      <div
                        className="w-full rounded-lg bg-primary-500 transition-[height]"
                        style={{ height: `${height}%` }}
                      />
                    </div>
                    <span className="text-[11px] text-slate-400 dark:text-slate-500">{formatTrendDate(point.date)}</span>
                  </div>
                );
              })}
            </div>
          </div>
          {coverage ? (
            <div className="mt-4 grid gap-2 sm:grid-cols-3">
              <CoverageStat label="对话" value={`${coverage.conversationCount}次`} />
              <CoverageStat label="学习服务" value={`${coverage.serviceTaskCount}个`} />
              <CoverageStat label="练习提交" value={`${coverage.practiceSubmissionCount}次`} />
              <CoverageStat label="练习正确率" value={formatPercent(practiceAccuracy)} />
              <CoverageStat label="新增错题" value={`${coverage.newMistakeCount}条`} />
              <CoverageStat label="复习" value={`${coverage.reviewCount}次`} />
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}

function SystemAnalysisPanel(props: {
  analytics: UserProfileAnalyticsResponse | null;
  loading: boolean;
  error: string;
  onRetry: () => void;
}) {
  const analysis = props.analytics?.systemAnalysis;
  return (
    <section id="analysis" className="rounded-2xl border border-blue-100/80 bg-white/85 p-5 shadow-sm shadow-blue-100/60 dark:border-slate-800 dark:bg-slate-900/80">
      <SectionTitle title="系统分析" subtitle="由画像字段与真实行为聚合生成" />
      {props.loading ? (
        <AnalyticsStateMessage kind="loading" text="正在读取系统分析" />
      ) : props.error ? (
        <AnalyticsStateMessage kind="error" text={props.error} onRetry={props.onRetry} />
      ) : !analysis ? (
        <EmptyInline text="暂无系统分析数据。" />
      ) : !analysis.dataAvailable ? (
        <EmptyInline text={analysis.summary || '暂无足够真实数据生成系统分析。'} />
      ) : (
        <div className="mt-4 space-y-4">
          <p className="rounded-2xl bg-slate-50 px-4 py-3 text-sm leading-6 text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
            {analysis.summary}
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <InfoCard
              title="强项领域"
              value={analysis.strongestSkill || EMPTY_VALUE}
              detail={analysis.strongestSkillScore ? `掌握度 ${analysis.strongestSkillScore}%` : '真实证据不足'}
              muted={!analysis.strongestSkill}
            />
            <InfoCard
              title="重点关注"
              value={analysis.focusAreas.length > 0 ? analysis.focusAreas.join('、') : EMPTY_VALUE}
              detail={analysis.focusAreas.length > 0 ? '来自薄弱点与低掌握度字段' : '暂无明确关注项'}
              muted={analysis.focusAreas.length === 0}
            />
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <CoverageStat label="画像掌握度字段" value={`${analysis.coverage.profileSkillCount}项`} />
            <CoverageStat label="薄弱点字段" value={`${analysis.coverage.weakPointCount}项`} />
            <CoverageStat label="近 30 天活跃日" value={`${analysis.coverage.activeDays}天`} />
            <CoverageStat label="可聚合行为" value={`${sumCoverageActivity(analysis.coverage)}次`} />
          </div>
        </div>
      )}
    </section>
  );
}

function CoverageStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-slate-50 px-3 py-2 dark:bg-slate-800/60">
      <div className="text-[11px] text-slate-400 dark:text-slate-500">{label}</div>
      <div className="mt-1 text-sm font-semibold text-slate-700 dark:text-slate-200">{value}</div>
    </div>
  );
}

function AnalyticsStateMessage(props: {
  kind: 'loading' | 'error';
  text: string;
  onRetry?: () => void;
}) {
  return (
    <div className="mt-4 rounded-2xl border border-dashed border-slate-200 px-4 py-5 text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">
      <div className="flex items-center gap-2">
        {props.kind === 'loading' ? (
          <LoaderCircle className="h-4 w-4 animate-spin text-primary-500" />
        ) : (
          <TriangleAlert className="h-4 w-4 text-orange-500" />
        )}
        <span>{props.text}</span>
      </div>
      {props.kind === 'error' && props.onRetry ? (
        <button
          type="button"
          onClick={props.onRetry}
          className="mt-3 rounded-xl border border-blue-100 px-3 py-1.5 text-xs font-medium text-primary-600 transition-colors hover:bg-primary-50 dark:border-slate-700 dark:text-primary-300 dark:hover:bg-slate-800"
        >
          重试分析
        </button>
      ) : null}
    </div>
  );
}

function EmptyInline({ text }: { text: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-400 dark:border-slate-700 dark:text-slate-500">
      {text}
    </div>
  );
}

function ProfileAccessState(props: {
  icon: ReactNode;
  title: string;
  description: string;
  actionLabel: string;
  onAction: () => void;
}) {
  return (
    <div className="flex min-h-[420px] flex-col items-center justify-center rounded-2xl border border-blue-100 bg-white/85 px-6 text-center shadow-sm shadow-blue-100/60 dark:border-slate-800 dark:bg-slate-900/80">
      <div className="mb-4 rounded-2xl bg-primary-50 p-3 text-primary-600 dark:bg-primary-500/10 dark:text-primary-300">
        {props.icon}
      </div>
      <h1 className="text-xl font-semibold text-slate-900 dark:text-white">{props.title}</h1>
      <p className="mt-2 max-w-md text-sm leading-6 text-slate-500 dark:text-slate-400">{props.description}</p>
      <button
        type="button"
        onClick={props.onAction}
        className="mt-5 inline-flex h-10 items-center justify-center rounded-xl bg-primary-600 px-4 text-sm font-medium text-white shadow-lg shadow-primary-500/20 transition-colors hover:bg-primary-700"
      >
        {props.actionLabel}
      </button>
    </div>
  );
}

function countBehaviorSignals(habits: ProfileLearningHabits): number {
  return [
    habits.studyFrequency,
    habits.preferredTime,
    habits.avgSessionDuration > 0 ? habits.avgSessionDuration : '',
    habits.noteTaking ? 'noteTaking' : '',
    habits.selfTesting ? 'selfTesting' : '',
  ].filter(Boolean).length;
}

function buildResourcePreferenceCards(
  analytics: UserProfileAnalyticsResponse | null,
  loading: boolean,
  error: string,
): ProfileResourcePreference[] {
  const preferences = analytics?.preferenceAnalytics?.resourcePreferences;
  if (preferences && preferences.length > 0) {
    return preferences;
  }
  const evidenceLabel = loading
    ? '正在读取偏好证据'
    : error
      ? '偏好证据读取失败'
      : '暂无真实证据';
  return defaultResourcePreferences.map((item) => ({
    ...item,
    evidenceLabel,
  }));
}

function preferenceStatusLabel(
  item: ProfileResourcePreference,
  loading: boolean,
  error: string,
): string {
  if (loading) {
    return '读取中';
  }
  if (error) {
    return '读取失败';
  }
  return item.identified ? '已识别' : '暂无证据';
}

function buildWeakPointItems(profile: ProfileSnapshot): WeakPointRank[] {
  if (profile.weakPointRanks.length > 0) {
    return profile.weakPointRanks;
  }
  return profile.weakPoints
    .filter((topic) => topic.trim())
    .map((topic) => ({
      topic,
      severity: 0,
      lastError: '等待更多练习或评估记录补充错因。',
      severityInferred: true,
    }));
}

function formatPreferenceEvidenceMeta(item: ProfileResourcePreference): string {
  const sources: string[] = [];
  if (item.profileMentioned) {
    sources.push('画像字段');
  }
  if (item.requestCount > 0) {
    sources.push('任务请求');
  }
  if (item.generatedCount > 0 || item.downloadCount > 0) {
    sources.push('生成产物');
  }
  const parts = sources.length > 0 ? [`证据来源：${sources.join('、')}`] : [];
  const lastUsedAt = formatPreferenceDateTime(item.lastUsedAt);
  if (lastUsedAt) {
    parts.push(`最近一次：${lastUsedAt}`);
  }
  return parts.join(' · ');
}

function formatProfileSourceLabel(source?: string | null): string {
  switch ((source ?? '').trim()) {
    case 'profile_json.explanationPreference':
    case 'explanationPreference':
    case 'explanation_preference':
      return '讲解方式画像字段';
    case 'profile_json.preferredResourceTypes':
    case 'preferredResourceTypes':
    case 'preferred_resource_types':
      return '学习资源偏好画像字段';
    case 'currentGoal':
    case 'current_goal':
      return '学习目标画像字段';
    case 'learningHabits':
    case 'learning_habits':
      return '学习习惯画像字段';
    case 'cognitiveStyle':
    case 'cognitive_style':
      return '认知风格画像字段';
    case 'weakPointDetails':
    case 'weakPointDetails/weakPoints':
    case 'weak_point_details':
    case 'weakPoints':
    case 'weak_points':
      return '薄弱点画像字段';
    case 'inferredRecommendations':
    case 'inferred_recommendations':
      return '学习建议画像字段';
    case 'analytics':
      return '系统分析结果';
    default:
      return source?.trim() ? '画像分析结果' : '当前画像字段';
  }
}

function formatProfileDisplayValue(value?: string | null): string {
  const raw = value?.trim() ?? '';
  if (!raw) {
    return EMPTY_VALUE;
  }
  const replacements: Array<[RegExp, string]> = [
    [/\bstep_by_step\b/gi, '分步讲解'],
    [/\bconcept_first\b/gi, '先讲原理'],
    [/\bconcept_then_question\b/gi, '先概念后练习'],
    [/\bexample_first\b/gi, '先例子后原理'],
    [/\bvisual_first\b/gi, '先图示后讲解'],
    [/\breasoning_oriented\b/gi, '偏原理推导'],
    [/\bprocedural_oriented\b/gi, '偏步骤实操'],
    [/\bmixed\b/gi, '混合型'],
    [/\bhigh_frequency\b/gi, '高频学习'],
    [/\bstage_based\b/gi, '阶段性学习'],
  ];
  return replacements.reduce((text, [pattern, replacement]) => text.replace(pattern, replacement), raw);
}

function formatPreferenceDateTime(value?: string | null): string {
  if (!value) {
    return '';
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return '';
  }
  return parsed.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function sumTrendActivity(point: ProfileBehaviorTrendPoint): number {
  return point.conversationCount
    + point.serviceTaskCount
    + point.practiceSubmissionCount
    + point.newMistakeCount
    + point.reviewCount;
}

function sumCoverageActivity(coverage: UserProfileAnalyticsResponse['systemAnalysis']['coverage']): number {
  return coverage.conversationCount
    + coverage.serviceTaskCount
    + coverage.practiceSubmissionCount
    + coverage.newMistakeCount
    + coverage.reviewCount;
}

function analyticsTrendSummary(analytics: UserProfileAnalyticsResponse | null): string {
  if (!analytics) {
    return '';
  }
  const activeDays = analytics.systemAnalysis.coverage.activeDays;
  if (activeDays <= 0) {
    return `近 ${analytics.days} 天暂无真实行为记录`;
  }
  return `近 ${analytics.days} 天有 ${activeDays} 天学习行为记录`;
}

function formatTrendDate(date: string): string {
  const parsed = new Date(`${date}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return date.slice(5);
  }
  return parsed.toLocaleDateString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
  });
}

function formatPercent(value: number | null): string {
  if (value === null || !Number.isFinite(value)) {
    return EMPTY_VALUE;
  }
  return `${Math.round(value)}%`;
}

const NODE_STATUS_COLORS: Record<string, { bg: string; border: string; text: string; label: string }> = {
  MASTERED:    { bg: 'bg-emerald-50 dark:bg-emerald-900/30', border: 'border-emerald-300 dark:border-emerald-700', text: 'text-emerald-700 dark:text-emerald-300', label: '已掌握' },
  IN_PROGRESS: { bg: 'bg-blue-50 dark:bg-blue-900/30',    border: 'border-blue-300 dark:border-blue-700',    text: 'text-blue-700 dark:text-blue-300',    label: '学习中' },
  WEAK:        { bg: 'bg-amber-50 dark:bg-amber-900/30',  border: 'border-amber-300 dark:border-amber-700',  text: 'text-amber-700 dark:text-amber-300',  label: '薄弱' },
  NOT_STARTED: { bg: 'bg-slate-50 dark:bg-slate-800/60',  border: 'border-slate-200 dark:border-slate-700',  text: 'text-slate-500 dark:text-slate-400',  label: '未开始' },
};
const KNOWLEDGE_STATUS_ORDER: Array<KnowledgeGraphResponse['nodes'][number]['status']> = ['WEAK', 'IN_PROGRESS', 'NOT_STARTED', 'MASTERED'];
type KnowledgeGraphNodeView = KnowledgeGraphResponse['nodes'][number];

function KnowledgeGraphPanel(props: {
  graph: KnowledgeGraphResponse | null;
  loading: boolean;
  error: string;
}) {
  if (props.loading) {
    return (
      <div className="mt-4 flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
        <LoaderCircle className="h-4 w-4 animate-spin text-primary-500" />
        正在读取知识掌握图谱
      </div>
    );
  }
  if (props.error) {
    return (
      <div className="mt-4 rounded-xl border border-amber-100 bg-amber-50/60 px-4 py-3 text-sm text-amber-700 dark:border-amber-900/40 dark:bg-amber-900/20 dark:text-amber-300">
        读取失败：{props.error}
      </div>
    );
  }
  if (!props.graph || props.graph.nodes.length === 0) {
    return (
      <div className="mt-4 rounded-xl border border-slate-100 bg-slate-50/60 px-4 py-6 text-center text-sm text-slate-400 dark:border-slate-800 dark:bg-slate-800/40 dark:text-slate-500">
        <Network className="mx-auto mb-2 h-8 w-8 opacity-40" />
        暂无知识掌握图谱数据。完成练习、评估或路径规划后，系统会自动整理知识点。
      </div>
    );
  }

  const { nextRecommended } = props.graph;
  const nodes = compactKnowledgeGraphNodes(props.graph.nodes);
  const nodeByKey = new Map(nodes.map((node) => [node.key, node]));
  const recommendedNodes = nextRecommended
    .map((key) => nodeByKey.get(key))
    .filter((node): node is KnowledgeGraphNodeView => Boolean(node));
  const focusNodes = recommendedNodes.length > 0
    ? recommendedNodes.slice(0, 3)
    : nodes
      .filter((node) => node.status === 'WEAK' || node.status === 'IN_PROGRESS')
      .sort((a, b) => a.mastery - b.mastery)
      .slice(0, 3);
  const focusKeySet = new Set(focusNodes.map((node) => node.key));
  const groupedNodes = KNOWLEDGE_STATUS_ORDER
    .map((status) => ({
      status,
      nodes: nodes
        .filter((node) => node.status === status && !focusKeySet.has(node.key))
        .sort((a, b) => a.mastery - b.mastery),
    }))
    .filter((group) => group.nodes.length > 0);
  const visibleGroups = groupedNodes.map((group) => ({
    ...group,
    nodes: group.nodes.slice(0, group.status === 'MASTERED' ? 4 : 6),
  }));
  const hiddenCount = groupedNodes.reduce(
    (total, group) => total + Math.max(0, group.nodes.length - (group.status === 'MASTERED' ? 4 : 6)),
    0,
  );
  const statusCounts = KNOWLEDGE_STATUS_ORDER.map((status) => ({
    status,
    count: nodes.filter((node) => node.status === status).length,
  }));

  return (
    <div className="mt-4 space-y-4">
      <div className="grid gap-2 sm:grid-cols-4">
        {statusCounts.map(({ status, count }) => {
          const style = NODE_STATUS_COLORS[status] ?? NODE_STATUS_COLORS.NOT_STARTED;
          return (
            <div key={status} className={`rounded-xl border px-3 py-2 ${style.bg} ${style.border}`}>
              <div className={`text-xs font-medium ${style.text}`}>{style.label}</div>
              <div className="mt-1 text-lg font-semibold text-slate-900 dark:text-white">{count}</div>
            </div>
          );
        })}
      </div>

      {focusNodes.length > 0 && (
        <div className="rounded-xl border border-blue-100 bg-blue-50/70 px-4 py-4 dark:border-blue-900/40 dark:bg-blue-950/30">
          <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
            <div>
              <div className="text-sm font-semibold text-blue-700 dark:text-blue-300">下一步优先关注</div>
              <div className="mt-1 text-xs text-blue-500 dark:text-blue-400">先处理这几个低掌握度知识点，下方列表不再重复展示。</div>
            </div>
            <span className="rounded-full bg-white/70 px-2.5 py-1 text-xs font-medium text-blue-600 ring-1 ring-blue-100 dark:bg-slate-900/50 dark:text-blue-300 dark:ring-blue-900/50">
              {focusNodes.length} 个重点
            </span>
          </div>
          <div className="grid gap-2 lg:grid-cols-3">
            {focusNodes.map((node, index) => (
              <KnowledgeFocusCard key={node.key} node={node} rank={index + 1} />
            ))}
          </div>
        </div>
      )}

      {visibleGroups.length > 0 ? (
        <div className="rounded-xl border border-slate-100 bg-white/70 px-4 py-4 dark:border-slate-800 dark:bg-slate-900/40">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div className="text-sm font-semibold text-slate-800 dark:text-slate-200">其余知识点概览</div>
            <div className="text-xs text-slate-400 dark:text-slate-500">按状态归类，低掌握度在前</div>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            {visibleGroups.map((group) => {
              const style = NODE_STATUS_COLORS[group.status] ?? NODE_STATUS_COLORS.NOT_STARTED;
              return (
                <section key={group.status} className="min-w-0">
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <div className={`text-sm font-semibold ${style.text}`}>{style.label}</div>
                    <div className="text-xs text-slate-400 dark:text-slate-500">{group.nodes.length} 个</div>
                  </div>
                  <div className="space-y-2">
                    {group.nodes.map((node) => (
                      <KnowledgeNodeRow key={node.key} node={node} />
                    ))}
                  </div>
                </section>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="rounded-xl border border-slate-100 bg-slate-50/60 px-4 py-4 text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-800/40 dark:text-slate-400">
          当前只有优先关注项，其余知识点会在继续学习后补充。
        </div>
      )}

      {hiddenCount > 0 && (
        <div className="text-xs text-slate-400 dark:text-slate-500">
          已收起 {hiddenCount} 个低优先级知识点，页面优先保留当前更需要关注的内容。
        </div>
      )}
    </div>
  );
}

function KnowledgeFocusCard(props: {
  node: KnowledgeGraphNodeView;
  rank: number;
}) {
  const { node, rank } = props;
  const style = NODE_STATUS_COLORS[node.status] ?? NODE_STATUS_COLORS.NOT_STARTED;
  const pct = Math.round(node.mastery * 100);
  const barColor = knowledgeProgressColor(node.status);

  return (
    <div className={`rounded-xl border px-3 py-3 ${style.bg} ${style.border}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold text-primary-600 dark:text-primary-300">重点 {rank}</div>
          <div className={`mt-1 truncate text-sm font-semibold ${style.text}`}>{node.topic}</div>
        </div>
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${style.bg} ${style.text} ring-1 ring-inset ring-current/20`}>
          {style.label}
        </span>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/70 dark:bg-slate-700/70">
          <div className={`h-full rounded-full ${barColor}`} style={{ width: `${pct}%` }} />
        </div>
        <span className="shrink-0 text-xs font-medium text-slate-500 dark:text-slate-400">{pct}%</span>
      </div>
    </div>
  );
}

function KnowledgeNodeRow(props: {
  node: KnowledgeGraphNodeView;
}) {
  const { node } = props;
  const style = NODE_STATUS_COLORS[node.status] ?? NODE_STATUS_COLORS.NOT_STARTED;
  const pct = Math.round(node.mastery * 100);
  const barColor = knowledgeProgressColor(node.status);

  return (
    <div className="rounded-lg border border-slate-100 bg-slate-50/60 px-3 py-2 dark:border-slate-800 dark:bg-slate-950/30">
      <div className="flex min-w-0 items-center justify-between gap-3">
        <div className={`min-w-0 truncate text-sm font-medium ${style.text}`}>{node.topic}</div>
        <div className="shrink-0 text-xs text-slate-500 dark:text-slate-400">{pct}%</div>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white dark:bg-slate-800">
          <div className={`h-full rounded-full ${barColor}`} style={{ width: `${pct}%` }} />
        </div>
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${style.bg} ${style.text}`}>
          {style.label}
        </span>
      </div>
    </div>
  );
}

function compactKnowledgeGraphNodes(nodes: KnowledgeGraphNodeView[]): KnowledgeGraphNodeView[] {
  const byTopic = new Map<string, KnowledgeGraphNodeView>();
  for (const node of nodes) {
    const topicKey = normalizeKnowledgeTopicKey(node.topic);
    if (!topicKey) {
      continue;
    }
    const existing = byTopic.get(topicKey);
    if (!existing || shouldUseKnowledgeNode(node, existing)) {
      byTopic.set(topicKey, node);
    }
  }
  return Array.from(byTopic.values());
}

function shouldUseKnowledgeNode(candidate: KnowledgeGraphNodeView, existing: KnowledgeGraphNodeView): boolean {
  const statusDelta = knowledgeStatusRank(candidate.status) - knowledgeStatusRank(existing.status);
  if (statusDelta !== 0) {
    return statusDelta < 0;
  }
  if (candidate.mastery !== existing.mastery) {
    return candidate.mastery < existing.mastery;
  }
  return candidate.topic.length < existing.topic.length;
}

function normalizeKnowledgeTopicKey(topic: string): string {
  const normalized = topic
    .trim()
    .toLowerCase()
    .replace(/[：。·・／]/g, (char) => ({ '：': ':', '。': '.', '·': '.', '・': '.', '／': '/' }[char] ?? char));
  const head = normalized.split(/[:./\\|_-]/)[0] || normalized;
  return head
    .replace(/\s+/g, '')
    .replace(/(基础语法入门|基础语法|基础入门|入门|基础|概述)$/u, '');
}

function knowledgeStatusRank(status: KnowledgeGraphNodeView['status']): number {
  const index = KNOWLEDGE_STATUS_ORDER.indexOf(status);
  return index >= 0 ? index : KNOWLEDGE_STATUS_ORDER.length;
}

function knowledgeProgressColor(status: KnowledgeGraphNodeView['status']): string {
  if (status === 'MASTERED') {
    return 'bg-emerald-500';
  }
  if (status === 'IN_PROGRESS') {
    return 'bg-blue-500';
  }
  if (status === 'WEAK') {
    return 'bg-amber-500';
  }
  return 'bg-slate-300';
}
