import { useCallback, useEffect, useMemo, type ReactNode, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import {
  CalendarClock,
  LineChart,
  LoaderCircle,
  Lock,
  Target,
  TriangleAlert,
  UserRoundSearch,
} from 'lucide-react';
import { LineChart as RechartsLineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts';
import RadarChart from '../components/RadarChart';
import { getErrorMessage } from '../api/request';
import {
  smartEngineApi,
  type ProfileBehaviorTrendPoint,
  type UserProfileAnalyticsResponse,
} from '../api/smartEngine';
import type { LayoutOutletContext } from '../components/Layout';
import {
  EMPTY_VALUE,
  type ProfileSnapshot,
  type WeakPointRank,
} from './LearningStudioDemoPage.types';
import { mapProfileResponse } from './LearningStudioDemoPage.profileMapping';

export default function ProfilePage() {
  const { isAuthenticated, currentUser, openAuthModal } = useOutletContext<LayoutOutletContext>();
  const [profile, setProfile] = useState<ProfileSnapshot | null>(null);
  const [updatedAt, setUpdatedAt] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [analytics, setAnalytics] = useState<UserProfileAnalyticsResponse | null>(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [analyticsError, setAnalyticsError] = useState('');

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

  useEffect(() => {
    void loadProfile();
    void loadAnalytics();
  }, [loadAnalytics, loadProfile]);

  useEffect(() => {
    const handleProfileUpdated = () => {
      void loadProfile();
      void loadAnalytics();
    };
    window.addEventListener('app:profile-updated', handleProfileUpdated);
    return () => window.removeEventListener('app:profile-updated', handleProfileUpdated);
  }, [loadAnalytics, loadProfile]);

  const displayName = currentUser?.fullName || currentUser?.loginId || currentUser?.username || '同学';
  const weakPointItems = useMemo(() => profile ? buildWeakPointItems(profile).slice(0, 3) : [], [profile]);
  const trendSummary = useMemo(() => buildTrendSummary(analytics), [analytics]);

  if (!isAuthenticated) {
    return (
      <ProfileShell>
        <ProfileAccessState
          icon={<Lock className="h-6 w-6" />}
          title="登录后查看学习画像"
          description="登录后查看你的学习节奏、能力维度和重点关注内容。"
          actionLabel="去登录"
          onAction={() => openAuthModal('login', '登录后查看学习画像')}
        />
      </ProfileShell>
    );
  }

  if (loading && !profile) {
    return (
      <ProfileShell>
        <div className="flex min-h-[420px] items-center justify-center rounded-[24px] bg-white/64 text-sm text-slate-500 shadow-[0_14px_40px_rgba(59,97,155,0.08)] backdrop-blur dark:bg-slate-900/60 dark:text-slate-400 dark:shadow-slate-950/20">
          <LoaderCircle className="mr-2 h-4 w-4 animate-spin text-primary-500" />
          正在整理学习画像
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
          onAction={() => {
            void loadProfile();
            void loadAnalytics();
          }}
        />
      </ProfileShell>
    );
  }

  if (!profile) {
    return (
      <ProfileShell>
        <ProfileAccessState
          icon={<UserRoundSearch className="h-6 w-6" />}
          title="暂无学习画像"
          description="完成对话、练习或学习服务后，系统会逐步补全画像。"
          actionLabel="刷新画像"
          onAction={() => {
            void loadProfile();
            void loadAnalytics();
          }}
        />
      </ProfileShell>
    );
  }

  return (
    <ProfileShell>
      <div className="min-w-0 space-y-5">
        <header className="rounded-[28px] bg-white/72 px-5 py-5 shadow-[0_18px_56px_rgba(59,97,155,0.10)] backdrop-blur-xl dark:bg-slate-900/68 dark:shadow-slate-950/20 md:px-6">
          <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
            <div className="min-w-0">
              <div className="inline-flex items-center gap-2 rounded-full bg-primary-50 px-3 py-1 text-xs font-semibold text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
                <UserRoundSearch className="h-3.5 w-3.5" />
                学习画像
              </div>
              <h1 className="mt-4 text-2xl font-semibold tracking-normal text-slate-950 dark:text-white md:text-3xl">
                {displayName}，这是你的学习信号面板
              </h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500 dark:text-slate-400">
                这里汇总近期学习趋势、能力维度和最需要关注的薄弱点，便于快速判断当前学习状态。
              </p>
            </div>
            <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-center md:flex-col md:items-end">
              <button
                type="button"
                onClick={() => {
                  void loadProfile();
                  void loadAnalytics();
                }}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-primary-600 px-4 text-sm font-medium text-white shadow-sm shadow-primary-500/20 outline-none transition-all hover:bg-primary-700 focus-visible:shadow-[0_10px_24px_rgba(59,130,246,0.24)] disabled:cursor-not-allowed disabled:opacity-60 dark:focus-visible:shadow-[0_10px_24px_rgba(37,99,235,0.24)]"
                disabled={loading || analyticsLoading}
              >
                {loading || analyticsLoading ? (
                  <LoaderCircle className="h-4 w-4 animate-spin" />
                ) : (
                  <CalendarClock className="h-4 w-4" />
                )}
                刷新画像
              </button>
              <div className="text-xs text-slate-400 dark:text-slate-500">
                更新时间：{updatedAt ? new Date(updatedAt).toLocaleString('zh-CN') : EMPTY_VALUE}
              </div>
            </div>
          </div>
        </header>

        <BehaviorTrendPanel
          analytics={analytics}
          loading={analyticsLoading}
          error={analyticsError}
          summary={trendSummary}
          onRetry={() => void loadAnalytics()}
        />

        <div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,1.08fr)_minmax(360px,0.92fr)]">
          <DimensionPanel profile={profile} />
          <WeakPointTopThree items={weakPointItems} />
        </div>
      </div>
    </ProfileShell>
  );
}

function ProfileShell({ children }: { children: ReactNode }) {
  return (
    <div className="profile-page mx-auto w-full max-w-[1280px] min-w-0 px-1 pb-10">
      {children}
    </div>
  );
}

function PanelShell({
  id,
  children,
  className = '',
}: {
  id?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      id={id}
      className={`min-w-0 rounded-[24px] bg-white/66 p-5 shadow-[0_14px_40px_rgba(59,97,155,0.08)] backdrop-blur dark:bg-slate-900/62 dark:shadow-slate-950/20 md:p-6 ${className}`}
    >
      {children}
    </section>
  );
}

function SectionTitle({ icon, title, subtitle }: { icon: ReactNode; title: string; subtitle: string }) {
  return (
    <div className="flex items-start gap-3">
      <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200">
        {icon}
      </div>
      <div className="min-w-0">
        <h2 className="text-lg font-semibold text-slate-950 dark:text-white">{title}</h2>
        <p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">{subtitle}</p>
      </div>
    </div>
  );
}

function BehaviorTrendPanel(props: {
  analytics: UserProfileAnalyticsResponse | null;
  loading: boolean;
  error: string;
  summary: TrendSummary;
  onRetry: () => void;
}) {
  const trend = props.analytics?.behaviorTrend ?? [];
  const visibleTrend = trend.slice(-14);
  const hasData = visibleTrend.some((point) => sumTrendActivity(point) > 0);

  return (
    <PanelShell id="behavior">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
        <SectionTitle
          icon={<LineChart className="h-5 w-5" />}
          title="学习行为趋势"
          subtitle="展示近 30 天学习节奏，聚焦对话、服务、练习和复盘。"
        />
        <div className="grid gap-2 sm:grid-cols-3 lg:min-w-[420px]">
          <TrendMetric label="活跃天数" value={`${props.summary.activeDays}天`} />
          <TrendMetric label="学习互动" value={`${props.summary.totalActivity}次`} />
          <TrendMetric label="练习正确率" value={formatAccuracy(props.summary.practiceAccuracy)} />
        </div>
      </div>

      <div className="mt-6 min-h-[230px]">
        {props.loading ? (
          <AnalyticsStateMessage text="正在读取行为趋势" />
        ) : props.error ? (
          <div className="rounded-2xl bg-red-50/80 p-4 text-sm text-red-700 shadow-sm shadow-red-100/70 dark:bg-red-950/30 dark:text-red-200 dark:shadow-red-950/20">
            <div>{props.error}</div>
            <button
              type="button"
              onClick={props.onRetry}
              className="mt-3 rounded-xl bg-white px-3 py-1.5 text-xs font-medium text-red-700 transition-colors hover:bg-red-100 dark:bg-red-950 dark:text-red-200 dark:hover:bg-red-900"
            >
              重新读取
            </button>
          </div>
        ) : hasData ? (
          <div className="grid min-w-0 min-h-[230px] gap-4 lg:grid-cols-[minmax(0,1fr)_220px]">
            <div className="min-w-0 overflow-hidden rounded-2xl bg-slate-50 p-4 dark:bg-slate-950/40">
              <ResponsiveContainer width="100%" height={230}>
                <RechartsLineChart data={visibleTrend.map(p => ({
                  date: formatTrendDate(p.date),
                  对话: p.conversationCount,
                  学习服务: p.serviceTaskCount,
                  练习提交: p.practiceSubmissionCount,
                  复盘: p.reviewCount,
                }))}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                  <XAxis dataKey="date" tick={{ fontSize: 11, fill: '#94a3b8' }} />
                  <YAxis tick={{ fontSize: 11, fill: '#94a3b8' }} />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: 'rgba(255,255,255,0.95)',
                      border: '1px solid #e2e8f0',
                      borderRadius: '12px',
                      boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
                      fontSize: '12px',
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '8px' }} />
                  <Line type="monotone" dataKey="对话" stroke="#6366f1" strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} />
                  <Line type="monotone" dataKey="学习服务" stroke="#06b6d4" strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} />
                  <Line type="monotone" dataKey="练习提交" stroke="#10b981" strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} />
                  <Line type="monotone" dataKey="复盘" stroke="#64748b" strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} />
                </RechartsLineChart>
              </ResponsiveContainer>
            </div>
            <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-1">
              <BehaviorSignal label="对话" value={props.summary.conversationCount} color="bg-primary-500" />
              <BehaviorSignal label="学习服务" value={props.summary.serviceTaskCount} color="bg-cyan-500" />
              <BehaviorSignal label="练习提交" value={props.summary.practiceSubmissionCount} color="bg-emerald-500" />
              <BehaviorSignal label="新增错题" value={props.summary.newMistakeCount} color="bg-amber-500" />
              <BehaviorSignal label="复盘" value={props.summary.reviewCount} color="bg-slate-500" />
            </div>
          </div>
        ) : (
          <EmptyInline text={props.analytics ? `近 ${props.analytics.days} 天暂无学习记录。` : '暂无行为趋势数据。'} />
        )}
      </div>
    </PanelShell>
  );
}

function DimensionPanel({ profile }: { profile: ProfileSnapshot }) {
  const dimensionScores = profile.dimensionScores;
  const hasScores = dimensionScores.length > 0;

  return (
    <PanelShell id="dimensions" className="min-h-[520px]">
      <SectionTitle
        icon={<Target className="h-5 w-5" />}
        title="画像维度可视化"
        subtitle="综合当前画像记录，快速观察掌握、目标、习惯与适配状态。"
      />

      {hasScores ? (
        <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,0.92fr)_minmax(260px,0.8fr)]">
          <div className="rounded-2xl bg-slate-50 p-3 dark:bg-slate-950/40">
            <RadarChart
              data={dimensionScores.map((item) => ({
                subject: item.subject,
                score: item.score,
                fullMark: item.fullMark,
                description: item.description,
              }))}
              height={340}
              className="min-h-[340px]"
            />
          </div>
          <div className="space-y-3">
            {dimensionScores.map((item) => (
              <ScoreLine key={item.key} label={item.subject} detail={item.hint} score={item.score} />
            ))}
          </div>
        </div>
      ) : (
        <div className="mt-6">
          <EmptyInline text="当前画像暂无可视化维度，完成更多学习记录后会更新。" />
        </div>
      )}
    </PanelShell>
  );
}

function WeakPointTopThree({ items }: { items: WeakPointRank[] }) {
  return (
    <PanelShell id="key-weak" className="min-h-[520px]">
      <SectionTitle
        icon={<TriangleAlert className="h-5 w-5" />}
        title="关键薄弱点 Top3"
        subtitle="只保留优先级最高的三个薄弱点，避免一次承载过多信息。"
      />

      <div className="mt-6 space-y-3">
        {items.length > 0 ? items.map((item, index) => (
          <WeakPointCard key={`${item.topic}-${index}`} item={item} rank={index + 1} />
        )) : (
          <EmptyInline text="暂无明确薄弱点，继续完成练习或学习评估后会更新。" />
        )}
      </div>
    </PanelShell>
  );
}

function TrendMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl bg-slate-50 px-4 py-3 dark:bg-slate-950/40">
      <div className="text-xs text-slate-400 dark:text-slate-500">{label}</div>
      <div className="mt-1 text-lg font-semibold text-slate-950 dark:text-white">{value}</div>
    </div>
  );
}

function BehaviorSignal({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="flex items-center justify-between rounded-2xl bg-white/72 px-3.5 py-3 shadow-sm shadow-slate-200/50 dark:bg-slate-950/30 dark:shadow-none">
      <div className="flex min-w-0 items-center gap-2.5">
        <span className={`h-2.5 w-2.5 rounded-full ${color}`} />
        <span className="truncate text-sm text-slate-600 dark:text-slate-300">{label}</span>
      </div>
      <span className="text-sm font-semibold text-slate-950 dark:text-white">{value}</span>
    </div>
  );
}

function ScoreLine({ label, detail, score }: { label: string; detail: string; score: number }) {
  const normalizedScore = Math.max(0, Math.min(100, score));
  return (
    <div className="rounded-2xl bg-white/62 p-4 shadow-sm shadow-slate-200/45 transition-colors hover:bg-primary-50/45 dark:bg-slate-950/24 dark:shadow-none dark:hover:bg-primary-500/5">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-slate-800 dark:text-slate-100">{label}</div>
          <div className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500 dark:text-slate-400">{detail}</div>
        </div>
        <div className="shrink-0 text-lg font-semibold text-slate-950 dark:text-white">{normalizedScore}</div>
      </div>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div className="h-full rounded-full bg-primary-500" style={{ width: `${normalizedScore}%` }} />
      </div>
    </div>
  );
}

function WeakPointCard({ item, rank }: { item: WeakPointRank; rank: number }) {
  const severity = Math.max(0, Math.min(100, item.severity));
  const level = severity >= 80 ? '高优先级' : severity >= 60 ? '中优先级' : '待观察';

  return (
    <article className="rounded-2xl bg-slate-50/70 p-4 shadow-sm shadow-slate-200/50 transition-colors hover:bg-amber-50/60 dark:bg-slate-950/30 dark:shadow-none dark:hover:bg-amber-500/5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-xs font-semibold text-amber-600 dark:text-amber-300">Top {rank}</div>
          <h3 className="mt-1 text-base font-semibold text-slate-950 dark:text-white">{item.topic}</h3>
        </div>
        <div className="shrink-0 rounded-full bg-white/86 px-3 py-1 text-xs font-medium text-slate-600 dark:bg-slate-900/70 dark:text-slate-300">
          {level}
        </div>
      </div>

      <div className="mt-4">
        <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
          <span>关注强度</span>
          <span>{severity}%</span>
        </div>
        <div className="mt-2 h-2 overflow-hidden rounded-full bg-white dark:bg-slate-800">
          <div className="h-full rounded-full bg-amber-500" style={{ width: `${severity}%` }} />
        </div>
      </div>

      <div className="mt-4 space-y-2 text-sm leading-6 text-slate-600 dark:text-slate-300">
        <p>{item.lastError || '等待更多练习或评估记录补充错因。'}</p>
        {item.errorPattern ? (
          <p className="text-xs text-slate-500 dark:text-slate-400">可能原因：{item.errorPattern}</p>
        ) : null}
        {item.severityInferred ? (
          <p className="text-xs text-slate-400 dark:text-slate-500">等待更多记录确认关注优先级</p>
        ) : null}
      </div>
    </article>
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
    <div className="flex min-h-[420px] items-center justify-center rounded-[28px] bg-white/72 p-6 text-center shadow-[0_18px_56px_rgba(59,97,155,0.10)] backdrop-blur-xl dark:bg-slate-900/68 dark:shadow-slate-950/20">
      <div className="max-w-md">
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary-50 text-primary-600 dark:bg-primary-500/10 dark:text-primary-300">
          {props.icon}
        </div>
        <h1 className="text-xl font-semibold text-slate-950 dark:text-white">{props.title}</h1>
        <p className="mt-2 text-sm leading-6 text-slate-500 dark:text-slate-400">{props.description}</p>
        <button
          type="button"
          onClick={props.onAction}
          className="mt-5 inline-flex h-10 items-center justify-center rounded-xl bg-primary-600 px-4 text-sm font-medium text-white shadow-sm shadow-primary-500/20 outline-none transition-all hover:bg-primary-700 focus-visible:shadow-[0_10px_24px_rgba(59,130,246,0.24)] dark:focus-visible:shadow-[0_10px_24px_rgba(37,99,235,0.24)]"
        >
          {props.actionLabel}
        </button>
      </div>
    </div>
  );
}

function AnalyticsStateMessage({ text }: { text: string }) {
  return (
    <div className="flex min-h-[230px] items-center justify-center rounded-2xl bg-slate-50 text-sm text-slate-500 dark:bg-slate-950/40 dark:text-slate-400">
      <LoaderCircle className="mr-2 h-4 w-4 animate-spin text-primary-500" />
      {text}
    </div>
  );
}

function EmptyInline({ text }: { text: string }) {
  return (
    <div className="flex min-h-[160px] items-center justify-center rounded-2xl bg-slate-50/70 px-4 py-8 text-center text-sm text-slate-500 dark:bg-slate-950/30 dark:text-slate-400">
      {text}
    </div>
  );
}

interface TrendSummary {
  activeDays: number;
  totalActivity: number;
  conversationCount: number;
  serviceTaskCount: number;
  practiceSubmissionCount: number;
  newMistakeCount: number;
  reviewCount: number;
  practiceAccuracy: number | null;
}

function buildTrendSummary(analytics: UserProfileAnalyticsResponse | null): TrendSummary {
  const trend = analytics?.behaviorTrend ?? [];
  const coverage = analytics?.systemAnalysis.coverage;
  const totals = trend.reduce(
    (summary, point) => ({
      conversationCount: summary.conversationCount + point.conversationCount,
      serviceTaskCount: summary.serviceTaskCount + point.serviceTaskCount,
      practiceSubmissionCount: summary.practiceSubmissionCount + point.practiceSubmissionCount,
      newMistakeCount: summary.newMistakeCount + point.newMistakeCount,
      reviewCount: summary.reviewCount + point.reviewCount,
    }),
    {
      conversationCount: 0,
      serviceTaskCount: 0,
      practiceSubmissionCount: 0,
      newMistakeCount: 0,
      reviewCount: 0,
    },
  );
  const practiceAccuracy = coverage && coverage.practiceSubmissionCount > 0
    ? weightedPracticeAccuracy(trend)
    : null;
  const conversationCount = coverage?.conversationCount ?? totals.conversationCount;
  const serviceTaskCount = coverage?.serviceTaskCount ?? totals.serviceTaskCount;
  const practiceSubmissionCount = coverage?.practiceSubmissionCount ?? totals.practiceSubmissionCount;
  const newMistakeCount = coverage?.newMistakeCount ?? totals.newMistakeCount;
  const reviewCount = coverage?.reviewCount ?? totals.reviewCount;
  return {
    activeDays: coverage?.activeDays ?? trend.filter((point) => sumTrendActivity(point) > 0).length,
    totalActivity: conversationCount
      + serviceTaskCount
      + practiceSubmissionCount
      + newMistakeCount
      + reviewCount,
    conversationCount,
    serviceTaskCount,
    practiceSubmissionCount,
    newMistakeCount,
    reviewCount,
    practiceAccuracy,
  };
}

function weightedPracticeAccuracy(trend: ProfileBehaviorTrendPoint[]): number | null {
  const validPoints = trend.filter((point) => point.practiceAccuracy !== null && point.practiceSubmissionCount > 0);
  const totalSubmissions = validPoints.reduce((sum, point) => sum + point.practiceSubmissionCount, 0);
  if (totalSubmissions === 0) {
    return null;
  }
  return validPoints.reduce(
    (sum, point) => sum + (point.practiceAccuracy ?? 0) * point.practiceSubmissionCount,
    0,
  ) / totalSubmissions;
}

function sumTrendActivity(point: ProfileBehaviorTrendPoint): number {
  return point.conversationCount
    + point.serviceTaskCount
    + point.practiceSubmissionCount
    + point.newMistakeCount
    + point.reviewCount;
}

function buildWeakPointItems(profile: ProfileSnapshot): WeakPointRank[] {
  if (profile.weakPointRanks.length > 0) {
    return profile.weakPointRanks;
  }
  return profile.weakPoints
    .filter((topic) => topic.trim())
    .map((topic, index) => ({
      topic,
      severity: Math.max(45, 76 - index * 10),
      lastError: '等待更多练习或评估记录补充错因。',
      severityInferred: true,
    }));
}

function formatAccuracy(value: number | null): string {
  if (value === null) {
    return EMPTY_VALUE;
  }
  return `${Math.round(value)}%`;
}

function formatTrendDate(value: string): string {
  if (!value) {
    return '--';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value.slice(5) || value;
  }
  return `${date.getMonth() + 1}/${date.getDate()}`;
}
