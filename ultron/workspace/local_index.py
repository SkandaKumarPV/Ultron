"""Local full-text workspace index using Python's bundled SQLite."""

from contextlib import closing
import re
import sqlite3

from ultron.config import config


def _path(workspace_id):
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", workspace_id):
        raise ValueError("Invalid workspace ID.")
    return config.resolve_path(f"search/{workspace_id}.sqlite3")


def rebuild(workspace_id, chunks):
    path = _path(workspace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(content, source, chunk_index UNINDEXED)"
        )
        connection.execute("DELETE FROM chunks")
        connection.executemany(
            "INSERT INTO chunks VALUES (?, ?, ?)",
            ((chunk.content, chunk.source_path, chunk.chunk_index) for chunk in chunks),
        )
    return len(chunks)


def query(workspace_id, question, limit):
    path = _path(workspace_id)
    if not path.is_file():
        return None
    tokens = list(dict.fromkeys(re.findall(r"[^\W_]+", question.casefold())))
    common = {"a", "an", "is", "it", "the", "do", "in", "what", "about", "can", "you",
              "me", "my", "tell", "this", "and", "folder", "workspace", "file", "of"}
    terms = [token for token in tokens if token not in common][:24]
    with closing(sqlite3.connect(path)) as connection:
        rows = []
        filename = re.search(r"[\w.-]+\.[a-zA-Z0-9]{1,8}\b", question)
        if filename:
            rows = connection.execute(
                "SELECT content, source, chunk_index FROM chunks "
                "WHERE substr(lower(source), -length(?)) = lower(?) ORDER BY chunk_index LIMIT ?",
                (filename[0], filename[0], limit),
            ).fetchall()
        if terms and not rows:
            match = " OR ".join(f'"{token}"' for token in terms)
            rows = connection.execute(
                "SELECT content, source, chunk_index FROM chunks WHERE chunks MATCH ? "
                "ORDER BY bm25(chunks, 1.0, 2.0) LIMIT ?",
                (match, limit),
            ).fetchall()
        if not rows and (not terms or re.search(r"\b(?:summari[sz]e|overview|folder|project|workspace)\b", question, re.I)):
            rows = connection.execute(
                "SELECT content, source, chunk_index FROM chunks "
                "ORDER BY CASE WHEN lower(source) LIKE '%readme%' THEN 0 ELSE 1 END, chunk_index LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(content=content, source=source, chunk_index=index, similarity=0.0)
            for content, source, index in rows]


def count(workspace_id):
    path = _path(workspace_id)
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("SELECT count(*) FROM chunks").fetchone()[0]
