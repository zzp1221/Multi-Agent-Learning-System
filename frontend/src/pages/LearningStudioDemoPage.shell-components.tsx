import type { ReactNode } from 'react';
import { BrainCircuit, Send, Square, TrendingUp } from 'lucide-react';

export function EngineSectionHeader(props: {
  icon: ReactNode;
  title: string;
  subtitle: string;
}) {
  return (
    <div className="flex items-start gap-3">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-blue-50/80 text-primary-600 dark:bg-primary-500/10 dark:text-primary-300">
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
      <div className="absolute left-1/2 top-[61%] h-20 w-[330px] -translate-x-1/2 rounded-[50%] bg-blue-100/42 shadow-[0_24px_56px_rgba(61,116,239,0.2)]" />
      <div className="absolute left-1/2 top-[58%] h-12 w-[260px] -translate-x-1/2 rounded-[50%] bg-white/48 shadow-[0_18px_42px_rgba(14,165,233,0.15)]" />
      <div className="absolute left-1/2 top-[45%] h-48 w-48 -translate-x-1/2 -translate-y-1/2 rounded-[34px] bg-white/35 shadow-[0_36px_90px_rgba(64,111,214,0.18)] backdrop-blur-md" />
      <div className="absolute left-1/2 top-[42%] flex h-[136px] w-[136px] -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-[30px] bg-gradient-to-br from-blue-400 via-blue-600 to-cyan-400 text-5xl font-black text-white shadow-[0_28px_70px_rgba(37,99,235,0.36)]">
        AI
      </div>
      <div className="absolute left-[17%] top-[30%] h-3 w-3 rounded-full bg-cyan-300 shadow-[0_0_22px_rgba(34,211,238,0.9)]" />
      <div className="absolute right-[20%] top-[34%] h-3 w-3 rounded-full bg-blue-400 shadow-[0_0_22px_rgba(59,130,246,0.75)]" />
      <div className="absolute left-[34%] top-[18%] h-2 w-2 rounded-full bg-emerald-300 shadow-[0_0_18px_rgba(52,211,153,0.75)]" />
      <div className="absolute right-[23%] bottom-[25%] h-2.5 w-2.5 rounded-full bg-cyan-300 shadow-[0_0_18px_rgba(34,211,238,0.8)]" />
      <div className="absolute left-[19%] top-[31%] h-px w-[62%] rotate-[28deg] bg-gradient-to-r from-transparent via-blue-300/80 to-transparent" />
      <div className="absolute left-[23%] top-[57%] h-px w-[58%] -rotate-[20deg] bg-gradient-to-r from-transparent via-cyan-300/70 to-transparent" />
      <div className="absolute left-[25%] top-[22%] h-64 w-64 rounded-full bg-blue-100/10" />
      <div className="absolute left-[22%] top-[28%] h-52 w-72 rotate-12 rounded-[50%] bg-cyan-100/10" />
      <span className="absolute right-9 top-20 rounded-xl bg-blue-50/90 px-3 py-1.5 text-sm font-semibold text-primary-600 shadow-sm shadow-blue-100/80">
        智能分析
      </span>
      <span className="absolute left-14 top-36 rounded-xl bg-cyan-50/90 px-3 py-1.5 text-sm font-semibold text-cyan-600 shadow-sm shadow-cyan-100/80">
        精准推荐
      </span>
      <span className="absolute bottom-28 right-5 rounded-xl bg-emerald-50/90 px-3 py-1.5 text-sm font-semibold text-emerald-600 shadow-sm shadow-emerald-100/80">
        学习进化
      </span>
    </div>
  );
}

export function TaskStatusPreview(props: {
  phaseIcon?: ReactNode;
  selectedServiceLabel: string;
  taskId: string;
  taskProgress: number;
  taskStatus: string;
}) {
  const hasTask = Boolean(props.taskId);
  const normalizedProgress = hasTask ? Math.max(6, Math.min(100, props.taskProgress)) : 0;
  const progressLabel = hasTask ? `${Math.round(props.taskProgress)}%` : '未开始';
  const statusLabel = hasTask ? props.taskStatus || '生成中' : '选择服务后开始';

  return (
    <section className="h-full bg-white/30 p-4 dark:bg-slate-950/12 sm:p-6">
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3 sm:items-center">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-blue-50/80 text-primary-600 dark:bg-primary-500/10 dark:text-primary-300">
            {props.phaseIcon ?? <TrendingUp className="h-4 w-4" />}
          </div>
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-slate-900 dark:text-white">学习动作进展</h2>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
              {props.selectedServiceLabel || '先选择一个学习环节'}
            </p>
          </div>
        </div>
        <span className="shrink-0 rounded-full bg-white/72 px-3 py-1 text-xs font-semibold text-slate-500 shadow-sm shadow-blue-100/35 dark:bg-slate-900/70 dark:text-slate-300">
          {statusLabel}
        </span>
      </div>

      <div className="mt-8">
        <div className="mb-3 flex items-center justify-between text-sm">
          <span className="font-medium text-slate-600 dark:text-slate-300">{hasTask ? statusLabel : '等待提交'}</span>
          <span className="font-semibold text-primary-600 dark:text-primary-300">{progressLabel}</span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-slate-200/70 dark:bg-slate-800">
          <div className="h-full rounded-full bg-primary-500 transition-[width] duration-300" style={{ width: `${normalizedProgress}%` }} />
        </div>
      </div>

      <div className="mt-6 grid grid-cols-3 gap-3 text-xs text-slate-500 dark:text-slate-400">
        <div className="rounded-2xl bg-white/45 px-3 py-3 dark:bg-slate-900/34">
          <div className="font-semibold text-slate-700 dark:text-slate-200">定位</div>
          <div className="mt-1">{props.selectedServiceLabel || '待选择'}</div>
        </div>
        <div className="rounded-2xl bg-white/45 px-3 py-3 dark:bg-slate-900/34">
          <div className="font-semibold text-slate-700 dark:text-slate-200">执行</div>
          <div className="mt-1">{hasTask ? '进行中' : '待启动'}</div>
        </div>
        <div className="rounded-2xl bg-white/45 px-3 py-3 dark:bg-slate-900/34">
          <div className="font-semibold text-slate-700 dark:text-slate-200">承接</div>
          <div className="mt-1">{props.taskProgress >= 100 ? '已就绪' : '自动刷新'}</div>
        </div>
      </div>

      <p className="mt-6 text-sm leading-7 text-slate-500 dark:text-slate-400">
        提交后保持在当前页面即可实时查看进度，完成结果会继续进入路径、资源或复盘环节。
      </p>
    </section>
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
    <section className="rounded-[24px] bg-white/78 p-4 shadow-[0_14px_36px_rgba(43,83,145,0.08)] backdrop-blur dark:bg-slate-900/72 dark:shadow-slate-950/20 sm:p-5">
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(240px,0.68fr)_168px] lg:items-center">
        <div className="flex items-start gap-3 sm:items-center sm:gap-4">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-primary-50 text-primary-600 dark:bg-primary-500/10 dark:text-primary-300 sm:h-12 sm:w-12">
            <BrainCircuit className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <div className="text-base font-semibold text-slate-900 dark:text-white">
              {props.busy ? '正在生成' : props.selectedServiceLabel || '选择服务'}
            </div>
            <p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">
              {props.busy ? props.status : props.selectedServiceLabel ? '参数已就绪，可直接提交。' : '选择一项服务后继续。'}
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
          {props.busy ? '正在启动...' : '开始生成'}
        </button>

        <button
          type="button"
          onClick={props.onStop}
          disabled={!props.canStop}
          className="inline-flex h-12 items-center justify-center gap-2 rounded-2xl bg-white px-5 text-sm font-semibold text-slate-600 shadow-sm shadow-blue-100/35 transition-all hover:bg-blue-50/60 hover:text-primary-600 disabled:cursor-not-allowed disabled:opacity-45 dark:bg-slate-950 dark:text-slate-300 dark:hover:bg-slate-900 sm:h-14"
        >
          <Square className="h-4 w-4" />
          停止生成
        </button>
      </div>
    </section>
  );
}
