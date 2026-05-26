interface RawSseEvent {
  event: string;
  data: string;
}

interface StreamSseOptions {
  init: RequestInit;
  missingBodyMessage: string;
  requestFailedMessage: (status: number) => string;
  onOpen?: () => void;
  onEvent: (event: RawSseEvent) => boolean | void;
  onDone: () => void;
  onError: (error: Error) => void;
  onRetry?: (attempt: number, maxRetries: number) => void;
  defaultEvent?: string;
  maxRetries?: number;
}

const RETRYABLE_STATUSES = new Set([429, 502, 503, 504]);

export async function streamSse(url: string, options: StreamSseOptions): Promise<void> {
  const maxRetries = options.maxRetries ?? 0;
  let lastError: Error | null = null;

  for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
    if (attempt > 0) {
      const delay = Math.min(1000 * Math.pow(2, attempt - 1), 8000);
      options.onRetry?.(attempt, maxRetries);
      await new Promise((resolve) => setTimeout(resolve, delay));
    }

    let doneCalled = false;
    const safeDone = () => {
      if (!doneCalled) {
        doneCalled = true;
        options.onDone();
      }
    };

    try {
      const response = await fetch(url, options.init);
      if (!response.ok) {
        const statusError = new Error(options.requestFailedMessage(response.status));
        if (RETRYABLE_STATUSES.has(response.status) && attempt < maxRetries) {
          lastError = statusError;
          continue;
        }
        throw statusError;
      }
      options.onOpen?.();

      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error(options.missingBodyMessage);
      }

      let shouldCancelReader = true;
      try {
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) {
            shouldCancelReader = false;
            buffer += decoder.decode();
            const trailingEvent = parseSseEventBlock(buffer, options.defaultEvent);
            if (trailingEvent && options.onEvent(trailingEvent)) {
              return;
            }
            break;
          }

          buffer += decoder.decode(value, { stream: true });
          const eventBlocks = buffer.split(/\r?\n\r?\n/);
          buffer = eventBlocks.pop() ?? '';

          for (const block of eventBlocks) {
            const parsed = parseSseEventBlock(block, options.defaultEvent);
            if (!parsed) {
              continue;
            }
            if (options.onEvent(parsed)) {
              return;
            }
          }
        }
      } finally {
        if (shouldCancelReader) {
          await reader.cancel().catch(() => undefined);
        }
        reader.releaseLock();
      }

      safeDone();
      return;
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return;
      }
      lastError = error instanceof Error ? error : new Error('SSE 流执行失败');

      if (attempt < maxRetries) {
        continue;
      }
    }
  }

  options.onError(lastError ?? new Error('SSE 流执行失败，已达最大重试次数'));
}

function parseSseEventBlock(block: string, defaultEvent = 'message'): RawSseEvent | null {
  if (!block.trim()) {
    return null;
  }

  const lines = block.split(/\r?\n/);
  let event = defaultEvent;
  const dataParts: string[] = [];
  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    if (line.startsWith('event:')) {
      event = line.slice(6).trim();
      continue;
    }
    if (line.startsWith('data:')) {
      dataParts.push(line.slice(5).trimStart());
    }
  }

  return {
    event,
    data: dataParts.join('\n'),
  };
}
