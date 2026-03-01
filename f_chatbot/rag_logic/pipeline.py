"""
pipeline.py — Main RAG orchestrator.

Event types sent over WebSocket:
  {"type": "pipeline_source", "source": "cache"|"rag"}  — where answer came from
  {"type": "cache_hit",  "text": str}                   — full cached response
  {"type": "metadata",   "sources": list[dict]}          — retrieved doc info
  {"type": "token",      "text": str}                    — LLM token chunk
  {"type": "audio",      "data": bytes}                  — TTS audio blob (binary)
  {"type": "done"}                                       — stream finished
  {"type": "error",      "message": str}                 — error info
"""

import asyncio
from channels.db import database_sync_to_async
from .cache import lookup as cache_lookup, store as cache_store
from .retriever import get_weaviate_client, retrieve_parent_docs
from .llm_client import query_builder_call, stream_response, get_sync_client
from .tts_client import SentenceBuffer, text_to_speech_blob


async def run_pipeline(raw_query: str, session_id: str):
    """Async generator. Yields event dicts."""
    openai_sync = get_sync_client()

    # ── Gate 1: Semantic Cache ────────────────────────────────────────────────
    cached = await asyncio.to_thread(cache_lookup, raw_query, openai_sync)
    if cached:
        yield {"type": "pipeline_source", "source": "cache"}
        yield {"type": "cache_hit", "text": cached["response"]}
        yield {"type": "done"}
        return

    # Tell the frontend this is a live RAG response
    yield {"type": "pipeline_source", "source": "rag"}

    # ── Step 1: Query Builder (TAG) ───────────────────────────────────────────
    optimised_query = await asyncio.to_thread(query_builder_call, raw_query)

    # ── Step 2: Weaviate Retrieval ────────────────────────────────────────────
    wv_client = await asyncio.to_thread(get_weaviate_client)
    try:
        parent_docs = await asyncio.to_thread(
            retrieve_parent_docs, optimised_query, wv_client
        )
    finally:
        await asyncio.to_thread(wv_client.close)

    context = "\n\n---\n\n".join(d["content"] for d in parent_docs) or "No relevant documents found."
    metadata = [
        {
            "doc_name": d["doc_name"],
            "chunk_id": d["chunk_id"],
            "similarity_score": round(d["similarity_score"], 4),
        }
        for d in parent_docs
    ]

    # ── Step 3: Push metadata immediately ────────────────────────────────────
    yield {"type": "metadata", "sources": metadata}

    # ── Step 4: Stream tokens live, collect sentences for TTS ─────────────────
    full_response_parts: list[str] = []
    sentence_buf = SentenceBuffer()
    sentences_for_tts: list[str] = []

    async for token in stream_response(raw_query, context):
        full_response_parts.append(token)
        sentences = sentence_buf.feed(token)
        sentences_for_tts.extend(sentences)
        yield {"type": "token", "text": token}

    # Flush any remaining partial sentence
    remaining = sentence_buf.flush()
    if remaining:
        sentences_for_tts.append(remaining)

    # ── Step 4b: TTS — generate audio blobs sentence by sentence ─────────────
    for sentence in sentences_for_tts:
        if sentence.strip():
            try:
                blob = await text_to_speech_blob(sentence.strip())
                yield {"type": "audio", "data": blob}
            except Exception:
                pass  # TTS failure should not break the chat

    # ── Step 5: DB commit + cache store ──────────────────────────────────────
    full_response = "".join(full_response_parts)
    await _save_to_db(raw_query, full_response, metadata, session_id)
    await asyncio.to_thread(cache_store, raw_query, full_response, openai_sync)

    yield {"type": "done"}


async def _collect(async_gen):
    """Drain an async generator into a list."""
    results = []
    async for item in async_gen:
        results.append(item)
    return results


@database_sync_to_async
def _save_to_db(query: str, response: str, metadata: list[dict], session_id: str):
    from f_chatbot.models import ChatSession, Message, DocumentMetadata

    session, _ = ChatSession.objects.get_or_create(session_id=session_id)

    user_msg = Message.objects.create(session=session, role="user", content=query)
    asst_msg = Message.objects.create(session=session, role="assistant", content=response)

    for m in metadata:
        DocumentMetadata.objects.create(
            message=asst_msg,
            doc_name=m.get("doc_name", ""),
            chunk_id=m.get("chunk_id", ""),
            similarity_score=m.get("similarity_score"),
        )
