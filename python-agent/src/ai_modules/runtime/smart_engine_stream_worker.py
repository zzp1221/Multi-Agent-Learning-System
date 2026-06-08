"""Redis Streams worker for SmartEngine long-running tasks."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import threading
import uuid
from collections.abc import Callable, Container
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import ValidationError

from src.ai_modules.config import Settings
from src.ai_modules.generation.content_chain import GenerationOutputInvalidError
from src.ai_modules.models import EngineStreamRequest, SSEEvent
from src.ai_modules.supervisor import PythonAgentSupervisor

try:
    import redis
    import redis.asyncio as redis_async
    from redis.exceptions import RedisError, ResponseError
except ModuleNotFoundError:  # pragma: no cover - protects hot-update before pip install.
    redis = None
    redis_async = None
    RedisError = ResponseError = Exception


LOGGER = logging.getLogger(__name__)
INTERNAL_TOKEN_HEADER = "X-Zhixue-Internal-Token"
LEADER_LOCK_TTL_SECONDS = 90
LEADER_LOCK_RENEW_SECONDS = 10
LEADER_LOCK_RETRY_SECONDS = 5
LEADER_LOCK_MAX_RENEW_ERRORS = 3
RENEW_LEADERSHIP_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
end
return 0
"""
RELEASE_LEADERSHIP_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


class JavaCallbackError(RuntimeError):
    """Raised when the Java control plane rejects or misses a worker callback."""


class RedisCancelledTasks(Container[str]):
    """Synchronous cancellation lookup used by the existing supervisor contract."""

    def __init__(self, client: Any, key_prefix: str) -> None:
        self.client = client
        self.key_prefix = key_prefix

    def __contains__(self, task_id: object) -> bool:
        if not isinstance(task_id, str):
            return False
        try:
            return bool(self.client.exists(self.key_prefix + task_id))
        except RedisError:
            LOGGER.exception("Failed to check Redis cancellation marker task_id=%s", task_id)
            return False


class LeadershipWatchdog:
    """Keeps the Redis leader lock alive from a thread outside the asyncio loop."""

    def __init__(
        self,
        *,
        redis_client: Any,
        lock_key: str,
        token: str,
        ttl_seconds: int,
        renew_seconds: int,
        max_renew_errors: int,
        lost_callback: Callable[[], None],
    ) -> None:
        self.redis_client = redis_client
        self.lock_key = lock_key
        self.token = token
        self.ttl_seconds = ttl_seconds
        self.renew_seconds = renew_seconds
        self.max_renew_errors = max_renew_errors
        self.lost_callback = lost_callback
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="smart-engine-leader-watchdog",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=max(1, self.renew_seconds))

    def _run(self) -> None:
        consecutive_errors = 0
        while not self._stop_event.wait(self.renew_seconds):
            try:
                renewed = self.redis_client.eval(
                    RENEW_LEADERSHIP_SCRIPT,
                    1,
                    self.lock_key,
                    self.token,
                    str(self.ttl_seconds),
                )
            except RedisError:
                consecutive_errors += 1
                LOGGER.warning(
                    "SmartEngine worker leadership renewal failed lock_key=%s token=%s errors=%s/%s",
                    self.lock_key,
                    self.token,
                    consecutive_errors,
                    self.max_renew_errors,
                    exc_info=True,
                )
                if consecutive_errors >= self.max_renew_errors:
                    self._mark_lost()
                    return
                continue

            consecutive_errors = 0
            if int(renewed) == 1:
                continue
            self._mark_lost()
            return

    def _mark_lost(self) -> None:
        if self._stop_event.is_set():
            return
        LOGGER.warning(
            "SmartEngine worker leadership lost lock_key=%s token=%s",
            self.lock_key,
            self.token,
        )
        self.lost_callback()


class SmartEngineStreamWorker:
    """Consumes SmartEngine task messages and reports execution events to Java."""

    leader_lock_ttl_seconds = LEADER_LOCK_TTL_SECONDS
    leader_lock_renew_seconds = LEADER_LOCK_RENEW_SECONDS
    leader_lock_retry_seconds = LEADER_LOCK_RETRY_SECONDS
    leader_lock_max_renew_errors = LEADER_LOCK_MAX_RENEW_ERRORS

    def __init__(
        self,
        settings: Settings,
        supervisor: PythonAgentSupervisor,
        internal_token_provider: Callable[[], str],
    ) -> None:
        self.settings = settings
        self.supervisor = supervisor
        self.internal_token_provider = internal_token_provider
        self._redis: Any | None = None
        self._sync_redis: Any | None = None
        self._http_client: httpx.AsyncClient | None = None
        self._leader_token: str | None = None
        self.worker_concurrency = max(1, settings.smart_engine_worker_concurrency)

    async def run_forever(self) -> None:
        if redis_async is None or redis is None:
            LOGGER.error("redis package is not installed; SmartEngine Redis Streams worker is disabled")
            return

        self._redis = redis_async.Redis(
            host=self.settings.redis_host,
            port=self.settings.redis_port,
            password=self.settings.redis_password or None,
            decode_responses=True,
        )
        self._sync_redis = redis.Redis(
            host=self.settings.redis_host,
            port=self.settings.redis_port,
            password=self.settings.redis_password or None,
            decode_responses=True,
        )
        self._http_client = httpx.AsyncClient(
            base_url=self.settings.control_plane_base_url.rstrip("/"),
            timeout=self.settings.smart_engine_callback_timeout_seconds,
        )

        await self._ensure_consumer_group()
        LOGGER.info(
            "SmartEngine Redis Streams worker started stream=%s group=%s consumer=%s",
            self.settings.smart_engine_stream_key,
            self.settings.smart_engine_consumer_group,
            self.settings.smart_engine_consumer_name,
        )

        leader_token = self._build_leader_token()
        waiting_for_leadership_logged = False
        try:
            while True:
                has_leadership = await self._run_leadership_cycle(leader_token)
                if has_leadership:
                    waiting_for_leadership_logged = False
                    continue
                if not waiting_for_leadership_logged:
                    LOGGER.info(
                        "SmartEngine worker waiting for leadership lock_key=%s token=%s",
                        self._leader_lock_key(),
                        leader_token,
                    )
                    waiting_for_leadership_logged = True
                await asyncio.sleep(self.leader_lock_retry_seconds)
        except asyncio.CancelledError:
            LOGGER.info("SmartEngine Redis Streams worker stopping")
            raise
        finally:
            await self.close()

    async def close(self) -> None:
        await self._release_active_leadership()
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
        if self._sync_redis is not None:
            self._sync_redis.close()
            self._sync_redis = None

    async def _run_leadership_cycle(self, token: str) -> bool:
        if not await self._acquire_leadership(token):
            return False

        self._leader_token = token
        LOGGER.info(
            "SmartEngine worker leadership acquired lock_key=%s token=%s",
            self._leader_lock_key(),
            token,
        )
        try:
            await self._consume_messages_until_leadership_lost(token)
        finally:
            released = await self._release_active_leadership(token)
            if released:
                LOGGER.info(
                    "SmartEngine worker leadership released lock_key=%s token=%s",
                    self._leader_lock_key(),
                    token,
                )
        return True

    async def _consume_messages_until_leadership_lost(self, token: str) -> None:
        leadership_lost = asyncio.Event()
        in_flight: set[asyncio.Task[None]] = set()
        in_flight_message_ids: set[str] = set()
        watchdog = self._start_leadership_watchdog(token, leadership_lost)
        should_cancel_in_flight = False
        try:
            while not leadership_lost.is_set():
                await self._collect_finished_tasks(in_flight, in_flight_message_ids)
                if len(in_flight) >= self.worker_concurrency:
                    await self._wait_for_processing_slot(in_flight, in_flight_message_ids, leadership_lost)
                    continue
                message = await self._read_message_while_leader(leadership_lost)
                if message is None:
                    await self._collect_finished_tasks(in_flight, in_flight_message_ids)
                    continue
                if leadership_lost.is_set():
                    break
                message_id, fields = message
                if message_id in in_flight_message_ids:
                    LOGGER.debug("Skipping in-flight pending SmartEngine message message_id=%s", message_id)
                    await asyncio.sleep(0)
                    continue
                task = asyncio.create_task(
                    self._process_message(message_id, fields),
                    name=f"smart-engine-task:{message_id}",
                )
                task._smart_engine_message_id = message_id  # type: ignore[attr-defined]
                task._smart_engine_task_id = fields.get("taskId", "")  # type: ignore[attr-defined]
                in_flight_message_ids.add(message_id)
                in_flight.add(task)
        except asyncio.CancelledError:
            should_cancel_in_flight = True
            raise
        finally:
            if should_cancel_in_flight or leadership_lost.is_set():
                await self._cancel_processing_tasks(in_flight)
            else:
                await self._drain_processing_tasks(in_flight)
            watchdog.stop()

    async def _wait_for_processing_slot(
        self,
        in_flight: set[asyncio.Task[None]],
        in_flight_message_ids: set[str],
        leadership_lost: asyncio.Event,
    ) -> None:
        lost_task = asyncio.create_task(leadership_lost.wait())
        try:
            done, _ = await asyncio.wait(
                {*in_flight, lost_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            await self._cancel_pending_tasks(lost_task)
            raise

        if lost_task in done:
            return
        await self._cancel_pending_tasks(lost_task)
        for task in done:
            in_flight.discard(task)
            self._discard_in_flight_message_id(task, in_flight_message_ids)
            await task

    async def _drain_processing_tasks(self, in_flight: set[asyncio.Task[None]]) -> None:
        while in_flight:
            done, _ = await asyncio.wait(in_flight, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                in_flight.discard(task)
                await task

    async def _cancel_processing_tasks(self, in_flight: set[asyncio.Task[None]]) -> None:
        tasks_to_cancel = [task for task in in_flight if not task.done()]
        for task in in_flight:
            if not task.done():
                task.cancel()
        if in_flight:
            all_tasks = list(in_flight)
            results = await asyncio.gather(*all_tasks, return_exceptions=True)
            result_by_task = dict(zip(all_tasks, results))
            await self._report_cancelled_processing_tasks(
                tasks_to_cancel,
                [result_by_task.get(task) for task in tasks_to_cancel],
            )

    async def _report_cancelled_processing_tasks(
        self,
        tasks: list[asyncio.Task[None]],
        results: list[Any | None],
    ) -> None:
        for task, result in zip(tasks, results):
            if result is None:
                continue
            task_id = getattr(task, "_smart_engine_task_id", "")
            message_id = getattr(task, "_smart_engine_message_id", "")
            if not isinstance(task_id, str) or not task_id or not isinstance(message_id, str) or not message_id:
                continue
            try:
                await self._post_worker_failed(
                    task_id,
                    "PYTHON_WORKER_CANCELLED",
                    "Python worker stopped before task completed",
                )
                await self._ack_and_clear_retry(message_id)
            except JavaCallbackError:
                LOGGER.exception("Failed to report cancelled SmartEngine task task_id=%s message_id=%s", task_id, message_id)

    async def _collect_finished_tasks(
        self,
        in_flight: set[asyncio.Task[None]],
        in_flight_message_ids: set[str] | None = None,
    ) -> None:
        for task in tuple(in_flight):
            if task.done():
                in_flight.discard(task)
                if in_flight_message_ids is not None:
                    self._discard_in_flight_message_id(task, in_flight_message_ids)
                await task

    def _discard_in_flight_message_id(
        self,
        task: asyncio.Task[None],
        in_flight_message_ids: set[str],
    ) -> None:
        message_id = getattr(task, "_smart_engine_message_id", None)
        if isinstance(message_id, str):
            in_flight_message_ids.discard(message_id)

    async def _read_message_while_leader(
        self,
        leadership_lost: asyncio.Event,
    ) -> tuple[str, dict[str, str]] | None:
        read_task = asyncio.create_task(self._read_one_message())
        lost_task = asyncio.create_task(leadership_lost.wait())
        try:
            done, _ = await asyncio.wait({read_task, lost_task}, return_when=asyncio.FIRST_COMPLETED)
        except BaseException:
            await self._cancel_pending_tasks(read_task, lost_task)
            raise

        if read_task in done:
            await self._cancel_pending_tasks(lost_task)
            return await read_task

        await self._cancel_pending_tasks(read_task)
        return None

    async def _process_message_while_leader(
        self,
        message_id: str,
        fields: dict[str, str],
        leadership_lost: asyncio.Event,
    ) -> None:
        process_task = asyncio.create_task(self._process_message(message_id, fields))
        lost_task = asyncio.create_task(leadership_lost.wait())
        try:
            done, _ = await asyncio.wait({process_task, lost_task}, return_when=asyncio.FIRST_COMPLETED)
        except BaseException:
            await self._cancel_pending_tasks(process_task, lost_task)
            raise

        if process_task in done:
            await self._cancel_pending_tasks(lost_task)
            await process_task
            return

        LOGGER.warning(
            "SmartEngine task processing cancelled after leadership loss message_id=%s token=%s",
            message_id,
            self._leader_token,
        )
        await self._cancel_pending_tasks(process_task)

    def _start_leadership_watchdog(self, token: str, leadership_lost: asyncio.Event) -> LeadershipWatchdog:
        if self._sync_redis is None:
            raise RuntimeError("Synchronous Redis client is not initialized")

        loop = asyncio.get_running_loop()

        def mark_lost() -> None:
            loop.call_soon_threadsafe(leadership_lost.set)

        watchdog = LeadershipWatchdog(
            redis_client=self._sync_redis,
            lock_key=self._leader_lock_key(),
            token=token,
            ttl_seconds=self.leader_lock_ttl_seconds,
            renew_seconds=self.leader_lock_renew_seconds,
            max_renew_errors=self.leader_lock_max_renew_errors,
            lost_callback=mark_lost,
        )
        watchdog.start()
        return watchdog

    async def _cancel_pending_tasks(self, *tasks: asyncio.Task[Any]) -> None:
        pending_tasks = [task for task in tasks if not task.done()]
        for task in pending_tasks:
            task.cancel()
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)

    def _leader_lock_key(self) -> str:
        return f"{self.settings.smart_engine_stream_key}:worker:leader"

    def _build_leader_token(self) -> str:
        return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"

    async def _acquire_leadership(self, token: str) -> bool:
        acquired = await self._redis.set(
            self._leader_lock_key(),
            token,
            nx=True,
            ex=self.leader_lock_ttl_seconds,
        )
        return bool(acquired)

    async def _renew_leadership(self, token: str) -> bool:
        if self._redis is None:
            return False
        try:
            renewed = await self._redis.eval(
                RENEW_LEADERSHIP_SCRIPT,
                1,
                self._leader_lock_key(),
                token,
                str(self.leader_lock_ttl_seconds),
            )
            return int(renewed) == 1
        except RedisError:
            LOGGER.exception("Failed to renew SmartEngine worker leadership")
            return False

    async def _release_active_leadership(self, token: str | None = None) -> bool:
        token_to_release = token or self._leader_token
        if token_to_release is None:
            return False
        released = await self._release_leadership(token_to_release)
        if self._leader_token == token_to_release:
            self._leader_token = None
        return released

    async def _release_leadership(self, token: str) -> bool:
        if self._redis is None:
            return False
        try:
            released = await self._redis.eval(
                RELEASE_LEADERSHIP_SCRIPT,
                1,
                self._leader_lock_key(),
                token,
            )
            return int(released) == 1
        except RedisError:
            LOGGER.exception("Failed to release SmartEngine worker leadership")
            return False

    async def _ensure_consumer_group(self) -> None:
        try:
            await self._redis.xgroup_create(
                self.settings.smart_engine_stream_key,
                self.settings.smart_engine_consumer_group,
                id="0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def _read_one_message(self) -> tuple[str, dict[str, str]] | None:
        reclaimed = await self._reclaim_stale_pending_message()
        if reclaimed is not None:
            return reclaimed

        fresh = await self._redis.xreadgroup(
            self.settings.smart_engine_consumer_group,
            self.settings.smart_engine_consumer_name,
            {self.settings.smart_engine_stream_key: ">"},
            count=1,
            block=self.settings.smart_engine_block_ms,
        )
        if fresh:
            return self._first_message(fresh)

        pending = await self._redis.xreadgroup(
            self.settings.smart_engine_consumer_group,
            self.settings.smart_engine_consumer_name,
            {self.settings.smart_engine_stream_key: "0"},
            count=1,
            block=1,
        )
        return self._first_message(pending)

    async def _reclaim_stale_pending_message(self) -> tuple[str, dict[str, str]] | None:
        min_idle_ms = max(1, self.leader_lock_ttl_seconds) * 1000
        try:
            claimed = await self._redis.xautoclaim(
                self.settings.smart_engine_stream_key,
                self.settings.smart_engine_consumer_group,
                self.settings.smart_engine_consumer_name,
                min_idle_ms,
                "0-0",
                count=1,
            )
        except ResponseError as exc:
            if "unknown command" in str(exc).lower():
                return None
            raise
        messages = claimed[1] if isinstance(claimed, (list, tuple)) and len(claimed) > 1 else []
        if not messages:
            return None
        message_id, fields = messages[0]
        return message_id, dict(fields)

    def _first_message(self, response: list[Any]) -> tuple[str, dict[str, str]] | None:
        if not response:
            return None
        _, messages = response[0]
        if not messages:
            return None
        message_id, fields = messages[0]
        return message_id, dict(fields)

    async def _process_message(self, message_id: str, fields: dict[str, str]) -> None:
        task_id = fields.get("taskId", "")
        try:
            if task_id and self._cancelled_tasks().__contains__(task_id):
                LOGGER.info("Skipping cancelled SmartEngine task before execution task_id=%s", task_id)
                await self._ack_and_clear_retry(message_id)
                return

            request = self._build_engine_request(fields)
        except JavaCallbackError as exc:
            await self._retry_or_dlq(message_id, fields, str(exc))
            return
        except (ValidationError, ValueError, KeyError, json.JSONDecodeError) as exc:
            await self._fail_and_ack(message_id, fields, "INVALID_TASK_PAYLOAD", str(exc))
            return

        try:
            await self._post_started(request.task_id)
            await self._execute_request(request)
            await self._ack_and_clear_retry(message_id)
        except JavaCallbackError as exc:
            await self._retry_or_dlq(message_id, fields, str(exc))
        except (GenerationOutputInvalidError, ValidationError) as exc:
            LOGGER.warning(
                "SmartEngine generation output invalid message_id=%s task_id=%s: %s",
                message_id,
                task_id,
                exc,
            )
            await self._fail_and_ack(message_id, fields, "GENERATION_OUTPUT_INVALID", str(exc))
        except Exception as exc:
            LOGGER.exception("SmartEngine worker execution failed message_id=%s task_id=%s", message_id, task_id)
            await self._fail_and_ack(message_id, fields, "PYTHON_WORKER_ERROR", str(exc))

    def _build_engine_request(self, fields: dict[str, str]) -> EngineStreamRequest:
        params = json.loads(fields.get("paramsJson") or "{}")
        return EngineStreamRequest.model_validate(
            {
                "userId": fields.get("userId") or None,
                "taskId": fields["taskId"],
                "traceId": fields["traceId"],
                "conversationId": fields.get("conversationId") or None,
                "serviceType": fields["serviceType"],
                "params": params,
            }
        )

    async def _execute_request(self, request: EngineStreamRequest) -> None:
        cancelled = self._cancelled_tasks()
        async for event in self.supervisor.stream(request, cancelled=cancelled):
            await self._post_event(request.task_id, event)
            if request.task_id in cancelled:
                LOGGER.info("Cancellation marker detected during SmartEngine task task_id=%s", request.task_id)

    def _cancelled_tasks(self) -> RedisCancelledTasks:
        return RedisCancelledTasks(self._sync_redis, self.settings.smart_engine_cancel_key_prefix)

    async def _fail_and_ack(self, message_id: str, fields: dict[str, str], error_code: str, message: str) -> None:
        task_id = fields.get("taskId")
        if task_id:
            try:
                await self._post_worker_failed(task_id, error_code, message)
            except JavaCallbackError as exc:
                await self._retry_or_dlq(message_id, fields, str(exc))
                return
        await self._ack_and_clear_retry(message_id)

    async def _post_started(self, task_id: str) -> None:
        await self._post(f"/internal/smart-engine/tasks/{task_id}/started", {})

    async def _post_event(self, task_id: str, event: SSEEvent) -> None:
        event_body = event.model_dump(by_alias=True, mode="json")
        payload = event_body.get("payload") or {}
        stage = payload.get("stage") if isinstance(payload.get("stage"), str) else None
        await self._post(
            f"/internal/smart-engine/tasks/{task_id}/events",
            {
                "eventType": event_body["event"],
                "stage": stage,
                "seq": event_body["seq"],
                "payload": payload,
            },
        )

    async def _post_worker_failed(self, task_id: str, error_code: str, message: str) -> None:
        await self._post(
            f"/internal/smart-engine/tasks/{task_id}/worker-failed",
            {
                "errorCode": error_code,
                "message": message,
            },
        )

    async def _post(self, path: str, payload: dict[str, Any]) -> None:
        if self._http_client is None:
            raise JavaCallbackError("Java callback client is not initialized")
        token = self.internal_token_provider().strip()
        headers = {INTERNAL_TOKEN_HEADER: token}
        attempts = self.settings.smart_engine_callback_retries + 1
        last_error = ""
        for attempt in range(1, attempts + 1):
            try:
                response = await self._http_client.post(path, json=payload, headers=headers)
                if 200 <= response.status_code < 300:
                    return
                last_error = f"status={response.status_code} body={response.text[:300]}"
                if response.status_code < 500 and response.status_code != 429:
                    break
            except httpx.RequestError as exc:
                last_error = str(exc)
            if attempt < attempts:
                await asyncio.sleep(0.5 * attempt)
        raise JavaCallbackError(f"Java callback failed path={path} {last_error}")

    async def _retry_or_dlq(self, message_id: str, fields: dict[str, str], reason: str) -> None:
        attempts = await self._increment_retry(message_id)
        if attempts >= self.settings.smart_engine_max_retries:
            dlq_fields = dict(fields)
            dlq_fields["failedReason"] = reason
            dlq_fields["failedAt"] = datetime.now(timezone.utc).isoformat()
            dlq_fields["attempts"] = str(attempts)
            await self._redis.xadd(self.settings.smart_engine_dlq_key, dlq_fields)
            await self._ack_and_clear_retry(message_id)
            LOGGER.error("Moved SmartEngine task to DLQ message_id=%s attempts=%s reason=%s", message_id, attempts, reason)
            return

        LOGGER.warning(
            "SmartEngine task callback failed; will retry message_id=%s attempts=%s reason=%s",
            message_id,
            attempts,
            reason,
        )
        await asyncio.sleep(self.settings.smart_engine_retry_backoff_seconds)

    async def _increment_retry(self, message_id: str) -> int:
        key = self._retry_key(message_id)
        attempts = await self._redis.incr(key)
        await self._redis.expire(key, 24 * 60 * 60)
        return int(attempts)

    async def _ack_and_clear_retry(self, message_id: str) -> None:
        await self._redis.xack(
            self.settings.smart_engine_stream_key,
            self.settings.smart_engine_consumer_group,
            message_id,
        )
        await self._redis.delete(self._retry_key(message_id))

    def _retry_key(self, message_id: str) -> str:
        return f"{self.settings.smart_engine_stream_key}:retry:{message_id}"
