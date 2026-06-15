package com.project.application.smartengine;

import com.project.application.streaming.SseStreamSender;
import com.project.domain.task.SmartEngineTask;
import com.project.domain.task.SmartEngineTaskRepository;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 管理实时 SSE 订阅者，并为重连场景重放已持久化的事件。
 */
@Service
public class SseEmitterService {

    private static final long DEFAULT_TIMEOUT_MS = 0L;

    private final SmartEngineTaskEventCache taskEventCache;
    private final SmartEngineTaskRepository taskRepository;
    private final ConcurrentHashMap<UUID, SubscriberGroup> emitters = new ConcurrentHashMap<>();

    public SseEmitterService(
        SmartEngineTaskEventCache taskEventCache,
        SmartEngineTaskRepository taskRepository
    ) {
        this.taskEventCache = taskEventCache;
        this.taskRepository = taskRepository;
    }

    public SseEmitter subscribe(SmartEngineTask task) {
        SseEmitter emitter = new SseEmitter(DEFAULT_TIMEOUT_MS);
        Subscriber subscriber = new Subscriber(emitter);
        emitter.onCompletion(() -> removeEmitter(task.getId(), subscriber));
        emitter.onTimeout(() -> removeEmitter(task.getId(), subscriber));
        emitter.onError(ex -> removeEmitter(task.getId(), subscriber));

        SubscriberGroup group = emitters.computeIfAbsent(task.getId(), ignored -> new SubscriberGroup());
        synchronized (group) {
            if (!replayEvents(task, subscriber)) {
                if (group.subscribers.isEmpty()) {
                    emitters.remove(task.getId(), group);
                }
                return emitter;
            }
            SmartEngineTask latestTask = taskRepository.findById(task.getId()).orElse(task);

            if (!latestTask.isTerminal()) {
                group.subscribers.add(subscriber);
            } else {
                emitter.complete();
                if (group.subscribers.isEmpty()) {
                    emitters.remove(task.getId(), group);
                }
            }
        }

        return emitter;
    }

    public void publish(TaskStreamEventPayload payload, boolean terminal) {
        SubscriberGroup group = emitters.get(payload.taskId());
        if (group == null) {
            return;
        }

        synchronized (group) {
            if (group.subscribers.isEmpty()) {
                return;
            }
            for (Subscriber subscriber : group.subscribers) {
                try {
                    send(subscriber, payload);
                    if (terminal) {
                        subscriber.emitter.complete();
                    }
                } catch (IOException ex) {
                    subscriber.emitter.completeWithError(ex);
                    removeEmitter(payload.taskId(), subscriber);
                }
            }

            if (terminal) {
                emitters.remove(payload.taskId(), group);
            }
        }
    }

    private boolean replayEvents(SmartEngineTask task, Subscriber subscriber) {
        List<TaskStreamEventPayload> events = taskEventCache.replay(task.getId());
        for (TaskStreamEventPayload event : events) {
            try {
                send(subscriber, event);
            } catch (IOException ex) {
                subscriber.emitter.completeWithError(ex);
                return false;
            }
        }
        return true;
    }

    private void send(Subscriber subscriber, TaskStreamEventPayload payload) throws IOException {
        subscriber.sender.sendReplayable(
            payload.event(),
            payload.seq(),
            payload,
            seq -> seq > subscriber.lastSentSeq.get()
        );
        subscriber.lastSentSeq.set(Math.max(subscriber.lastSentSeq.get(), payload.seq()));
    }

    /**
     * 强制完成已取消任务的所有 emitter 并发布最终事件。
     */
    public void cancelTask(UUID taskId, TaskStreamEventPayload cancelPayload) {
        SubscriberGroup group = emitters.remove(taskId);
        if (group == null || group.subscribers.isEmpty()) {
            return;
        }
        synchronized (group) {
            for (Subscriber subscriber : group.subscribers) {
                try {
                    send(subscriber, cancelPayload);
                    subscriber.emitter.complete();
                } catch (IOException ex) {
                    subscriber.emitter.completeWithError(ex);
                }
            }
        }
    }

    private void removeEmitter(UUID taskId, Subscriber subscriber) {
        SubscriberGroup group = emitters.get(taskId);
        if (group == null) {
            return;
        }
        synchronized (group) {
            group.subscribers.remove(subscriber);
            if (group.subscribers.isEmpty()) {
                emitters.remove(taskId, group);
            }
        }
    }

    private static final class SubscriberGroup {
        private final CopyOnWriteArrayList<Subscriber> subscribers = new CopyOnWriteArrayList<>();
    }

    private static final class Subscriber {
        private final SseEmitter emitter;
        private final AtomicInteger lastSentSeq = new AtomicInteger();
        private final SseStreamSender sender;

        private Subscriber(SseEmitter emitter) {
            this.emitter = emitter;
            this.sender = new SseStreamSender(emitter, lastSentSeq);
        }
    }
}
