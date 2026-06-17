import { beforeEach, describe, expect, it, vi } from 'vitest';
import { streamSse } from './sse';

const encoder = new TextEncoder();

describe('streamSse', () => {
  beforeEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('retries retryable status responses and then completes', async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response('', { status: 503 }))
      .mockResolvedValueOnce(sseResponse('event: done\ndata: {"payload":{}}\n\n'));
    const onRetry = vi.fn();
    const onDone = vi.fn();

    const request = streamSse('/stream', {
      init: {},
      missingBodyMessage: 'missing',
      requestFailedMessage: (status) => `failed ${status}`,
      maxRetries: 2,
      onRetry,
      onEvent: (event) => {
        if (event.event === 'done') {
          onDone();
          return true;
        }
        return false;
      },
      onDone,
      onError: vi.fn(),
    });
    await vi.runAllTimersAsync();
    await request;

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(onRetry).toHaveBeenCalledWith(1, 2);
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('does not retry unauthorized responses', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('', { status: 401 }));
    const onError = vi.fn();

    await streamSse('/stream', {
      init: {},
      missingBodyMessage: 'missing',
      requestFailedMessage: (status) => `failed ${status}`,
      maxRetries: 2,
      onEvent: vi.fn(),
      onDone: vi.fn(),
      onError,
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0]?.[0]).toBeInstanceOf(Error);
  });
});

function sseResponse(body: string): Response {
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(body));
        controller.close();
      },
    }),
    {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    },
  );
}
