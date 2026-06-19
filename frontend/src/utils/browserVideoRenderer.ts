export interface BrowserVideoRenderRequest {
  taskId?: string;
  audioBase64: string;
  title?: string;
  durationSeconds?: number;
  knowledgePoint?: string;
  style?: string;
}

export interface BrowserVideoRenderResult {
  videoUrl: string;
  thumbnailUrl?: string;
  duration?: number;
  mimeType?: string;
  fileName?: string;
}

interface RendererReadyMessage {
  type: 'dh_live_renderer:ready';
}

interface RendererProgressMessage {
  type: 'dh_live_renderer:progress';
  requestId: string;
  percent: number;
  message?: string;
}

interface RendererCompleteMessage {
  type: 'dh_live_renderer:complete';
  requestId: string;
  videoBlob: Blob;
  thumbnailBlob?: Blob;
  mimeType?: string;
  fileName?: string;
}

interface RendererErrorMessage {
  type: 'dh_live_renderer:error';
  requestId: string;
  message: string;
}

type RendererMessage =
  | RendererReadyMessage
  | RendererProgressMessage
  | RendererCompleteMessage
  | RendererErrorMessage;

interface PendingRenderJob {
  taskId: string;
  requestId: string;
  resolve: (result: BrowserVideoRenderResult) => void;
  reject: (error: Error) => void;
  onProgress?: (percent: number, message?: string) => void;
  videoObjectUrl?: string;
  thumbnailObjectUrl?: string;
}

export interface BrowserVideoRenderTaskState {
  taskId: string;
  requestId: string;
  status: 'rendering' | 'completed' | 'failed';
  percent: number;
  message?: string;
  result?: BrowserVideoRenderResult;
  error?: string;
}

let rendererIframePromise: Promise<HTMLIFrameElement> | null = null;
let rendererReadyResolver: (() => void) | null = null;
let rendererReadyRejecter: ((error: Error) => void) | null = null;
let rendererReadyPromise: Promise<void> | null = null;
let activeJob: PendingRenderJob | null = null;
let rendererMessageBound = false;
const renderTaskStates = new Map<string, BrowserVideoRenderTaskState>();
const renderTaskListeners = new Map<string, Set<(state: BrowserVideoRenderTaskState) => void>>();
const renderTaskTouchedAt = new Map<string, number>();
const TERMINAL_TASK_TTL_MS = 10 * 60 * 1000;

function revokeResultObjectUrls(result?: BrowserVideoRenderResult): void {
  if (!result) {
    return;
  }
  if (result.videoUrl.startsWith('blob:')) {
    URL.revokeObjectURL(result.videoUrl);
  }
  if (result.thumbnailUrl?.startsWith('blob:')) {
    URL.revokeObjectURL(result.thumbnailUrl);
  }
}

function pruneExpiredTaskStates(now = Date.now()): void {
  renderTaskStates.forEach((state, taskId) => {
    const isTerminal = state.status === 'completed' || state.status === 'failed';
    if (!isTerminal) {
      return;
    }
    const touchedAt = renderTaskTouchedAt.get(taskId) ?? now;
    if (now - touchedAt < TERMINAL_TASK_TTL_MS) {
      return;
    }
    revokeResultObjectUrls(state.result);
    renderTaskStates.delete(taskId);
    renderTaskTouchedAt.delete(taskId);
    renderTaskListeners.delete(taskId);
  });
}

function publishTaskState(taskId: string): void {
  const snapshot = renderTaskStates.get(taskId);
  if (!snapshot) {
    return;
  }
  const listeners = renderTaskListeners.get(taskId);
  if (!listeners || listeners.size === 0) {
    return;
  }
  const immutableSnapshot = { ...snapshot };
  listeners.forEach((listener) => listener(immutableSnapshot));
}

function updateTaskState(taskId: string, nextState: BrowserVideoRenderTaskState): void {
  pruneExpiredTaskStates();
  renderTaskStates.set(taskId, nextState);
  renderTaskTouchedAt.set(taskId, Date.now());
  publishTaskState(taskId);
}

function getBrowserVideoRenderTaskState(taskId: string): BrowserVideoRenderTaskState | null {
  pruneExpiredTaskStates();
  const snapshot = renderTaskStates.get(taskId);
  return snapshot ? { ...snapshot } : null;
}

function subscribeBrowserVideoRenderTask(
  taskId: string,
  listener: (state: BrowserVideoRenderTaskState) => void,
): () => void {
  pruneExpiredTaskStates();
  const listeners = renderTaskListeners.get(taskId) ?? new Set<(state: BrowserVideoRenderTaskState) => void>();
  listeners.add(listener);
  renderTaskListeners.set(taskId, listeners);
  const snapshot = renderTaskStates.get(taskId);
  if (snapshot) {
    listener({ ...snapshot });
  }
  return () => {
    const current = renderTaskListeners.get(taskId);
    if (!current) {
      return;
    }
    current.delete(listener);
    if (current.size === 0) {
      renderTaskListeners.delete(taskId);
    }
  };
}

function waitForTaskResult(taskId: string, onProgress?: (percent: number, message?: string) => void): Promise<BrowserVideoRenderResult> {
  const existing = getBrowserVideoRenderTaskState(taskId);
  if (existing?.status === 'completed' && existing.result) {
    return Promise.resolve(existing.result);
  }
  if (existing?.status === 'failed') {
    return Promise.reject(new Error(existing.error || '视频生成失败'));
  }
  return new Promise<BrowserVideoRenderResult>((resolve, reject) => {
    const unsubscribe = subscribeBrowserVideoRenderTask(taskId, (state) => {
      if (state.status === 'rendering') {
        onProgress?.(state.percent, state.message);
        return;
      }
      unsubscribe();
      if (state.status === 'completed' && state.result) {
        resolve(state.result);
        return;
      }
      reject(new Error(state.error || '视频生成失败'));
    });
  });
}

function ensureMessageListener(): void {
  if (rendererMessageBound || typeof window === 'undefined') {
    return;
  }
  window.addEventListener('message', handleRendererMessage);
  rendererMessageBound = true;
}

function handleRendererMessage(event: MessageEvent<RendererMessage>): void {
  if (event.origin !== window.location.origin) {
    return;
  }
  if (!event.data || typeof event.data !== 'object') {
    return;
  }
  const message = event.data;
  if (!message.type?.startsWith('dh_live_renderer:')) {
    return;
  }
  if (message.type === 'dh_live_renderer:ready') {
    rendererReadyResolver?.();
    rendererReadyResolver = null;
    rendererReadyRejecter = null;
    return;
  }
  if (message.type === 'dh_live_renderer:error' && message.requestId === '') {
    const error = new Error(message.message || '视频生成初始化失败');
    rendererReadyRejecter?.(error);
    rendererReadyResolver = null;
    rendererReadyRejecter = null;
    rendererReadyPromise = null;
    rendererIframePromise = null;
    return;
  }
  if (!activeJob || ('requestId' in message && message.requestId !== activeJob.requestId)) {
    return;
  }
  if (message.type === 'dh_live_renderer:progress') {
    updateTaskState(activeJob.taskId, {
      taskId: activeJob.taskId,
      requestId: activeJob.requestId,
      status: 'rendering',
      percent: message.percent,
      message: message.message,
    });
    activeJob.onProgress?.(message.percent, message.message);
    return;
  }
  if (message.type === 'dh_live_renderer:error') {
    const failedJob = activeJob;
    activeJob = null;
    updateTaskState(failedJob.taskId, {
      taskId: failedJob.taskId,
      requestId: failedJob.requestId,
      status: 'failed',
      percent: 0,
      error: message.message || '视频生成失败',
      message: message.message || '视频生成失败',
    });
    failedJob.reject(new Error(message.message || '视频生成失败'));
    return;
  }
  if (message.type === 'dh_live_renderer:complete') {
    const completedJob = activeJob;
    activeJob = null;
    const videoObjectUrl = URL.createObjectURL(message.videoBlob);
    const thumbnailObjectUrl = message.thumbnailBlob ? URL.createObjectURL(message.thumbnailBlob) : undefined;
    completedJob.videoObjectUrl = videoObjectUrl;
    completedJob.thumbnailObjectUrl = thumbnailObjectUrl;
    const result: BrowserVideoRenderResult = {
      videoUrl: videoObjectUrl,
      thumbnailUrl: thumbnailObjectUrl,
      mimeType: message.mimeType || message.videoBlob.type || 'video/webm',
      fileName: message.fileName || inferFileName(message.mimeType || message.videoBlob.type),
    };
    updateTaskState(completedJob.taskId, {
      taskId: completedJob.taskId,
      requestId: completedJob.requestId,
      status: 'completed',
      percent: 100,
      message: '视频生成完成',
      result,
    });
    completedJob.resolve(result);
  }
}

async function ensureRendererIframe(): Promise<HTMLIFrameElement> {
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    throw new Error('当前环境不支持视频生成');
  }
  ensureMessageListener();
  if (!rendererIframePromise) {
    rendererReadyPromise = new Promise<void>((resolve, reject) => {
      rendererReadyResolver = resolve;
      rendererReadyRejecter = reject;
      window.setTimeout(() => {
        if (!rendererReadyRejecter) {
          return;
        }
        const error = new Error('视频生成启动超时，请稍后重试');
        rendererReadyRejecter(error);
        rendererReadyResolver = null;
        rendererReadyRejecter = null;
      }, 90000);
    });
    rendererIframePromise = Promise.resolve().then(() => {
      const iframe = document.createElement('iframe');
      iframe.src = '/dh_live/renderer.html';
      iframe.title = '视频生成器';
      iframe.setAttribute('aria-hidden', 'true');
      iframe.style.position = 'fixed';
      iframe.style.width = '1px';
      iframe.style.height = '1px';
      iframe.style.opacity = '0';
      iframe.style.pointerEvents = 'none';
      iframe.style.border = '0';
      iframe.style.left = '-9999px';
      iframe.style.top = '-9999px';
      document.body.appendChild(iframe);
      return iframe;
    });
  }
  const iframe = await rendererIframePromise;
  await rendererReadyPromise;
  return iframe;
}

function inferFileName(mimeType?: string): string {
  const extension = (mimeType || '').includes('mp4') ? 'mp4' : 'webm';
  return `browser-rendered-video.${extension}`;
}

function createRequestId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return `render-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export async function renderTalkingVideoInBrowser(
  request: BrowserVideoRenderRequest,
  options?: {
    onProgress?: (percent: number, message?: string) => void;
  },
): Promise<BrowserVideoRenderResult> {
  if (!request.audioBase64) {
    throw new Error('缺少视频生成所需的音频数据');
  }
  const taskId = request.taskId || createRequestId();
  const existing = getBrowserVideoRenderTaskState(taskId);
  if (existing?.status === 'completed' && existing.result) {
    return existing.result;
  }
  if (existing?.status === 'rendering') {
    return waitForTaskResult(taskId, options?.onProgress);
  }
  cancelActiveBrowserVideoRender('已取消上一条视频生成任务');
  const requestId = createRequestId();
  const iframe = await ensureRendererIframe();
  updateTaskState(taskId, {
    taskId,
    requestId,
    status: 'rendering',
    percent: 1,
    message: '等待视频生成启动',
  });
  return new Promise<BrowserVideoRenderResult>((resolve, reject) => {
    activeJob = {
      taskId,
      requestId,
      resolve,
      reject,
      onProgress: options?.onProgress,
    };
    iframe.contentWindow?.postMessage(
      {
        type: 'dh_live_renderer:render',
        requestId,
        payload: request,
      },
      window.location.origin,
    );
  });
}

function cancelActiveBrowserVideoRender(message = '视频生成已取消'): void {
  if (!activeJob) {
    return;
  }
  const job = activeJob;
  activeJob = null;
  updateTaskState(job.taskId, {
    taskId: job.taskId,
    requestId: job.requestId,
    status: 'failed',
    percent: 0,
    error: message,
    message,
  });
  if (job.videoObjectUrl) {
    URL.revokeObjectURL(job.videoObjectUrl);
  }
  if (job.thumbnailObjectUrl) {
    URL.revokeObjectURL(job.thumbnailObjectUrl);
  }
  rendererIframePromise?.then((iframe) => {
    iframe.contentWindow?.postMessage(
      {
        type: 'dh_live_renderer:cancel',
        requestId: job.requestId,
      },
      window.location.origin,
    );
  }).catch(() => undefined);
  job.reject(new Error(message));
}
