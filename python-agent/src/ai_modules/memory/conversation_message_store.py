"""完整对话消息记录的持久化层。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.ai_modules.config import get_settings


class ConversationMessageDocument(BaseModel):
    """单条持久化的对话消息。"""

    message_id: str = Field(default_factory=lambda: str(uuid4()), alias="messageId")
    conversation_id: str = Field(alias="conversationId")
    user_id: str | None = Field(default=None, alias="userId")
    role: Literal["user", "assistant"]
    content: str
    reasoning_content: str = Field(default="", alias="reasoningContent")
    image_urls: list[str] = Field(default_factory=list, alias="imageUrls")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="createdAt")
    # MongoDB 模式字段 — 从 conversation_id 映射
    qna_session_id: str | None = Field(default=None, alias="qnaSessionId")
    thread_id: str | None = Field(default=None, alias="threadId")
    message_seq: int | None = Field(default=None, alias="messageSeq")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("created_at", mode="before")
    @classmethod
    def _ensure_timezone(cls, v: datetime) -> datetime:
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    @field_validator("image_urls", mode="before")
    @classmethod
    def _normalize_image_urls(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    @field_validator("reasoning_content", mode="before")
    @classmethod
    def _normalize_reasoning_content(cls, value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""


class ConversationMessageStore(Protocol):
    """完整对话记录的持久化契约。"""

    async def append_message(self, document: ConversationMessageDocument) -> None: ...

    async def list_messages(
        self,
        *,
        conversation_id: str,
        user_id: str | None,
        page: int | None = None,
        size: int | None = None,
    ) -> list[ConversationMessageDocument]: ...


class InMemoryConversationMessageStore:
    """测试和本地回退模式使用的内存消息存储。"""

    def __init__(self) -> None:
        self.documents: list[ConversationMessageDocument] = []

    async def append_message(self, document: ConversationMessageDocument) -> None:
        self.documents.append(document)

    async def list_messages(
        self,
        *,
        conversation_id: str,
        user_id: str | None,
        page: int | None = None,
        size: int | None = None,
    ) -> list[ConversationMessageDocument]:
        documents = [
            document
            for document in self.documents
            if document.conversation_id == conversation_id and document.user_id == user_id
        ]
        if page is None or size is None:
            return documents
        start = page * size
        end = start + size
        return documents[start:end]


class MongoConversationMessageStore:
    """基于 MongoDB 的对话记录存储。"""

    def __init__(
        self,
        mongo_uri: str | None = None,
        database_name: str | None = None,
        collection_name: str = "conversation_messages",
        collection: Any | None = None,
    ) -> None:
        settings = get_settings()
        self.mongo_uri = mongo_uri or settings.mongo_uri
        self.database_name = database_name or settings.mongo_db
        self.collection_name = collection_name
        self._collection = collection

    @property
    def collection(self) -> Any:
        if self._collection is None:
            from pymongo import MongoClient

            client = MongoClient(self.mongo_uri)
            self._collection = client[self.database_name][self.collection_name]
        return self._collection

    async def append_message(self, document: ConversationMessageDocument) -> None:
        # 从 conversation_id 填充 MongoDB 必需的字段
        if document.qna_session_id is None:
            document.qna_session_id = document.conversation_id
        if document.message_seq is None:
            # 根据该对话中的已有消息自动生成序列号
            count = await asyncio.to_thread(
                self.collection.count_documents,
                {"qnaSessionId": document.conversation_id},
            )
            document.message_seq = count + 1
        await asyncio.to_thread(
            self.collection.insert_one,
            document.model_dump(by_alias=True, exclude_none=True),
        )

    async def list_messages(
        self,
        *,
        conversation_id: str,
        user_id: str | None,
        page: int | None = None,
        size: int | None = None,
    ) -> list[ConversationMessageDocument]:
        criteria: dict[str, Any] = {"conversationId": conversation_id}
        if user_id is not None:
            criteria["userId"] = user_id

        find_kwargs: dict[str, Any] = {
            "sort": [("createdAt", 1)],
        }
        if page is not None and size is not None:
            find_kwargs["skip"] = page * size
            find_kwargs["limit"] = size

        records = await asyncio.to_thread(
            lambda: list(
                self.collection.find(
                    criteria,
                    **find_kwargs,
                )
            )
        )
        documents: list[ConversationMessageDocument] = []
        for record in records:
            record.pop("_id", None)
            documents.append(ConversationMessageDocument.model_validate(record))
        return documents
