"""
OpenAI TTS — sentence-buffered async audio generation.

Tokens from the LLM stream are buffered until a sentence boundary is detected.
Each complete sentence triggers an async TTS call that returns an MP3 blob,
which is then sent as binary over the WebSocket.
"""

import re
import asyncio
from openai import AsyncOpenAI
from django.conf import settings

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


async def text_to_speech_blob(text: str) -> bytes:
    """Convert a sentence to MP3 bytes via OpenAI TTS."""
    client = get_client()
    response = await client.audio.speech.create(
        model="tts-1",
        voice="nova",          # friendly, warm voice
        input=text,
        response_format="mp3",
    )
    return response.content


class SentenceBuffer:
    """
    Accumulates token strings. When a sentence boundary is detected,
    flushes the sentence and calls `on_sentence(sentence)` callback.
    """

    def __init__(self):
        self._buf = ""

    def feed(self, token: str) -> list[str]:
        """Feed a token, return list of complete sentences (may be empty)."""
        self._buf += token
        parts = SENTENCE_END.split(self._buf)
        if len(parts) > 1:
            complete = parts[:-1]
            self._buf = parts[-1]
            return complete
        return []

    def flush(self) -> str | None:
        """Return any remaining buffered text (end of stream)."""
        remaining = self._buf.strip()
        self._buf = ""
        return remaining if remaining else None


async def stream_audio(sentences: list[str], send_bytes_fn):
    """
    Given a list of sentences, generate TTS for each sequentially
    and call send_bytes_fn(blob) for each.
    """
    for sentence in sentences:
        if sentence.strip():
            blob = await text_to_speech_blob(sentence.strip())
            await send_bytes_fn(blob)
