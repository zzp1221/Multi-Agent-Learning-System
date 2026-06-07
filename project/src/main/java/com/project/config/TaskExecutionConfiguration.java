package com.project.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.task.SimpleAsyncTaskExecutor;
import org.springframework.core.task.TaskExecutor;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * 基于虚拟线程的任务执行器，用于智学引擎和对话任务调度。
 *
 * <p>每个任务运行在虚拟线程上，阻塞 I/O（Python SSE、数据库写入）
 * 不会占用平台线程。并发上限保护 Python Agent 和 PostgreSQL
 * 连接池免受无限制扇出的影响。</p>
 */
@Configuration
@EnableAsync
@EnableScheduling
public class TaskExecutionConfiguration {

    @Bean
    public TaskExecutor conversationTaskExecutor() {
        // 为辅导流预留独立容量，避免排在耗时较长的智学引擎任务之后。
        return buildExecutor("conversation-", 8);
    }

    @Bean
    public TaskExecutor voiceTaskExecutor() {
        return buildExecutor("voice-", 8);
    }

    @Bean
    public TaskExecutor noteIndexTaskExecutor() {
        return buildExecutor("note-index-", 2);
    }

    @Bean
    public TaskExecutor resourceSemanticWarmupTaskExecutor() {
        return buildExecutor("resource-warmup-", 2);
    }

    private TaskExecutor buildExecutor(String threadNamePrefix, int concurrencyLimit) {
        SimpleAsyncTaskExecutor executor = new SimpleAsyncTaskExecutor();
        executor.setVirtualThreads(true);
        executor.setConcurrencyLimit(concurrencyLimit);
        executor.setThreadNamePrefix(threadNamePrefix);
        return executor;
    }
}
