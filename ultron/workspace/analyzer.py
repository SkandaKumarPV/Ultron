"""ULTRON Workspace Analyzer - Query workspace with retrieval + LLM"""
from typing import List, Dict, Any, Optional, AsyncGenerator
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
import asyncio
import os
import re
import sqlite3

from ultron.workspace.parser import parser_registry, ParsedChunk
from ultron.workspace.chunker import chunker
from ultron.workspace import local_index
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

    @staticmethod
    def folder_contents(folder: Path) -> str:
        """List the immediate entries in the selected folder."""
        entries = sorted(
            ((path.is_dir(), path.name) for path in folder.iterdir()
             if not path.name.startswith(".") and not path.is_symlink()),
            key=lambda entry: (not entry[0], entry[1].casefold()),
        )
        folders = sum(is_directory for is_directory, _ in entries)
        files = len(entries) - folders
        file_label = "file" if files == 1 else "files"
        folder_label = "folder" if folders == 1 else "folders"
        lines = [f"{folder.name or folder}: {files} {file_label} and {folders} {folder_label} at the top level."]
        lines.extend(f"• {name}/" if is_directory else f"• {name}" for is_directory, name in entries[:50])
        if len(entries) > 50:
            lines.append(f"Showing 50 of {len(entries)} items.")
        return "\n".join(lines)

    @staticmethod
    def _workspace_files(ws_path: Path):
        supported_exts = set(config.config.workspace.supported_extensions)
        max_size = config.config.workspace.max_file_size_mb * 1024 * 1024
        excluded = {".git", ".ultron", ".venv", "venv", "node_modules", "__pycache__", ".codex"}
        for folder, directories, names in os.walk(ws_path):
            directories[:] = [
                name for name in directories
                if name.lower() not in excluded
                and (Path(folder) / name).resolve().is_relative_to(ws_path)
            ]
            for name in names:
                if name.lower().startswith(".env"):
                    continue
                file = Path(folder) / name
                if file.suffix.lower() not in supported_exts or file.is_symlink():
                    continue
                try:
                    if file.resolve().is_relative_to(ws_path) and file.stat().st_size <= max_size:
                        yield file
                except OSError:
                    continue

    def index_workspace(self, workspace_id: str, progress_callback=None) -> Dict[str, Any]:
        """Index all files in a workspace"""
        ws = workspace_manager.get_workspace(workspace_id)
        if not ws:
            return {"success": False, "error": "Workspace not found"}

        ws_path = Path(ws.path).resolve()
        if not ws_path.is_dir():
            return {"success": False, "error": f"Workspace path not found: {ws_path}"}

        files = list(self._workspace_files(ws_path))
        if progress_callback:
            progress_callback(0, len(files), "Scanning files...")

        all_chunks = []
        processed = 0
        errors = 0

        for file_path in files:
            try:
                # Parse file
                parsed_chunks = [chunk for chunk in parser_registry.parse(file_path) if chunk.content.strip()]
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

            try:
                indexed = local_index.rebuild(workspace_id, all_chunks)
            except (OSError, sqlite3.Error) as error:
                return {"success": False, "error": f"Could not save the workspace index: {error}"}

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
                "error": "No readable content found. Check the file formats and size limit.",
                "files_processed": processed,
                "errors": errors,
            }

    def _document_matches(self, folder: Path, question: str) -> List[Path]:
        """Resolve a filename or document title in the selected workspace."""
        question = question.casefold()
        words = re.findall(r"[^\W_]+", question)
        document_request = re.search(r"\b(?:summari[sz]e|summary|document|presentation|pdf|file)\b", question)
        matches = []
        for path in self._workspace_files(folder):
            relative = path.relative_to(folder).as_posix().casefold()
            if "/" in relative and re.search(rf"(?<!\w){re.escape(relative)}(?!\w|\.[\w])", question.replace("\\", "/")):
                matches.append(((4, 1.0), path))
                continue
            if re.search(rf"(?<!\w){re.escape(path.name.casefold())}(?!\w|\.[\w])", question):
                matches.append(((3, 1.0), path))
                continue

            title = re.findall(r"[^\W_]+", path.stem.casefold())
            if not title or (len(title) == 1 and len(title[0]) < (4 if document_request else 6)):
                continue
            phrases = (words[i:i + len(title)] for i in range(len(words) - len(title) + 1))
            scores = []
            for phrase in phrases:
                if phrase == title:
                    scores.append((2, float(len(title))))
                elif document_request and len("".join(title)) >= 6:
                    similarity = SequenceMatcher(None, " ".join(title), " ".join(phrase)).ratio()
                    if similarity >= 0.88:
                        scores.append((1, similarity))
            if scores:
                matches.append((max(scores), path))
        if not matches:
            return []
        best = max(score for score, _ in matches)
        return sorted(path for score, path in matches if score == best)

    @staticmethod
    def _document_context(path: Path, folder: Path) -> List[Dict]:
        # Preserve the whole text of short documents. Space samples throughout longer
        # documents so a summary can also cover sections near the end.
        text = "\n\n".join(chunk.content.strip() for chunk in parser_registry.parse(path) if chunk.content.strip())
        limit = min(6500, max(512, (config.config.model.n_ctx - 850) * 2))
        if len(text) > limit:
            section_size = limit // 3 - 30
            middle = (len(text) - section_size) // 2
            samples = (text[:section_size], text[middle:middle + section_size], text[-section_size:])
            text = "\n\n[Excerpt break]\n\n".join(samples)
        return [{"source": path.relative_to(folder).as_posix(), "content": text}] if text else []

    async def query_async(self, workspace_id: str, question: str) -> WorkspaceQueryResult:
        """Query workspace asynchronously"""
        workspace = workspace_manager.get_workspace(workspace_id)
        contexts = []
        if workspace:
            folder = Path(workspace.path).resolve()
            documents = await asyncio.to_thread(self._document_matches, folder, question)
            if len(documents) > 1:
                choices = "\n".join(f"• {path.relative_to(folder).as_posix()}" for path in documents)
                return WorkspaceQueryResult(f"Which document did you mean?\n{choices}", [], 0.0)
            if documents:
                document = documents[0]
                contexts = await asyncio.to_thread(self._document_context, document, folder)
                if not contexts:
                    return WorkspaceQueryResult(
                        f"I found {document.name} in the selected folder, but could not extract readable text from it.",
                        [], 0.0,
                    )
        if not contexts:
            contexts = local_index.query(workspace_id, question, config.config.workspace.retriever_top_k)
            if contexts is None:
                contexts = retriever.query(workspace_id, question)

        if not contexts:
            return WorkspaceQueryResult(
                answer="I don't have enough information from the provided documents to answer this question.",
                sources=[],
                confidence=0.0,
            )

        # Build prompt with context
        prompt = self._build_prompt(question, contexts)

        if model_manager.is_loaded() or await asyncio.to_thread(model_manager.load):
            answer = await model_manager.generate_async(prompt, max_tokens=512, temperature=0.3)
        else:
            excerpts = "\n\n".join(
                f"{Path(item['source']).name}\n{item['content'][:350]}" for item in contexts[:3]
            )
            answer = "The local language model could not load. Matching excerpts from your folder:\n\n" + excerpts

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

        count = local_index.count(workspace_id)
        if count is None:
            count = retriever.get_stats(workspace_id).get("count", 0)
        return {
            "status": ws.index_status,
            "file_count": ws.file_count,
            "chunks_indexed": count,
            "last_accessed": ws.last_accessed,
        }


# Global instance
workspace_analyzer = WorkspaceAnalyzer()
