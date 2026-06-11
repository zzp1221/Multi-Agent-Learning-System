import type { ReactNode } from 'react';
import { BookOpenCheck, BrainCircuit, FileText, RefreshCw, Route, Send, Square, TrendingUp, UserRoundSearch } from 'lucide-react';

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
  const loopNodes = [
    { label: '画像', icon: UserRoundSearch, className: 'left-[12%] top-[18%]' },
    { label: '路径', icon: Route, className: 'right-[12%] top-[20%]' },
    { label: '资源', icon: FileText, className: 'right-[8%] bottom-[26%]' },
    { label: '练习', icon: BookOpenCheck, className: 'left-[12%] bottom-[24%]' },
  ];

  return (
    <div className="relative hidden min-h-[420px] overflow-hidden lg:block" aria-hidden="true">
      <div className="absolute inset-8 rounded-[38px] border border-[color:var(--ring-soft)] bg-[radial-gradient(circle_at_50%_38%,color-mix(in_srgb,var(--accent-action)_13%,transparent),transparent_34%),linear-gradient(180deg,var(--surface-control-strong),var(--surface-control))]" />
      <div className="absolute left-1/2 top-1/2 h-64 w-64 -translate-x-1/2 -translate-y-1/2 rounded-full border border-dashed border-[color:var(--ring-soft)]" />
      <div className="absolute left-1/2 top-1/2 h-40 w-40 -translate-x-1/2 -translate-y-1/2 rounded-full border border-[color:var(--ring-hairline)] bg-[color:var(--surface-panel-strong)] shadow-[var(--shadow-soft)]" />
      <div className="absolute left-1/2 top-1/2 flex h-28 w-28 -translate-x-1/2 -translate-y-1/2 flex-col items-center justify-center rounded-[30px] bg-[color:var(--accent-action)] text-white shadow-[var(--shadow-button)] dark:text-[#08201d]">
        <BrainCircuit className="h-8 w-8" />
        <span className="mt-2 text-sm font-semibold">学习调度</span>
      </div>
      <div className="absolute left-[20%] top-[50%] h-px w-[60%] bg-[color:var(--ring-hairline)]" />
      <div className="absolute left-[50%] top-[18%] h-[64%] w-px bg-[color:var(--ring-hairline)]" />
      <div className="absolute left-[24%] top-[25%] h-px w-[52%] rotate-[31deg] bg-[color:var(--ring-hairline)]" />
      <div className="absolute left-[24%] top-[73%] h-px w-[52%] -rotate-[31deg] bg-[color:var(--ring-hairline)]" />
      {loopNodes.map((node) => (
        <div
          key={node.label}
          className={`absolute ${node.className} flex h-[88px] w-[108px] flex-col items-center justify-center rounded-[24px] border border-[color:var(--ring-soft)] bg-[color:var(--surface-panel-strong)] text-[color:var(--ink-body)] shadow-[var(--shadow-soft)]`}
        >
          <node.icon className="h-5 w-5 text-[color:var(--accent-action)]" />
          <span className="mt-2 text-sm font-semibold text-[color:var(--ink-strong)]">{node.label}</span>
        </div>
      ))}
      <div className="absolute bottom-12 left-1/2 flex -translate-x-1/2 items-center gap-2 rounded-full border border-[color:var(--ring-soft)] bg-[color:var(--surface-panel-strong)] px-4 py-2 text-sm font-semibold text-[color:var(--ink-body)] shadow-[var(--shadow-soft)]">
        <RefreshCw className="h-4 w-4 text-[color:var(--accent-action)]" />
        复盘后进入下一轮路径
      </div>
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
    <section className="assistant-action-bar rounded-[24px] p-4 sm:p-5">
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
          className="inline-flex h-14 items-center justify-center gap-3 rounded-2xl bg-[color:var(--accent-action)] px-5 text-base font-semibold text-white shadow-[var(--shadow-button)] transition-all hover:-translate-y-0.5 hover:bg-[color:var(--accent-action-hover)] disabled:cursor-not-allowed disabled:opacity-45 disabled:shadow-none disabled:hover:translate-y-0 dark:text-[#08201d] sm:h-16 sm:px-6 sm:text-lg"
        >
          <Send className="h-6 w-6" />
          {props.busy ? '正在启动...' : '开始生成'}
        </button>

        <button
          type="button"
          onClick={props.onStop}
          disabled={!props.canStop}
          className="inline-flex h-12 items-center justify-center gap-2 rounded-2xl border border-[color:var(--ring-hairline)] bg-[color:var(--surface-control)] px-5 text-sm font-semibold text-[color:var(--ink-body)] transition-all hover:bg-[color:var(--surface-control-strong)] hover:text-[color:var(--accent-action)] disabled:cursor-not-allowed disabled:opacity-45 sm:h-14"
        >
          <Square className="h-4 w-4" />
          停止生成
        </button>
      </div>
    </section>
  );
}
