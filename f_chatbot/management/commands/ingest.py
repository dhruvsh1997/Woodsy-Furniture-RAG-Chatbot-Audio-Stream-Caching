"""
python manage.py ingest

Reads all PDF, CSV, TXT, JSON files from data/raw_docs/,
applies parent/child splitting, generates embeddings,
and pushes everything to Weaviate.
"""

import os
import uuid
import json
import csv
from pathlib import Path

from django.core.management.base import BaseCommand
from django.conf import settings

import weaviate
import weaviate.classes as wvc
from langchain_openai import OpenAIEmbeddings
from langchain_community.document_loaders import PyPDFLoader, CSVLoader, TextLoader

from f_chatbot.rag_logic.retriever import (
    COLLECTION_NAME,
    child_splitter,
    parent_splitter,
    get_weaviate_client,
    get_embeddings,
)

RAW_DOCS_DIR = Path(settings.BASE_DIR) / "data" / "raw_docs"


class Command(BaseCommand):
    help = "Ingest raw documents into Weaviate using parent-child chunking."

    def handle(self, *args, **options):
        self.stdout.write("Connecting to Weaviate …")
        wv = get_weaviate_client()
        emb = get_embeddings()

        self._ensure_collection(wv)

        docs_dir = RAW_DOCS_DIR
        if not docs_dir.exists():
            self.stderr.write(f"Directory not found: {docs_dir}")
            return

        files = list(docs_dir.iterdir())
        self.stdout.write(f"Found {len(files)} file(s) in {docs_dir}")

        for file_path in files:
            self.stdout.write(f"  Processing {file_path.name} …")
            raw_text = self._load_file(file_path)
            if raw_text is None:
                self.stdout.write(f"    Skipped (unsupported type)")
                continue

            self._ingest_text(wv, emb, raw_text, file_path.name)

        wv.close()
        self.stdout.write(self.style.SUCCESS("Ingestion complete."))

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _ensure_collection(self, wv: weaviate.WeaviateClient):
        """Create Weaviate collection if it doesn't exist."""
        if wv.collections.exists(COLLECTION_NAME):
            self.stdout.write(f"Collection '{COLLECTION_NAME}' already exists.")
            return

        wv.collections.create(
            name=COLLECTION_NAME,
            vectorizer_config=wvc.config.Configure.Vectorizer.none(),  # we supply our own vectors
            properties=[
                wvc.config.Property(name="content", data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="doc_name", data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="chunk_id", data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="parent_id", data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="is_parent", data_type=wvc.config.DataType.BOOL),
            ],
        )
        self.stdout.write(f"Created collection '{COLLECTION_NAME}'.")

    def _load_file(self, path: Path) -> str | None:
        ext = path.suffix.lower()
        if ext == ".pdf":
            loader = PyPDFLoader(str(path))
            pages = loader.load()
            return "\n\n".join(p.page_content for p in pages)
        elif ext == ".txt":
            return path.read_text(encoding="utf-8", errors="ignore")
        elif ext == ".csv":
            rows = []
            with open(path, newline="", encoding="utf-8", errors="ignore") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows.append(", ".join(f"{k}: {v}" for k, v in row.items()))
            return "\n".join(rows)
        elif ext == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return "\n".join(json.dumps(item) for item in data)
            return json.dumps(data, indent=2)
        return None

    def _ingest_text(self, wv, emb, text: str, doc_name: str):
        collection = wv.collections.get(COLLECTION_NAME)

        # 1. Split into parent chunks
        parent_docs = parent_splitter.create_documents([text])

        objects_to_insert = []

        for parent_doc in parent_docs:
            parent_id = str(uuid.uuid4())
            parent_text = parent_doc.page_content

            # 2. Store parent chunk (is_parent=True, parent_id = itself)
            parent_vec = emb.embed_query(parent_text)
            objects_to_insert.append(
                wvc.data.DataObject(
                    properties={
                        "content": parent_text,
                        "doc_name": doc_name,
                        "chunk_id": parent_id,
                        "parent_id": parent_id,
                        "is_parent": True,
                    },
                    vector=parent_vec,
                )
            )

            # 3. Split parent into child chunks
            child_docs = child_splitter.create_documents([parent_text])
            for child_doc in child_docs:
                child_text = child_doc.page_content
                child_vec = emb.embed_query(child_text)
                objects_to_insert.append(
                    wvc.data.DataObject(
                        properties={
                            "content": child_text,
                            "doc_name": doc_name,
                            "chunk_id": str(uuid.uuid4()),
                            "parent_id": parent_id,
                            "is_parent": False,
                        },
                        vector=child_vec,
                    )
                )

        # Batch insert
        result = collection.data.insert_many(objects_to_insert)
        self.stdout.write(f"    Inserted {len(objects_to_insert)} objects for '{doc_name}'")
        if result.errors:
            for err in result.errors.values():
                self.stderr.write(f"    Error: {err}")
