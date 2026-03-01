"""
Weaviate connection + Parent Document Retriever.

Child chunks  → small (200 tokens) — used for cosine-similarity search in Weaviate.
Parent chunks → large (1500 tokens) — full context returned to the LLM.

Parent chunks are stored in Weaviate alongside their children so we can
fetch them by parent_id without a separate store.
"""

import uuid
import weaviate
from django.conf import settings
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_weaviate.vectorstores import WeaviateVectorStore

COLLECTION_NAME = "FurnitureChunks"

# ─── Splitter config ──────────────────────────────────────────────────────────

child_splitter = RecursiveCharacterTextSplitter(
    chunk_size=300,
    chunk_overlap=30,
)

parent_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1500,
    chunk_overlap=100,
)


# ─── Weaviate client ─────────────────────────────────────────────────────────

def get_weaviate_client() -> weaviate.WeaviateClient:
    url = settings.WEAVIATE_URL
    api_key = settings.WEAVIATE_API_KEY

    if api_key:
        return weaviate.connect_to_wcs(
            cluster_url=url,
            auth_credentials=weaviate.auth.AuthApiKey(api_key),
        )
    return weaviate.connect_to_local(host="localhost", port=8080)


def get_embeddings() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        model="text-embedding-3-small",
        openai_api_key=settings.OPENAI_API_KEY,
    )


def get_vector_store(wv_client: weaviate.WeaviateClient) -> WeaviateVectorStore:
    return WeaviateVectorStore(
        client=wv_client,
        index_name=COLLECTION_NAME,
        text_key="content",
        embedding=get_embeddings(),
        attributes=["parent_id", "doc_name", "chunk_id"],
    )


# ─── Retrieval ────────────────────────────────────────────────────────────────

def retrieve_parent_docs(
    query: str,
    wv_client: weaviate.WeaviateClient,
    k: int = 4,
) -> list[dict]:
    """
    1. Search child chunks for query.
    2. Collect unique parent_ids.
    3. Fetch parent chunks from Weaviate by parent_id.
    4. Return list of dicts with content + metadata.
    """
    store = get_vector_store(wv_client)

    # Step 1: child search with scores
    child_results = store.similarity_search_with_score(query, k=k * 2)

    # Step 2: collect unique parent ids + best score per parent
    seen: dict[str, float] = {}
    for doc, score in child_results:
        pid = doc.metadata.get("parent_id")
        if pid and (pid not in seen or score > seen[pid]):
            seen[pid] = score

    # Step 3: fetch parent chunks
    parents = []
    collection = wv_client.collections.get(COLLECTION_NAME)
    for pid, score in seen.items():
        response = collection.query.fetch_objects(
            filters=weaviate.classes.query.Filter.by_property("chunk_id").equal(pid),
            limit=1,
        )
        if response.objects:
            obj = response.objects[0]
            props = obj.properties
            parents.append(
                {
                    "content": props.get("content", ""),
                    "doc_name": props.get("doc_name", "unknown"),
                    "chunk_id": pid,
                    "similarity_score": score,
                }
            )

    return parents
