"""
OpenAI text generation — streaming.

query_builder_call  → lightweight model, TAG prompt, returns optimised query string.
stream_response     → main model, CARE prompt, async generator of token strings.
"""

from openai import AsyncOpenAI, OpenAI
from django.conf import settings
from .prompts import tag_prompt, care_prompt

# Sync client for query builder (fast, non-streaming)
_sync_client: OpenAI | None = None

# Async client for streaming generation
_async_client: AsyncOpenAI | None = None


def get_sync_client() -> OpenAI:
    global _sync_client
    if _sync_client is None:
        _sync_client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _sync_client


def get_async_client() -> AsyncOpenAI:
    global _async_client
    if _async_client is None:
        _async_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _async_client


def query_builder_call(raw_query: str) -> str:
    """Use TAG prompt + fast model to produce an optimised search query."""
    client = get_sync_client()
    messages = tag_prompt.format_messages(raw_query=raw_query)
    # Convert LangChain messages to OpenAI dicts
    oai_messages = [{"role": m.type if m.type != "human" else "user", "content": m.content} for m in messages]
    # Remap "system" type
    oai_messages = [
        {"role": "system" if m["role"] == "system" else "user", "content": m["content"]}
        for m in oai_messages
    ]

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=oai_messages,
        max_tokens=60,
        temperature=0,
    )
    return resp.choices[0].message.content.strip()


async def stream_response(question: str, context: str):
    """
    Async generator that yields token strings from the main LLM.
    Uses CARE prompt.
    """
    client = get_async_client()
    messages = care_prompt.format_messages(context=context, question=question)
    oai_messages = []
    for m in messages:
        role = "system" if m.type == "system" else "user"
        oai_messages.append({"role": role, "content": m.content})

    stream = await client.chat.completions.create(
        model="gpt-4o",
        messages=oai_messages,
        max_tokens=800,
        temperature=0.3,
        stream=True,
    )

    async for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
