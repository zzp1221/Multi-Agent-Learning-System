import asyncio
import json
import time
from typing import Any

import pytest

from src.ai_modules.config import Settings
from src.ai_modules.generation import GenerationOutputInvalidError
import src.ai_modules.runtime.smart_engine_stream_worker as worker_module
from src.ai_modules.runtime.smart_engine_stream_worker import SmartEngineStreamWorker


class FakeRedis:
    def exists(self, key: str) -> bool:
        del key
        return False


class FakeLeaderRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expiry_seconds: dict[str, int] = {}
        self.eval_calls = 0

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.expiry_seconds[key] = ex
        return True

    async def eval(self, script: str, numkeys: int, key: str, token: str, *args: Any) -> int:
        return self.eval_sync(script, numkeys, key, token, *args)

    def eval_sync(self, script: str, numkeys: int, key: str, token: str, *args: Any) -> int:
        del script, numkeys
        self.eval_calls += 1
        if self.values.get(key) != token:
            return 0
        if args:
            self.expiry_seconds[key] = int(args[0])
            return 1
        del self.values[key]
        return 1


class FakeSyncLeaderRedis(FakeLeaderRedis):
    def eval(self, script: str, numkeys: int, key: str, token: str, *args: Any) -> int:
        return self.eval_sync(script, numkeys, key, token, *args)


class FlakySyncLeaderRedis(FakeSyncLeaderRedis):
    def __init__(self, failures: int, error_type: type[Exception]) -> None:
        super().__init__()
        self.failures = failures
        self.error_type = error_type

    def eval(self, script: str, numkeys: int, key: str, token: str, *args: Any) -> int:
        if self.failures > 0:
            self.failures -= 1
            raise self.error_type("temporary redis failure")
        return super().eval(script, numkeys, key, token, *args)


class CapturingWorker(SmartEngineStreamWorker):
    def __init__(self, supervisor) -> None:
        super().__init__(Settings(), supervisor, lambda: "test-internal-token")
        self._sync_redis = FakeRedis()
        self.started: list[str] = []
        self.failed: list[tuple[str, str, str]] = []
        self.acked: list[str] = []
        self.retried: list[str] = []

    async def _post_started(self, task_id: str) -> None:
        self.started.append(task_id)

    async def _post_worker_failed(self, task_id: str, error_code: str, message: str) -> None:
        self.failed.append((task_id, error_code, message))

    async def _ack_and_clear_retry(self, message_id: str) -> None:
        self.acked.append(message_id)

    async def _retry_or_dlq(self, message_id: str, fields: dict[str, str], reason: str) -> None:
        del fields, reason
        self.retried.append(message_id)


class LeadershipCycleWorker(CapturingWorker):
    def __init__(self, supervisor) -> None:
        super().__init__(supervisor)
        self._redis = FakeLeaderRedis()
        self.consume_tokens: list[str] = []

    async def _consume_messages_until_leadership_lost(self, token: str) -> None:
        self.consume_tokens.append(token)


class HoldingLeadershipWorker(LeadershipCycleWorker):
    def __init__(self, supervisor) -> None:
        super().__init__(supervisor)
        self.entered_consume = asyncio.Event()
        self.release_consume = asyncio.Event()

    async def _consume_messages_until_leadership_lost(self, token: str) -> None:
        self.consume_tokens.append(token)
        self.entered_consume.set()
        await self.release_consume.wait()


class RenewLossWorker(CapturingWorker):
    leader_lock_renew_seconds = 0.01

    def __init__(self, supervisor) -> None:
        super().__init__(supervisor)
        self._redis = FakeLeaderRedis()
        self.read_attempts = 0

    async def _read_one_message(self) -> tuple[str, dict[str, str]] | None:
        self.read_attempts += 1
        await asyncio.sleep(1)
        return None


class ConcurrentProcessWorker(CapturingWorker):
    leader_lock_renew_seconds = 0.01

    def __init__(self, supervisor) -> None:
        super().__init__(supervisor)
        self.worker_concurrency = 2
        self._sync_redis = FakeSyncLeaderRedis()
        self.messages = [
            ("message-1", valid_fields()),
            ("message-2", valid_fields() | {"taskId": "task-2", "traceId": "trace-2"}),
        ]
        self.started_processing: list[str] = []
        self.process_started_event = asyncio.Event()
        self.release_processing_event = asyncio.Event()

    async def _read_one_message(self) -> tuple[str, dict[str, str]] | None:
        if self.messages:
            return self.messages.pop(0)
        await self.process_started_event.wait()
        return None

    async def _process_message(self, message_id: str, fields: dict[str, str]) -> None:
        del fields
        self.started_processing.append(message_id)
        if len(self.started_processing) == 2:
            self.process_started_event.set()
        await self.release_processing_event.wait()


class BlockingProcessWorker(CapturingWorker):
    leader_lock_renew_seconds = 0.01

    def __init__(self, supervisor) -> None:
        super().__init__(supervisor)
        self._sync_redis = FakeSyncLeaderRedis()
        self.block_seconds = 0.04

    async def _process_message(self, message_id: str, fields: dict[str, str]) -> None:
        del message_id, fields
        time.sleep(self.block_seconds)


class InvalidGenerationSupervisor:
    async def stream(self, request, cancelled=None):
        del request, cancelled
        raise GenerationOutputInvalidError("invalid generated asset")
        yield


class UnusedSupervisor:
    async def stream(self, request, cancelled=None):
        del request, cancelled
        yield


def valid_fields() -> dict[str, str]:
    return {
        "taskId": "task-1",
        "traceId": "trace-1",
        "serviceType": "RESOURCE_GENERATION",
        "paramsJson": json.dumps({"resourceType": "READING"}),
    }


@pytest.mark.asyncio
async def test_worker_reports_generation_output_invalid_for_execution_validation_failure() -> None:
    worker = CapturingWorker(InvalidGenerationSupervisor())

    await worker._process_message("message-1", valid_fields())

    assert worker.started == ["task-1"]
    assert worker.failed[0][0] == "task-1"
    assert worker.failed[0][1] == "GENERATION_OUTPUT_INVALID"
    assert "invalid generated asset" in worker.failed[0][2]
    assert worker.acked == ["message-1"]
    assert worker.retried == []


@pytest.mark.asyncio
async def test_worker_keeps_invalid_task_payload_for_bad_params_json() -> None:
    worker = CapturingWorker(UnusedSupervisor())
    fields = valid_fields()
    fields["paramsJson"] = "{bad-json"

    await worker._process_message("message-2", fields)

    assert worker.started == []
    assert worker.failed[0][0] == "task-1"
    assert worker.failed[0][1] == "INVALID_TASK_PAYLOAD"
    assert worker.acked == ["message-2"]
    assert worker.retried == []


@pytest.mark.asyncio
async def test_worker_consumes_messages_only_after_acquiring_leadership() -> None:
    leader = HoldingLeadershipWorker(UnusedSupervisor())
    follower = LeadershipCycleWorker(UnusedSupervisor())
    shared_redis = FakeLeaderRedis()
    leader._redis = shared_redis
    follower._redis = shared_redis

    leader_task = asyncio.create_task(leader._run_leadership_cycle("leader-token"))
    await asyncio.wait_for(leader.entered_consume.wait(), timeout=1)

    assert await follower._run_leadership_cycle("follower-token") is False
    assert leader.consume_tokens == ["leader-token"]
    assert follower.consume_tokens == []

    leader.release_consume.set()
    assert await asyncio.wait_for(leader_task, timeout=1) is True


@pytest.mark.asyncio
async def test_worker_stops_consuming_after_leadership_renewal_fails() -> None:
    worker = RenewLossWorker(UnusedSupervisor())
    worker._sync_redis = FakeSyncLeaderRedis()
    await worker._redis.set(worker._leader_lock_key(), "other-token", nx=True, ex=30)
    worker._sync_redis.values[worker._leader_lock_key()] = "other-token"

    await asyncio.wait_for(worker._consume_messages_until_leadership_lost("worker-token"), timeout=1)

    assert worker.read_attempts == 1


@pytest.mark.asyncio
async def test_worker_processes_messages_with_limited_concurrency() -> None:
    worker = ConcurrentProcessWorker(UnusedSupervisor())
    lock_key = worker._leader_lock_key()
    worker._sync_redis.values[lock_key] = "worker-token"

    consume_task = asyncio.create_task(worker._consume_messages_until_leadership_lost("worker-token"))
    await asyncio.wait_for(worker.process_started_event.wait(), timeout=1)

    assert worker.started_processing == ["message-1", "message-2"]

    worker._sync_redis.values[lock_key] = "other-token"
    worker.release_processing_event.set()
    await asyncio.wait_for(consume_task, timeout=1)


@pytest.mark.asyncio
async def test_watchdog_keeps_leadership_when_event_loop_is_blocked() -> None:
    worker = BlockingProcessWorker(UnusedSupervisor())
    lock_key = worker._leader_lock_key()
    worker._sync_redis.values[lock_key] = "worker-token"
    leadership_lost = asyncio.Event()
    watchdog = worker._start_leadership_watchdog("worker-token", leadership_lost)

    try:
        time.sleep(0.04)
    finally:
        watchdog.stop()

    assert not leadership_lost.is_set()
    assert worker._sync_redis.expiry_seconds[lock_key] == worker.leader_lock_ttl_seconds
    assert worker._sync_redis.eval_calls >= 1


@pytest.mark.asyncio
async def test_watchdog_tolerates_transient_redis_errors() -> None:
    worker = BlockingProcessWorker(UnusedSupervisor())
    redis_client = FlakySyncLeaderRedis(failures=2, error_type=worker_module.RedisError)
    lock_key = worker._leader_lock_key()
    redis_client.values[lock_key] = "worker-token"
    worker._sync_redis = redis_client
    leadership_lost = asyncio.Event()
    watchdog = worker._start_leadership_watchdog("worker-token", leadership_lost)

    try:
        time.sleep(0.06)
    finally:
        watchdog.stop()

    assert not leadership_lost.is_set()
    assert redis_client.values[lock_key] == "worker-token"


@pytest.mark.asyncio
async def test_watchdog_marks_lost_after_consecutive_redis_errors() -> None:
    worker = RenewLossWorker(UnusedSupervisor())
    redis_client = FlakySyncLeaderRedis(failures=3, error_type=worker_module.RedisError)
    redis_client.values[worker._leader_lock_key()] = "worker-token"
    worker._sync_redis = redis_client

    await asyncio.wait_for(worker._consume_messages_until_leadership_lost("worker-token"), timeout=1)

    assert worker.read_attempts == 1


@pytest.mark.asyncio
async def test_worker_releases_only_own_leadership_token() -> None:
    worker = CapturingWorker(UnusedSupervisor())
    worker._redis = FakeLeaderRedis()
    lock_key = worker._leader_lock_key()

    await worker._redis.set(lock_key, "other-token", nx=True, ex=30)

    assert await worker._release_leadership("worker-token") is False
    assert worker._redis.values[lock_key] == "other-token"

    worker._redis.values[lock_key] = "worker-token"

    assert await worker._release_leadership("worker-token") is True
    assert lock_key not in worker._redis.values
