"""ULTRON File Parser - Multi-format document parsing"""
import os
from pathlib import Path
from typing import List, Dict, Any, Optional, Generator
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class ParsedChunk:
    """A chunk of parsed content with metadata"""
    content: str
    source_path: str
    chunk_index: int
    start_char: int
    end_char: int
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class BaseParser:
    """Base class for file parsers"""

    extensions: List[str] = []

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.extensions

    def parse(self, file_path: Path) -> Generator[ParsedChunk, None, None]:
        raise NotImplementedError


class TextParser(BaseParser):
    """Parse plain text files"""
    extensions = ['.txt', '.md', '.py', '.js', '.ts', '.json', '.yaml', '.yml', '.csv', '.log', '.ini', '.cfg', '.toml']

    def parse(self, file_path: Path) -> Generator[ParsedChunk, None, None]:
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except Exception as e:
            logger.error(f"Failed to read {file_path}: {e}")
            return

        yield ParsedChunk(
            content=content,
            source_path=str(file_path),
            chunk_index=0,
            start_char=0,
            end_char=len(content),
            metadata={'parser': 'text', 'encoding': 'utf-8'}
        )


class PDFParser(BaseParser):
    """Parse PDF files using pypdf"""
    extensions = ['.pdf']

    def __init__(self):
        self._pypdf = None

    def _get_pypdf(self):
        if self._pypdf is None:
            try:
                import pypdf
                self._pypdf = pypdf
            except ImportError:
                logger.warning("pypdf not installed, PDF parsing unavailable")
                self._pypdf = False
        return self._pypdf

    def parse(self, file_path: Path) -> Generator[ParsedChunk, None, None]:
        pypdf = self._get_pypdf()
        if not pypdf:
            return

        try:
            with open(file_path, 'rb') as f:
                reader = pypdf.PdfReader(f)
                full_text = ""
                page_texts = []

                for page_num, page in enumerate(reader.pages):
                    try:
                        text = page.extract_text()
                        if text:
                            page_texts.append({
                                'page': page_num + 1,
                                'text': text,
                                'start': len(full_text)
                            })
                            full_text += text + "\n\n"
                    except Exception as e:
                        logger.warning(f"Failed to extract page {page_num + 1} from {file_path}: {e}")

                if not full_text.strip():
                    logger.warning(f"No text extracted from {file_path}")
                    return

                # Yield as single chunk with page metadata
                yield ParsedChunk(
                    content=full_text.strip(),
                    source_path=str(file_path),
                    chunk_index=0,
                    start_char=0,
                    end_char=len(full_text),
                    metadata={
                        'parser': 'pdf',
                        'pages': len(reader.pages),
                        'page_texts': page_texts,
                    }
                )

        except Exception as e:
            logger.error(f"Failed to parse PDF {file_path}: {e}")


class DocxParser(BaseParser):
    """Parse DOCX files using python-docx"""
    extensions = ['.docx']

    def __init__(self):
        self._docx = None

    def _get_docx(self):
        if self._docx is None:
            try:
                import docx
                self._docx = docx
            except ImportError:
                logger.warning("python-docx not installed, DOCX parsing unavailable")
                self._docx = False
        return self._docx

    def parse(self, file_path: Path) -> Generator[ParsedChunk, None, None]:
        docx = self._get_docx()
        if not docx:
            return

        try:
            doc = docx.Document(file_path)
            paragraphs = []

            for i, para in enumerate(doc.paragraphs):
                if para.text.strip():
                    paragraphs.append({
                        'index': i,
                        'text': para.text,
                        'style': para.style.name if para.style else 'Normal'
                    })

            full_text = "\n\n".join(p['text'] for p in paragraphs)

            if not full_text.strip():
                return

            yield ParsedChunk(
                content=full_text.strip(),
                source_path=str(file_path),
                chunk_index=0,
                start_char=0,
                end_char=len(full_text),
                metadata={
                    'parser': 'docx',
                    'paragraphs': len(paragraphs),
                    'paragraph_details': paragraphs,
                }
            )

        except Exception as e:
            logger.error(f"Failed to parse DOCX {file_path}: {e}")


class XLSXParser(BaseParser):
    """Parse XLSX files using openpyxl"""
    extensions = ['.xlsx']

    def __init__(self):
        self._openpyxl = None

    def _get_openpyxl(self):
        if self._openpyxl is None:
            try:
                import openpyxl
                self._openpyxl = openpyxl
            except ImportError:
                logger.warning("openpyxl not installed, XLSX parsing unavailable")
                self._openpyxl = False
        return self._openpyxl

    def parse(self, file_path: Path) -> Generator[ParsedChunk, None, None]:
        openpyxl = self._get_openpyxl()
        if not openpyxl:
            return

        try:
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            sheets_data = []

            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                rows = []
                for row in ws.iter_rows(values_only=True):
                    row_data = [str(cell) if cell is not None else '' for cell in row]
                    if any(cell.strip() for cell in row_data):
                        rows.append(row_data)

                if rows:
                    sheets_data.append({
                        'name': sheet_name,
                        'rows': rows,
                        'row_count': len(rows),
                        'col_count': max(len(r) for r in rows) if rows else 0,
                    })

            # Convert to text representation
            text_parts = []
            for sheet in sheets_data:
                text_parts.append(f"=== Sheet: {sheet['name']} ===")
                for row in sheet['rows']:
                    text_parts.append("\t".join(row))

            full_text = "\n".join(text_parts)

            if not full_text.strip():
                return

            yield ParsedChunk(
                content=full_text.strip(),
                source_path=str(file_path),
                chunk_index=0,
                start_char=0,
                end_char=len(full_text),
                metadata={
                    'parser': 'xlsx',
                    'sheets': sheets_data,
                }
            )

        except Exception as e:
            logger.error(f"Failed to parse XLSX {file_path}: {e}")


class ParserRegistry:
    """Registry of file parsers"""

    def __init__(self):
        self._parsers: List[BaseParser] = []
        self._ext_map: Dict[str, BaseParser] = {}

    def register(self, parser: BaseParser):
        self._parsers.append(parser)
        for ext in parser.extensions:
            self._ext_map[ext.lower()] = parser

    def get_parser(self, file_path: Path) -> Optional[BaseParser]:
        return self._ext_map.get(file_path.suffix.lower())

    def parse(self, file_path: Path) -> Generator[ParsedChunk, None, None]:
        parser = self.get_parser(file_path)
        if parser:
            yield from parser.parse(file_path)
        else:
            logger.warning(f"No parser for {file_path.suffix}: {file_path}")

    def get_supported_extensions(self) -> List[str]:
        return list(self._ext_map.keys())


# Global parser registry
parser_registry = ParserRegistry()

# Register default parsers
parser_registry.register(TextParser())
parser_registry.register(PDFParser())
parser_registry.register(DocxParser())
parser_registry.register(XLSXParser())