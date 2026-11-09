"""ULTRON Retriever - Vector search using ChromaDB"""
from typing import List, Dict, Any, Optional
from pathlib import Path
import threading

from ultron.config import config
from ultron.workspace.parser import ParsedChunk
from ultron.workspace.embedder import embedder
from ultron.workspace.selector import workspace_manager
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity


class Retriever:
    """Vector-based document retrieval with workspace isolation"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._client = None
        self._collections: Dict[str, Any] = {}  # workspace_id -> collection
        self._initialized = True

    def initialize(self):
        """Initialize ChromaDB client"""
        if self._client is not None:
            return True

        try:
            import chromadb
            from chromadb.config import Settings

            persist_dir = config.resolve_path("chroma")
            persist_dir.mkdir(parents=True, exist_ok=True)

            self._client = chromadb.PersistentClient(
                path=str(persist_dir),
                settings=Settings(anonymized_telemetry=False)
            )
            return True

        except Exception as e:
            audit_log(AuditEventType.ERROR, severity=AuditSeverity.ERROR,
                      details={"message": f"Failed to init ChromaDB: {e}"})
            return False

    def _get_collection(self, workspace_id: str):
        """Get or create collection for workspace"""
        if workspace_id in self._collections:
            return self._collections[workspace_id]

        if not self.initialize():
            return None

        try:
            collection = self._client.get_or_create_collection(
                name=f"workspace_{workspace_id}",
                metadata={"workspace_id": workspace_id}
            )
            self._collections[workspace_id] = collection
            return collection
        except Exception as e:
            audit_log(AuditEventType.ERROR, severity=AuditSeverity.ERROR,
                      details={"message": f"Failed to get collection: {e}", "workspace_id": workspace_id})
            return None

    def index_chunks(self, workspace_id: str, chunks: List[ParsedChunk]) -> int:
        """Index chunks for a workspace"""
        collection = self._get_collection(workspace_id)
        if not collection:
            return 0

        if not chunks:
            return 0

        # Ensure embedder is loaded
        if not embedder.is_loaded():
            embedder.initialize()

        # Prepare data
        ids = []
        documents = []
        metadatas = []
        embeddings = []

        for chunk in chunks:
            chunk_id = f"{chunk.source_path}::{chunk.chunk_index}"
            ids.append(chunk_id)
            documents.append(chunk.content)
            metadatas.append({
                "source_path": chunk.source_path,
                "chunk_index": chunk.chunk_index,
                "start_char": chunk.start_char,
                "end_char": chunk.end_char,
                **chunk.metadata,
            })

        # Generate embeddings
        try:
            embeddings = embedder.embed(documents).tolist()
        except Exception as e:
            audit_log(AuditEventType.ERROR, severity=AuditSeverity.ERROR,
                      details={"message": f"Embedding failed: {e}"})
            return 0

        # Upsert to collection
        try:
            collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                embeddings=embeddings,
            )

            audit_log(AuditEventType.TOOL_INVOKED, workspace_id=workspace_id,
                      tool_name="retriever.index", action="index",
                      result="success", details={"chunks": len(chunks)})
            return len(chunks)

        except Exception as e:
            audit_log(AuditEventType.ERROR, severity=AuditSeverity.ERROR,
                      details={"message": f"Index upsert failed: {e}"})
            return 0

    def query(self, workspace_id: str, query_text: str, top_k: int = None, threshold: float = None) -> List[Dict[str, Any]]:
        """Query workspace for relevant chunks"""
        cfg = config.config.workspace
        top_k = top_k or cfg.retriever_top_k
        threshold = threshold or cfg.retriever_threshold

        collection = self._get_collection(workspace_id)
        if not collection:
            return []

        if not embedder.is_loaded():
            embedder.initialize()

        # Embed query
        try:
            query_embedding = embedder.embed_single(query_text).tolist()
        except Exception as e:
            audit_log(AuditEventType.ERROR, severity=AuditSeverity.ERROR,
                      details={"message": f"Query embedding failed: {e}"})
            return []

        # Search
        try:
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            audit_log(AuditEventType.ERROR, severity=AuditSeverity.ERROR,
                      details={"message": f"Query failed: {e}"})
            return []

        # Format results
        formatted = []
        if results["documents"] and results["documents"][0]:
            for i, (doc, meta, dist) in enumerate(zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0]
            )):
                # Convert distance to similarity (ChromaDB uses L2 distance)
                similarity = 1.0 / (1.0 + dist)
                if similarity >= threshold:
                    formatted.append({
                        "content": doc,
                        "source": meta.get("source_path", "unknown"),
                        "chunk_index": meta.get("chunk_index", 0),
                        "start_char": meta.get("start_char", 0),
                        "end_char": meta.get("end_char", 0),
                        "similarity": similarity,
                        "metadata": meta,
                    })

        audit_log(AuditEventType.TOOL_INVOKED, workspace_id=workspace_id,
                  tool_name="retriever.query", action="query",
                  result="success", details={"results": len(formatted), "query": query_text[:50]})
        return formatted

    def delete_workspace(self, workspace_id: str) -> bool:
        """Delete all data for a workspace"""
        if not self._client:
            return False

        try:
            self._client.delete_collection(name=f"workspace_{workspace_id}")
            self._collections.pop(workspace_id, None)

            audit_log(AuditEventType.TOOL_INVOKED, workspace_id=workspace_id,
                      tool_name="retriever.delete", action="delete",
                      result="success")
            return True
        except Exception as e:
            audit_log(AuditEventType.ERROR, severity=AuditSeverity.ERROR,
                      details={"message": f"Delete workspace failed: {e}"})
            return False

    def get_stats(self, workspace_id: str) -> Dict[str, Any]:
        """Get collection statistics"""
        collection = self._get_collection(workspace_id)
        if not collection:
            return {"count": 0}

        try:
            count = collection.count()
            return {"count": count}
        except Exception:
            return {"count": 0}


# Global instance
retriever = Retriever()