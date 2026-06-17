import { AuthSessionExpiredError, notifyAuthSessionExpired } from './request';

interface RawSseEvent {
  event: string;
  data: string;
}

export interface StreamEventEnvelope<TPayload extends Record<string, unknown> = Record<string, unknown>> {
  event?: string;
  eventType?: string;
  seq?: number;
  sequence?: number;
  timestamp?: string;
  occurredAt?: string;
  payload?: TPayload;
}

export interface ConversationDialogState {
  conversationId: string;
  turnId: string;
  pedagogyStrategy?: string;
  nextAction?: string;
}

export interface ConversationStreamEventEnvelope extends StreamEventEnvelope {
  dialogState?: ConversationDialogState;
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
        if (response.status === 401) {
          notifyAuthSessionExpired('unauthorized');
          throw new AuthSessionExpiredError();
        }
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
      if (error instanceof AuthSessionExpiredError) {
        options.onError(error);
        return;
      }
      lastError = error instanceof Error ? error : new Error('实时连接执行失败');

      if (attempt < maxRetries) {
        continue;
      }
    }
  }

  options.onError(lastError ?? new Error('实时连接执行失败，多次重试后仍未恢复'));
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

export function parseStreamEnvelope<TEnvelope extends StreamEventEnvelope = StreamEventEnvelope>(
  rawData: string,
  fallbackPayloadKey: 'text' | 'message' = 'text',
): TEnvelope {
  try {
    const parsed = JSON.parse(rawData) as TEnvelope;
    return {
      ...parsed,
      eventType: parsed.eventType ?? parsed.event,
      sequence: parsed.sequence ?? parsed.seq,
      occurredAt: parsed.occurredAt ?? parsed.timestamp,
    };
  } catch {
    return {
      payload: {
        [fallbackPayloadKey]: rawData,
      },
    } as TEnvelope;
  }
}

export function readStreamPayload(rawData: string): Record<string, unknown> | undefined {
  return parseStreamEnvelope(rawData, 'message').payload;
}

export function readStreamMessage(payload: Record<string, unknown> | undefined): string {
  if (!payload) {
    return '';
  }
  return readString(payload.message) || readString(payload.text) || readString(payload.summary);
}

export function readConversationChunk(data: ConversationStreamEventEnvelope, eventName: string): string {
  const payload = data.payload;
  if (!payload) {
    return '';
  }

  const stage = readString(payload.stage);
  if (shouldSuppressConversationEvent(eventName, payload)) {
    return '';
  }
  if (eventName === 'result_chunk') {
    if (stage && stage !== 'tutoring') {
      return '';
    }
    if (shouldSuppressConversationPayload(payload)) {
      return '';
    }
    const chunkText = readString(payload.text);
    return chunkText && !looksLikeStructuredPayloadText(chunkText)
      ? sanitizeConversationLiveChunk(chunkText)
      : '';
  }

  return readString(payload.chunk)
    || readString(payload.delta)
    || readString(payload.message)
    || readString(payload.content);
}

function shouldSuppressConversationEvent(eventName: string, payload: Record<string, unknown>): boolean {
  if (
    eventName === 'progress'
    || eventName === 'resource_file'
    || eventName === 'question_batch'
    || eventName === 'judge_result'
    || eventName === 'done'
    || eventName === 'error'
    || eventName.startsWith('video_gen:')
  ) {
    return true;
  }
  return shouldSuppressConversationPayload(payload);
}

function shouldSuppressConversationPayload(payload: Record<string, unknown>): boolean {
  const stage = readString(payload.stage).toLowerCase();
  const serviceType = readString(payload.serviceType).toUpperCase();
  const assetType = readString(payload.assetType).toUpperCase();
  const displayMode = readString(payload.displayMode).toUpperCase();
  const hasDownloadUrl = readString(payload.downloadUrl) || readString(payload.resourceUrl) || readString(payload.url);
  return [
    'query_rewrite',
    'retrieving',
    'retrieval',
    'critic',
    'resource',
    'resource_generation',
    'practice',
    'judge',
    'video',
    'tool',
  ].some((item) => stage.includes(item))
    || serviceType === 'RESOURCE_GENERATION'
    || assetType === 'SLIDES'
    || assetType === 'VIDEO'
    || displayMode === 'DOWNLOAD_CARD'
    || Boolean(hasDownloadUrl)
    || Boolean(payload.traceId)
    || Boolean(payload.taskId)
    || Boolean(payload.agentTrace)
    || Boolean(payload.agentName && stage !== 'tutoring')
    || Boolean(payload.toolName)
    || Boolean(payload.toolCall)
    || Boolean(payload.resourceFailures)
    || Boolean(payload.artifactType)
    || Boolean(payload.inlineContent)
    || Array.isArray(payload.questions)
    || typeof payload.practiceQuestionBatch === 'object';
}

function looksLikeStructuredPayloadText(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed) {
    return false;
  }
  if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
    return true;
  }
  return trimmed.includes('"assetType"')
    || trimmed.includes('"eventType"')
    || trimmed.includes('"payload"')
    || trimmed.includes('"agentName"')
    || trimmed.includes('"traceId"')
    || trimmed.includes('"taskId"')
    || trimmed.includes('"toolName"')
    || trimmed.includes('"downloadUrl"')
    || trimmed.includes('"questions"')
    || trimmed.includes('"inlineContent"')
    || trimmed.includes('"practiceQuestionBatch"')
    || trimmed.includes('"resourcePushPlan"')
    || trimmed.includes('"learningPath"');
}

function sanitizeConversationLiveChunk(text: string): string {
  const normalized = text.replace(/\r\n/g, '\n');
  if (normalized.trim().startsWith('```json')) {
    return '';
  }
  return normalized;
}

function readString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}
