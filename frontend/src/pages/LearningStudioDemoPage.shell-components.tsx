import type { CSSProperties, ReactNode } from 'react';
import { BrainCircuit, Send, Square, TrendingUp } from 'lucide-react';

export function EngineSectionHeader(props: {
  icon: ReactNode;
  title: string;
  subtitle: string;
}) {
  return (
    <div className="flex items-start gap-3">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-blue-50 text-primary-600 ring-1 ring-blue-100 dark:bg-primary-500/10 dark:text-primary-300 dark:ring-primary-500/20">
        {props.icon}
      </div>
      <div className="min-w-0">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-white">{props.title}</h2>
        <p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">{props.subtitle}</p>
      </div>
    </div>
  );
}

export function ServiceHeroVisual() {
  return (
    <div className="relative hidden min-h-[420px] overflow-hidden lg:block">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_52%_43%,rgba(59,130,246,0.24),transparent_32%),radial-gradient(circle_at_35%_22%,rgba(125,211,252,0.24),transparent_17%)]" />
      <div className="absolute left-1/2 top-[61%] h-20 w-[330px] -translate-x-1/2 rounded-[50%] border border-blue-200/80 bg-blue-100/42 shadow-[0_24px_56px_rgba(61,116,239,0.2)]" />
      <div className="absolute left-1/2 top-[58%] h-12 w-[260px] -translate-x-1/2 rounded-[50%] border border-cyan-100/80 bg-white/48 shadow-[0_18px_42px_rgba(14,165,233,0.15)]" />
      <div className="absolute left-1/2 top-[45%] h-48 w-48 -translate-x-1/2 -translate-y-1/2 rounded-[34px] border border-blue-200/80 bg-white/35 shadow-[0_36px_90px_rgba(64,111,214,0.18)] backdrop-blur-md" />
      <div className="absolute left-1/2 top-[42%] flex h-[136px] w-[136px] -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-[30px] border border-blue-200/80 bg-gradient-to-br from-blue-400 via-blue-600 to-cyan-400 text-5xl font-black text-white shadow-[0_28px_70px_rgba(37,99,235,0.36)]">
        AI
      </div>
      <div className="absolute left-[17%] top-[30%] h-3 w-3 rounded-full bg-cyan-300 shadow-[0_0_22px_rgba(34,211,238,0.9)]" />
      <div className="absolute right-[20%] top-[34%] h-3 w-3 rounded-full bg-blue-400 shadow-[0_0_22px_rgba(59,130,246,0.75)]" />
      <div className="absolute left-[34%] top-[18%] h-2 w-2 rounded-full bg-emerald-300 shadow-[0_0_18px_rgba(52,211,153,0.75)]" />
      <div className="absolute right-[23%] bottom-[25%] h-2.5 w-2.5 rounded-full bg-cyan-300 shadow-[0_0_18px_rgba(34,211,238,0.8)]" />
      <div className="absolute left-[19%] top-[31%] h-px w-[62%] rotate-[28deg] bg-gradient-to-r from-transparent via-blue-300/80 to-transparent" />
      <div className="absolute left-[23%] top-[57%] h-px w-[58%] -rotate-[20deg] bg-gradient-to-r from-transparent via-cyan-300/70 to-transparent" />
      <div className="absolute left-[25%] top-[22%] h-64 w-64 rounded-full border border-blue-200/50" />
      <div className="absolute left-[22%] top-[28%] h-52 w-72 rotate-12 rounded-[50%] border border-cyan-200/55" />
      <span className="absolute right-9 top-20 rounded-xl bg-blue-50/90 px-3 py-1.5 text-sm font-semibold text-primary-600 shadow-sm shadow-blue-100/80 ring-1 ring-blue-100">
        智能分析
      </span>
      <span className="absolute left-14 top-36 rounded-xl bg-cyan-50/90 px-3 py-1.5 text-sm font-semibold text-cyan-600 shadow-sm shadow-cyan-100/80 ring-1 ring-cyan-100">
        精准推荐
      </span>
      <span className="absolute bottom-28 right-5 rounded-xl bg-emerald-50/90 px-3 py-1.5 text-sm font-semibold text-emerald-600 shadow-sm shadow-emerald-100/80 ring-1 ring-emerald-100">
        学习进化
      </span>
    </div>
  );
}

export function LearningEffectPreview(props: {
  selectedServiceLabel: string;
  taskId: string;
  taskProgress: number;
  taskStatus: string;
  resultLineCount: number;
  downloadCount: number;
}) {
  const hasTask = Boolean(props.taskId);
  const progressLabel = hasTask ? `${Math.round(props.taskProgress)}%` : '待提交';
  const linePercent = Math.min(100, props.resultLineCount * 12);
  const assetPercent = Math.min(100, props.downloadCount * 25);

  return (
    <section className="h-full rounded-[22px] border border-blue-100/80 bg-white/88 p-4 shadow-sm shadow-blue-100/50 dark:border-slate-800 dark:bg-slate-900/80 sm:rounded-[24px] sm:p-6">
      <div className="flex items-start gap-3 sm:items-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-blue-50 text-primary-600 ring-1 ring-blue-100 dark:bg-primary-500/10 dark:text-primary-300 dark:ring-primary-500/20">
          <TrendingUp className="h-4 w-4" />
        </div>
        <div>
          <h2 className="text-lg font-semibold text-slate-900 dark:text-white">学习效果预览</h2>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">只展示真实任务状态，不展示预测分假数据。</p>
        </div>
      </div>

      {hasTask ? (
        <div className="mt-6 grid gap-5 md:grid-cols-[160px_minmax(0,1fr)] md:items-center sm:mt-8 sm:gap-6">
          <div className="mx-auto flex h-32 w-32 items-center justify-center rounded-full bg-[conic-gradient(#3b82f6_var(--progress),#e8eef7_0)] p-3 sm:h-36 sm:w-36" style={{ '--progress': `${Math.max(1, Math.min(100, props.taskProgress))}%` } as CSSProperties}>
            <div className="flex h-full w-full flex-col items-center justify-center rounded-full bg-white text-center shadow-inner dark:bg-slate-950">
              <span className="text-2xl font-bold text-primary-600 dark:text-primary-300">{progressLabel}</span>
              <span className="mt-1 text-xs text-slate-400">任务进度</span>
            </div>
          </div>

          <div className="space-y-5">
            <PreviewBar label="任务进度" value={`${Math.round(props.taskProgress)}%`} percent={props.taskProgress} color="bg-primary-500" />
            <PreviewBar label="结果片段" value={`${props.resultLineCount}条`} percent={linePercent} color="bg-cyan-500" />
            <PreviewBar label="资源产物" value={`${props.downloadCount}个`} percent={assetPercent} color="bg-violet-500" />
          </div>
        </div>
      ) : (
        <div className="mt-6 grid gap-5 md:grid-cols-[160px_minmax(0,1fr)] md:items-center sm:mt-8 sm:gap-6">
          <div className="mx-auto flex h-32 w-32 items-center justify-center rounded-full border border-dashed border-blue-200 bg-blue-50/60 text-center dark:border-slate-700 dark:bg-slate-950/40 sm:h-36 sm:w-36">
            <div>
              <div className="text-xl font-bold text-primary-600 dark:text-primary-300">待提交</div>
              <div className="mt-1 text-xs text-slate-400">暂无真实任务</div>
            </div>
          </div>
          <div className="rounded-2xl border border-dashed border-blue-100 bg-slate-50/70 px-4 py-5 text-sm leading-7 text-slate-500 dark:border-slate-700 dark:bg-slate-950/40 dark:text-slate-400 sm:px-5 sm:py-6">
            提交任务后，这里只显示后端任务返回的进度、结果片段和资源产物数量。
          </div>
        </div>
      )}

      <div className="mt-6 rounded-2xl border border-blue-100 bg-slate-50/60 p-4 dark:border-slate-800 dark:bg-slate-950/40 sm:mt-8">
        <div className="text-sm font-semibold text-slate-700 dark:text-slate-200">学习效果趋势预测</div>
        <div className="mt-3 flex min-h-24 items-center justify-center rounded-xl border border-dashed border-blue-100 bg-white/70 px-4 py-5 text-center text-sm leading-6 text-slate-500 dark:border-slate-700 dark:bg-slate-900/60 dark:text-slate-400 sm:h-28 sm:py-0">
          当前没有真实预测接口，已隐藏预测曲线和固定百分比。
        </div>
        <div className="mt-3 text-xs text-slate-400">
          当前服务：{props.selectedServiceLabel || '未选择'}{hasTask ? ` · ${props.taskStatus}` : ''}
        </div>
      </div>
    </section>
  );
}

function PreviewBar(props: {
  label: string;
  value: string;
  percent: number;
  color: string;
}) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-sm">
        <span className="font-medium text-slate-600 dark:text-slate-300">{props.label}</span>
        <span className="font-semibold text-slate-700 dark:text-slate-200">{props.value}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
        <div className={`h-full rounded-full ${props.color} transition-[width] duration-300`} style={{ width: `${Math.max(0, Math.min(100, props.percent))}%` }} />
      </div>
    </div>
  );
}

export function AssistantActionBar(props: {
  selectedServiceLabel: string;
  disabled: boolean;
  canStop: boolean;
  busy: boolean;
  status: string;
  onSubmit: () => void;
  onStop: () => void;
}) {
  return (
    <section className="rounded-[24px] border border-blue-100/80 bg-white/90 p-4 shadow-sm shadow-blue-100/50 dark:border-slate-800 dark:bg-slate-900/80 sm:p-5">
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(260px,0.75fr)_180px] lg:items-center">
        <div className="flex items-start gap-3 sm:items-center sm:gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-blue-50 ring-1 ring-blue-100 sm:h-14 sm:w-14">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-gradient-to-br from-blue-500 to-cyan-400 text-white shadow-md shadow-blue-300/50 sm:h-10 sm:w-10">
              <BrainCircuit className="h-5 w-5" />
            </div>
          </div>
          <div className="min-w-0">
            <div className="text-base font-semibold text-slate-900 dark:text-white">智学助手</div>
            <p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">
              {props.busy ? props.status : props.selectedServiceLabel ? `已选择 ${props.selectedServiceLabel}，提交后开始执行真实服务任务。` : '请选择一项服务后提交任务。'}
            </p>
          </div>
        </div>

        <button
          type="button"
          onClick={props.onSubmit}
          disabled={props.disabled}
          className="inline-flex h-14 items-center justify-center gap-3 rounded-2xl bg-gradient-to-r from-blue-600 to-primary-500 px-5 text-base font-semibold text-white shadow-lg shadow-blue-500/24 transition-all hover:-translate-y-0.5 hover:shadow-xl hover:shadow-blue-500/28 disabled:cursor-not-allowed disabled:opacity-45 disabled:shadow-none disabled:hover:translate-y-0 sm:h-16 sm:px-6 sm:text-lg"
        >
          <Send className="h-6 w-6" />
          {props.busy ? '提交中...' : '提交任务'}
        </button>

        <button
          type="button"
          onClick={props.onStop}
          disabled={!props.canStop}
          className="inline-flex h-12 items-center justify-center gap-2 rounded-2xl border border-blue-100 bg-white px-5 text-sm font-semibold text-slate-600 shadow-sm shadow-blue-100/60 transition-all hover:border-primary-200 hover:text-primary-600 disabled:cursor-not-allowed disabled:opacity-45 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-300 sm:h-14"
        >
          <Square className="h-4 w-4" />
          停止任务
        </button>
      </div>
    </section>
  );
}
