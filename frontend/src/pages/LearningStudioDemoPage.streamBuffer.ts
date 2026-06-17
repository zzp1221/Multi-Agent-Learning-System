import type { RunByApiTaskArgs } from './LearningStudioDemoPage.types';
export function scheduleStreamFlush(
  streamQueueRef: RunByApiTaskArgs['streamQueueRef'],
  streamFlushTimerRef: RunByApiTaskArgs['streamFlushTimerRef'],
  streamRafRef: RunByApiTaskArgs['streamRafRef'],
  setServiceResultLines: RunByApiTaskArgs['setServiceResultLines'],
): void {
  if (streamFlushTimerRef.current != null) {
    return;
  }

  streamFlushTimerRef.current = window.setTimeout(() => {
    streamFlushTimerRef.current = null;
    if (streamRafRef.current != null) {
      return;
    }
    streamRafRef.current = window.requestAnimationFrame(() => {
      streamRafRef.current = null;
      flushStreamQueue(streamQueueRef, streamFlushTimerRef, streamRafRef, setServiceResultLines);
    });
  }, 60);
}

export function flushStreamQueue(
  streamQueueRef: RunByApiTaskArgs['streamQueueRef'],
  streamFlushTimerRef: RunByApiTaskArgs['streamFlushTimerRef'],
  streamRafRef: RunByApiTaskArgs['streamRafRef'],
  setServiceResultLines: RunByApiTaskArgs['setServiceResultLines'],
): void {
  cleanupStreamSchedulers(streamFlushTimerRef, streamRafRef);
  if (streamQueueRef.current.length === 0) {
    return;
  }
  const chunks = [...streamQueueRef.current];
  streamQueueRef.current = [];
  setServiceResultLines((prev) => [...prev, ...chunks]);
}

export function cleanupStreamSchedulers(
  streamFlushTimerRef: RunByApiTaskArgs['streamFlushTimerRef'],
  streamRafRef: RunByApiTaskArgs['streamRafRef'],
): void {
  if (streamFlushTimerRef.current != null) {
    window.clearTimeout(streamFlushTimerRef.current);
    streamFlushTimerRef.current = null;
  }
  if (streamRafRef.current != null) {
    window.cancelAnimationFrame(streamRafRef.current);
    streamRafRef.current = null;
  }
}
