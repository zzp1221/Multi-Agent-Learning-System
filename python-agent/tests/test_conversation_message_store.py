import pytest

from src.ai_modules.memory import ConversationMessageDocument, InMemoryConversationMessageStore


@pytest.mark.asyncio
async def test_conversation_message_document_preserves_reasoning_content() -> None:
    store = InMemoryConversationMessageStore()
    document = ConversationMessageDocument(
        conversationId="conversation-1",
        userId="user-1",
        role="assistant",
        content="final answer",
        reasoningContent="公开思考过程",
    )

    await store.append_message(document)
    messages = await store.list_messages(conversation_id="conversation-1", user_id="user-1")

    assert messages[0].reasoning_content == "公开思考过程"
    assert messages[0].model_dump(by_alias=True)["reasoningContent"] == "公开思考过程"


def test_internal_conversation_messages_round_trip_reasoning_content(
    client,
    internal_token: str,
    monkeypatch,
) -> None:
    import server

    monkeypatch.setattr(server, "MESSAGE_STORE", InMemoryConversationMessageStore())
    headers = {"X-Zhixue-Internal-Token": internal_token}

    response = client.post(
        "/internal/conversations/conversation-1/messages",
        headers=headers,
        json={
            "userId": "user-1",
            "role": "assistant",
            "content": "final answer",
            "reasoningContent": "公开思考过程",
        },
    )

    assert response.status_code == 200
    list_response = client.get(
        "/internal/conversations/conversation-1/messages?userId=user-1",
        headers=headers,
    )

    assert list_response.status_code == 200
    assert list_response.json()[0]["reasoningContent"] == "公开思考过程"
