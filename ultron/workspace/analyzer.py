"""ULTRON Workspace Analyzer - Query workspace with retrieval + LLM"""
from typing import List, Dict, Any, Optional, AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
import asyncio

from ultron.workspace.parser import parser_registry, ParsedChunk
from ultron.workspace.chunker import chunker
from ultron.workspace.embedder import embedder
from ultron.workspace.retriever import retriever
from ultron.workspace.selector import workspace_manager
from ultron.model.loader import model_manager
from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity


@dataclass
class WorkspaceQueryResult:
    answer: str
    sources: List[Dict[str, Any]]
    confidence: float


class WorkspaceAnalyzer:
    """Analyze workspace documents using retrieval + LLM"""

    def __init__(self):
        self._indexing = False

    def index_workspace(self, workspace_id: str, progress_callback=None) -> Dict[str, Any]:
        """Index all files in a workspace"""
        ws = workspace_manager.get_workspace(workspace_id)
        if not ws:
            return {"success": False, "error": "Workspace not found"}

        ws_path = Path(ws.path)
        if not ws_path.exists():
            return {"success": False, "error": f"Workspace path not found: {ws_path}"}

        # Find all supported files
        supported_exts = set(config.config.workspace.supported_extensions)
        files = []
        for ext in supported_exts:
            files.extend(ws_path.rglob(f"*{ext}"))

        # Filter by size
        max_size = config.config.workspace.max_file_size_mb * 1024 * 1024
        files = [f for f in files if f.is_file() and f.stat().st_size <= max_size]

        if progress_callback:
            progress_callback(0, len(files), "Scanning files...")

        all_chunks = []
        processed = 0
        errors = 0

        for file_path in files:
            try:
                # Parse file
                parsed_chunks = list(parser_registry.parse(file_path))
                if not parsed_chunks:
                    continue

                # Chunk each parsed chunk
                for pc in parsed_chunks:
                    chunks = chunker.chunk(pc)
                    all_chunks.extend(chunks)

                processed += 1
            except Exception as e:
                errors += 1
                print(f"Error processing {file_path}: {e}")

            if progress_callback and processed % 10 == 0:
                progress_callback(processed, len(files), f"Processed {processed}/{len(files)}")

        # Index chunks
        if all_chunks:
            if progress_callback:
                progress_callback(processed, len(files), f"Indexing {len(all_chunks)} chunks...")

            embedder.initialize()
            retriever.initialize()
            indexed = retriever.index_chunks(workspace_id, all_chunks)

            workspace_manager.update_index_status(workspace_id, "ready")
            ws.file_count = processed
            workspace_manager._save_workspace(ws)

            return {
                "success": True,
                "files_processed": processed,
                "chunks_indexed": indexed,
                "errors": errors,
            }
        else:
            return {
                "success": False,
                "error": "No content to index",
                "files_processed": processed,
                "errors": errors,
            }

    async def query_async(self, workspace_id: str, question: str) -> WorkspaceQueryResult:
        """Query workspace asynchronously"""
        # Retrieve relevant chunks
        contexts = retriever.query(workspace_id, question)

        if not contexts:
            return WorkspaceQueryResult(
                answer="I don't have enough information from the provided documents to answer this question.",
                sources=[],
                confidence=0.0,
            )

        # Build prompt with context
        prompt = self._build_prompt(question, contexts)

        # Get LLM response using chat completion
        if model_manager.is_loaded():
            answer = await model_manager.generate_async(prompt, max_tokens=512, temperature=0.3)
        else:
            # Try to load
            if model_manager.load():
                answer = await model_manager.generate_async(prompt, max_tokens=512, temperature=0.3)
            else:
                answer = "Model not available. Please load a model first."

        # Calculate confidence based on retrieval scores
        avg_similarity = sum(c.get("similarity", 0) for c in contexts) / len(contexts) if contexts else 0

        return WorkspaceQueryResult(
            answer=answer.strip(),
            sources=[{
                "source": c.get("source", "unknown"),
                "chunk_index": c.get("chunk_index", 0),
                "similarity": c.get("similarity", 0),
                "preview": c.get("content", "")[:200],
            } for c in contexts],
            confidence=avg_similarity,
        )

    def query(self, workspace_id: str, question: str) -> WorkspaceQueryResult:
        """Synchronous query"""
        return asyncio.run(self.query_async(workspace_id, question))

    def _build_prompt(self, question: str, contexts: List[Dict]) -> str:
        """Build prompt with retrieved context - Qwen chat format"""
        if not contexts:
            return f"<|im_start|>user\nQuestion: {question}\n\nAnswer based on your knowledge:<|im_end|>\n<|im_start|>assistant\n"

        context_str = "\n\n".join([
            f"[Source: {c.get('source', 'unknown')}]\n{c.get('content', '')}"
            for c in contexts
        ])

        return f"""<|im_start|>system
You are ULTRON, a helpful AI assistant. Answer the question using ONLY the provided context.
If the answer is not in the context, say "I don't have enough information from the provided documents."
Cite sources using [Source: ...] format.
<|im_end|>
<|im_start|>user
Context:
{context_str}

Question: {question}
<|im_end|>
<|im_start|>assistant
"""

    def get_index_status(self, workspace_id: str) -> Dict[str, Any]:
        """Get indexing status"""
        ws = workspace_manager.get_workspace(workspace_id)
        if not ws:
            return {"status": "not_found"}

        stats = retriever.get_stats(workspace_id)
        return {
            "status": ws.index_status,
            "file_count": ws.file_count,
            "chunks_indexed": stats.get("count", 0),
            "last_accessed": ws.last_accessed,
        }


# Global instance
workspace_analyzer = WorkspaceAnalyzer()