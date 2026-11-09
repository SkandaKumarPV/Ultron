"""ULTRON Chunker - Text chunking with metadata preservation"""
from typing import List, Generator
from dataclasses import dataclass
import re

from ultron.workspace.parser import ParsedChunk
from ultron.config import config


@dataclass
class ChunkConfig:
    chunk_size: int = 512
    chunk_overlap: int = 64
    min_chunk_size: int = 50


class Chunker:
    """Split parsed content into overlapping chunks with metadata"""

    def __init__(self, config: ChunkConfig = None):
        if config is None:
            cfg = config.config.workspace if hasattr(config, 'config') else None
            # Use default if config not available
            self.config = ChunkConfig(
                chunk_size=512,
                chunk_overlap=64,
                min_chunk_size=50
            )
        else:
            self.config = config

    def chunk(self, parsed_chunk: ParsedChunk) -> List[ParsedChunk]:
        """Split a parsed chunk into smaller overlapping chunks"""
        content = parsed_chunk.content
        if len(content) <= self.config.chunk_size:
            return [parsed_chunk]

        chunks = []
        start = 0
        chunk_index = 0

        while start < len(content):
            end = min(start + self.config.chunk_size, len(content))

            # Try to break at sentence boundary
            if end < len(content):
                # Look for sentence end within last 100 chars
                search_start = max(start + self.config.chunk_size - 100, start)
                sentence_end = self._find_sentence_boundary(content, search_start, end)
                if sentence_end > start:
                    end = sentence_end

            chunk_content = content[start:end].strip()

            if len(chunk_content) >= self.config.min_chunk_size:
                chunks.append(ParsedChunk(
                    content=chunk_content,
                    source_path=parsed_chunk.source_path,
                    chunk_index=chunk_index,
                    start_char=start,
                    end_char=end,
                    metadata={
                        **parsed_chunk.metadata,
                        'parent_chunk_index': parsed_chunk.chunk_index,
                        'chunk_method': 'sentence_aware',
                    }
                ))
                chunk_index += 1

            # Move start forward with overlap
            start = end - self.config.chunk_overlap
            if start < 0:
                start = 0

            # Prevent infinite loop
            if start >= len(content):
                break

        return chunks

    def _find_sentence_boundary(self, text: str, start: int, end: int) -> int:
        """Find the last sentence boundary (., !, ?) in range"""
        # Search backwards from end
        for i in range(end - 1, start - 1, -1):
            if text[i] in '.!?':
                # Check if it's likely end of sentence (followed by space/newline or end)
                if i + 1 >= len(text) or text[i + 1] in ' \n\r\t':
                    return i + 1
        return end

    def chunk_all(self, parsed_chunks: Generator[ParsedChunk, None, None]) -> List[ParsedChunk]:
        """Chunk multiple parsed chunks"""
        all_chunks = []
        for pc in parsed_chunks:
            all_chunks.extend(self.chunk(pc))
        return all_chunks


# Global chunker instance
chunker = Chunker()