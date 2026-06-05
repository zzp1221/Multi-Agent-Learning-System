package com.project.domain.task;

import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Collection;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

/**
 * 任务生命周期持久化仓库。
 */
public interface SmartEngineTaskRepository extends JpaRepository<SmartEngineTask, UUID> {

    Optional<SmartEngineTask> findByIdAndUserId(UUID id, UUID userId);

    Optional<SmartEngineTask> findFirstByUserIdAndServiceTypeAndTaskStatusInOrderByCreatedAtDesc(
        UUID userId,
        ServiceType serviceType,
        Collection<TaskStatus> taskStatuses
    );

    Optional<SmartEngineTask> findFirstByUserIdAndServiceTypeOrderByCreatedAtDesc(UUID userId, ServiceType serviceType);

    List<SmartEngineTask> findTop5ByUserIdAndServiceTypeOrderByCreatedAtDesc(UUID userId, ServiceType serviceType);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT t FROM SmartEngineTask t WHERE t.id = :id")
    Optional<SmartEngineTask> findWithLockById(@Param("id") UUID id);
}
