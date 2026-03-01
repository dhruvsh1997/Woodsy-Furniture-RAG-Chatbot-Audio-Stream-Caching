"""
Semantic Cache backed by Redis.

Each entry stores:
  key   → "semcache:<sha256_of_query>"
  value → JSON { "query": str, "embedding": list[float], "response": str }

On lookup we do a linear cosine-similarity scan over all cached embeddings.
For production scale, switch to a Redis vector index (RediSearch / RedisVSS).
"""

import json
import hashlib
import numpy as np
import redis
from django.conf import settings

CACHE_PREFIX = "semcache:"
SIMILARITY_THRESHOLD = 0.92          # tune as needed
CACHE_TTL = 60 * 60 * 24 * 7        # 7 days

_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


def _cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a), np.array(b)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    return float(np.dot(va, vb) / denom) if denom else 0.0


def _embed(text: str, openai_client) -> list[float]:
    resp = openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=text,
    )
    return resp.data[0].embedding


# ─── Public API ───────────────────────────────────────────────────────────────

def lookup(query: str, openai_client) -> dict | None:
    """Return cached entry dict or None on miss."""
    client = _get_client()
    query_emb = _embed(query, openai_client)

    keys = client.keys(f"{CACHE_PREFIX}*")
    best_score, best_entry = 0.0, None

    for key in keys:
        raw = client.get(key)
        if not raw:
            continue
        entry = json.loads(raw)
        score = _cosine(query_emb, entry["embedding"])
        if score > best_score:
            best_score, best_entry = score, entry

    if best_score >= SIMILARITY_THRESHOLD:
        return best_entry
    return None


def store(query: str, response: str, openai_client) -> None:
    """Embed query and persist to Redis."""
    client = _get_client()
    emb = _embed(query, openai_client)
    key = CACHE_PREFIX + hashlib.sha256(query.encode()).hexdigest()
    entry = {"query": query, "embedding": emb, "response": response}
    client.setex(key, CACHE_TTL, json.dumps(entry))
