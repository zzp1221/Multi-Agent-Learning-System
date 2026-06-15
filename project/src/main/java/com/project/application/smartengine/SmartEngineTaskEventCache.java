package com.project.application.smartengine;

import org.springframework.stereotype.Component;
import org.springframework.scheduling.annotation.Scheduled;

import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Runtime-only replay cache for SmartEngine SSE events.
 */
@Component
public class SmartEngineTaskEventCache {

    private static final long EVENT_TTL_MILLIS = Duration.ofHours(2).toMillis();
    private static final int MAX_EVENTS_PER_TASK = 500;

    private final ConcurrentHashMap<UUID, CachedTaskEvents> eventsByTask = new ConcurrentHashMap<>();

    public int nextSequence(UUID taskId) {
        CachedTaskEvents events = activeEvents(taskId);
        synchronized (events) {
            return events.lastSequence() + 1;
        }
    }

    public TaskStreamEventPayload find(UUID taskId, int sequence) {
        CachedTaskEvents events = activeEvents(taskId);
        synchronized (events) {
            return events.find(sequence);
        }
    }

    public TaskStreamEventPayload append(TaskStreamEventPayload payload) {
        CachedTaskEvents events = activeEvents(payload.taskId());
        synchronized (events) {
            return events.append(payload);
        }
    }

    public List<TaskStreamEventPayload> replay(UUID taskId) {
        CachedTaskEvents events = activeEvents(taskId);
        synchronized (events) {
            return events.replay();
        }
    }

    public void clear(UUID taskId) {
        eventsByTask.remove(taskId);
    }

    @Scheduled(fixedDelay = 600_000)
    public void evictExpired() {
        long now = System.currentTimeMillis();
        eventsByTask.entrySet().removeIf(entry -> entry.getValue().isExpired(now));
    }

    private CachedTaskEvents activeEvents(UUID taskId) {
        long now = System.currentTimeMillis();
        return eventsByTask.compute(taskId, (ignored, existing) -> {
            if (existing == null || existing.isExpired(now)) {
                return new CachedTaskEvents(now);
            }
            existing.touch(now);
            return existing;
        });
    }

    private static final class CachedTaskEvents {
        private final List<TaskStreamEventPayload> events = new ArrayList<>();
        private long lastAccessedAtMillis;

        private CachedTaskEvents(long now) {
            this.lastAccessedAtMillis = now;
        }

        private boolean isExpired(long now) {
            return now - lastAccessedAtMillis > EVENT_TTL_MILLIS;
        }

        private void touch(long now) {
            this.lastAccessedAtMillis = now;
        }

        private int lastSequence() {
            return events.isEmpty() ? 0 : events.get(events.size() - 1).seq();
        }

        private TaskStreamEventPayload find(int sequence) {
            for (TaskStreamEventPayload event : events) {
                if (event.seq() == sequence) {
                    return event;
                }
            }
            return null;
        }

        private TaskStreamEventPayload append(TaskStreamEventPayload payload) {
            TaskStreamEventPayload normalized = new TaskStreamEventPayload(
                payload.event(),
                payload.taskId(),
                payload.traceId(),
                payload.seq(),
                payload.timestamp() == null ? OffsetDateTime.now() : payload.timestamp(),
                payload.payload() == null ? Map.of() : new LinkedHashMap<>(payload.payload())
            );
            events.add(normalized);
            if (events.size() > MAX_EVENTS_PER_TASK) {
                events.remove(0);
            }
            return normalized;
        }

        private List<TaskStreamEventPayload> replay() {
            return List.copyOf(events);
        }
    }
}
