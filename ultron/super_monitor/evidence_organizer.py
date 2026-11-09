"""
ULTRON Super Monitor - Evidence Organizer
Manages evidence linking and organization for events.
"""

import asyncio
import logging
import hashlib
import json
import mimetypes
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, BinaryIO, Union
from pathlib import Path
from enum import Enum
import uuid

logger = logging.getLogger(__name__)

try:
    import magic
    MAGIC_AVAILABLE = True
except ImportError:
    magic = None
    MAGIC_AVAILABLE = False
    logging.getLogger(__name__).warning("python-magic not available, MIME type detection limited")

from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity


class EvidenceType(Enum):
    """Types of evidence."""
    DOCUMENT = "document"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    GEOSPATIAL = "geospatial"
    CODE = "code"
    LOG = "log"
    SCREENSHOT = "screenshot"
    DOCUMENT_SCAN = "document_scan"
    EMAIL = "email"
    CHAT = "chat"
    DATABASE = "database"
    OTHER = "other"


class EvidenceStatus(Enum):
    """Evidence processing status."""
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    ARCHIVED = "archived"
    DELETED = "deleted"


@dataclass
class Evidence:
    """A piece of evidence linked to events."""
    id: str
    event_ids: List[str] = field(default_factory=list)
    type: EvidenceType = EvidenceType.DOCUMENT
    status: EvidenceStatus = EvidenceStatus.PENDING
    title: str = ""
    description: str = ""
    source: str = ""
    file_path: Optional[str] = None
    file_size: int = 0
    mime_type: str = ""
    checksum: str = ""
    thumbnail_path: Optional[str] = None
    metadata: Dict = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    created: datetime = field(default_factory=datetime.now)
    updated: datetime = field(default_factory=datetime.now)
    processed_at: Optional[datetime] = None
    error: Optional[str] = None
    metadata: Dict = field(default_factory=dict)


class EvidenceOrganizer:
    """
    Manages evidence collection, processing, and organization.
    Supports multiple file types, automatic processing, and linking to events.
    """

    def __init__(self, 
                 storage_dir: Optional[Path] = None,
                 max_file_size: int = 100 * 1024 * 1024):  # 100MB
        self.storage_dir = config.resolve_path("evidence")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        
        self.max_file_size = max_file_size
        self._evidence: Dict[str, Evidence] = {}
        self._event_evidence_index: Dict[str, List[str]] = {}  # event_id -> evidence_ids
        self._type_index: Dict[EvidenceType, List[str]] = {}  # type -> evidence_ids
        self._status_index: Dict[str, List[str]] = {}  # status -> evidence_ids
        self._lock = asyncio.Lock()
        
        # Allowed MIME types by evidence type
        self.allowed_mimes = {
            EvidenceType.DOCUMENT: [
                "application/pdf",
                "application/msword",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/vnd.oasis.opendocument.text",
                "text/plain",
                "text/markdown",
            ],
            EvidenceType.IMAGE: [
                "image/jpeg",
                "image/png",
                "image/gif",
                "image/webp",
                "image/tiff",
                "image/bmp",
            ],
            EvidenceType.VIDEO: [
                "video/mp4",
                "video/webm",
                "video/quicktime",
                "video/x-msvideo",
            ],
            EvidenceType.AUDIO: [
                "audio/mpeg",
                "audio/wav",
                "audio/ogg",
                "audio/flac",
            ],
            EvidenceType.GEOSPATIAL: [
                "application/geo+json",
                "application/vnd.geo+json",
                "application/gpx+xml",
                "application/vnd.google-earth.kml+xml",
            ],
            EvidenceType.CODE: [
                "text/x-python",
                "text/x-java-source",
                "text/javascript",
                "application/json",
                "text/x-c",
                "text/x-c++",
            ],
        }

    async def add_evidence(self, 
                           event_ids: List[str],
                           file_path: Union[str, Path, BinaryIO],
                           title: str = "",
                           description: str = "",
                           source: str = "",
                           evidence_type: Optional[EvidenceType] = None,
                           tags: List[str] = None,
                           metadata: Dict = None) -> Optional[str]:
        """Add evidence from file or stream."""
        
        # Read file content
        if isinstance(file_path, (str, Path)):
            file_path = Path(file_path)
            if not file_path.exists():
                logger.error(f"File not found: {file_path}")
                return None
            
            file_size = file_path.stat().st_size
            if file_path.stat().st_size > self.max_file_size:
                logger.error(f"File too large: {file_path}")
                return None
            
            with open(file_path, 'rb') as f:
                content = file_path.read_bytes()
            mime_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            
        elif hasattr(file_path, 'read'):
            content = file_path.read()
            file_size = len(content)
            mime_type = mimetypes.guess_type(getattr(file_path, 'name', ''))[0] or "application/octet-stream"
        else:
            return None
        
        # Determine evidence type from MIME if not specified
        if evidence_type is None:
            evidence_type = self._guess_evidence_type(mime_type)
        
        # Check file size
        if len(content) > self.max_file_size:
            logger.error(f"File too large: {len(content)} bytes")
            return None
        
        # Calculate checksum
        checksum = hashlib.sha256(content).hexdigest()
        
        # Check for duplicates
        existing = self._find_by_checksum(checksum)
        if existing:
            # Link to existing evidence
            for event_id in existing.event_ids:
                if event_id not in self._event_evidence_index.get(evidence.id, []):
                    self._link_evidence_to_event(existing.id, event_id)
            return existing.id
        
        # Create evidence
        evidence_id = hashlib.sha256(f"{datetime.now()}{uuid.uuid4()}".encode()).hexdigest()[:16]
        
        # Save file
        file_name = f"{evidence_id}{Path(mime_type).suffix or ''}"
        file_path = self.storage_dir / file_name
        
        async with aiofiles.open(file_path, 'wb') as f:
            await f.write(content)
        
        # Create evidence object
        evidence = Evidence(
            id=evidence_id,
            event_ids=event_ids,
            type=evidence_type,
            title=title,
            description=description,
            source=source,
            file_path=str(file_path),
            file_size=len(content),
            mime_type=mime_type,
            checksum=checksum,
            tags=tags or [],
            metadata=metadata or {},
        )
        
        # Link to events
        for event_id in event_ids:
            self._link_evidence_to_event(evidence.id, event_id)
        
        # Index by type and status
        self._index_evidence(evidence)
        
        # Process asynchronously
        asyncio.create_task(self._process_evidence(evidence.id, content))
        
        return evidence_id

    def _guess_evidence_type(self, mime_type: str) -> EvidenceType:
        """Guess evidence type from MIME type."""
        for ev_type, mimes in self.allowed_mimes.items():
            if mime_type in mimes:
                return ev_type
        return EvidenceType.OTHER

    async def _process_evidence(self, evidence_id: str, content: bytes):
        """Process evidence asynchronously (OCR, thumbnails, metadata extraction)."""
        async with self._lock:
            evidence = self._evidence.get(evidence_id)
            if not evidence:
                return
            
            evidence.status = EvidenceStatus.PROCESSING
            
            try:
                # Extract metadata based on type
                if evidence.type == EvidenceType.IMAGE:
                    await self._process_image(evidence_id)
                elif evidence.type == EvidenceType.DOCUMENT:
                    await self._process_document(evidence_id)
                elif evidence.type == EvidenceType.VIDEO:
                    await self._process_video(evidence_id)
                elif evidence.type == EvidenceType.AUDIO:
                    await self._process_audio(evidence_id)
                elif evidence.type == EvidenceType.GEOSPATIAL:
                    await self._process_geospatial(evidence_id)
                elif evidence.type == EvidenceType.CODE:
                    await self._process_code(evidence_id)
                
                evidence.status = EvidenceStatus.PROCESSED
                evidence.processed_at = datetime.now()
                
            except Exception as e:
                logger.error(f"Evidence processing failed for {evidence_id}: {e}")
                evidence.status = EvidenceStatus.FAILED
                evidence.error = str(e)
            finally:
                evidence.updated = datetime.now()

    async def _process_image(self, evidence_id: str):
        """Process image evidence - extract EXIF, generate thumbnail."""
        evidence = self._evidence[evidence_id]
        file_path = Path(evidence.file_path)
        
        try:
            from PIL import Image
            from PIL.ExifTags import TAGS
            
            with Image.open(file_path) as img:
                # Generate thumbnail
                thumb_path = self.storage_dir / "thumbs" / f"{evidence.id}_thumb.jpg"
                thumb_path.parent.mkdir(exist_ok=True)
                
                thumb = img.copy()
                thumb.thumbnail((300, 300))
                thumb.save(thumb_path, "JPEG")
                evidence.thumbnail_path = str(thumb_path)
                
                # Extract EXIF
                exif_data = {}
                if hasattr(img, '_getexif') and img._getexif():
                    for tag_id, value in img._getexif().items():
                        tag = TAGS.get(tag_id, tag_id)
                        exif_data[tag] = value
                
                evidence.metadata["exif"] = exif_data
                evidence.metadata["dimensions"] = img.size
                evidence.metadata["format"] = img.format
                evidence.metadata["mode"] = img.mode
                
        except Exception as e:
            logger.warning(f"Image processing failed for {evidence.id}: {e}")

    async def _process_document(self, evidence_id: str):
        """Process document evidence - extract text, metadata."""
        evidence = self._evidence[evidence_id]
        file_path = Path(evidence.file_path)
        
        try:
            if evidence.mime_type == "application/pdf":
                import fitz  # PyMuPDF
                doc = fitz.open(file_path)
                evidence.metadata["pages"] = doc.page_count
                evidence.metadata["metadata"] = doc.metadata
                
                # Extract text from first few pages
                text = ""
                for page_num in range(min(5, doc.page_count)):
                    text += doc[page_num].get_text()
                evidence.metadata["preview_text"] = text[:5000]
                
            elif evidence.mime_type in ["application/msword", 
                                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]:
                import docx
                doc = docx.Document(file_path)
                text = "\n".join([p.text for p in doc.paragraphs])
                evidence.metadata["preview_text"] = text[:5000]
                
        except Exception as e:
            logger.warning(f"Document processing failed for {evidence.id}: {e}")

    async def _process_video(self, evidence_id: str):
        """Process video evidence - extract frames, metadata."""
        evidence = self._evidence[evidence_id]
        file_path = Path(evidence.file_path)
        
        try:
            import cv2
            cap = cv2.VideoCapture(str(file_path))
            
            evidence.metadata["duration"] = cap.get(cv2.CAP_PROP_FRAME_COUNT) / cap.get(cv2.CAP_PROP_FPS)
            evidence.metadata["fps"] = cap.get(cv2.CAP_PROP_FPS)
            evidence.metadata["frame_count"] = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            evidence.metadata["width"] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            evidence.metadata["height"] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            
            # Extract keyframes
            thumb_path = self.storage_dir / "thumbs" / f"{evidence.id}_thumb.jpg"
            thumb_path.parent.mkdir(exist_ok=True)
            
            cap.set(cv2.CAP_PROP_POS_FRAMES, cap.get(cv2.CAP_PROP_FRAME_COUNT) // 2)
            ret, frame = cap.read()
            if ret:
                cv2.imwrite(str(thumb_path), frame)
                evidence.thumbnail_path = str(thumb_path)
            
            cap.release()
            
        except Exception as e:
            logger.warning(f"Video processing failed for {evidence.id}: {e}")

    async def _process_audio(self, evidence_id: str):
        """Process audio evidence."""
        evidence = self._evidence[evidence_id]
        file_path = Path(evidence.file_path)
        
        try:
            import mutagen
            audio = mutagen.File(file_path)
            if audio:
                evidence.metadata["duration"] = audio.info.length
                evidence.metadata["bitrate"] = audio.info.bitrate
                evidence.metadata["sample_rate"] = audio.info.sample_rate
                evidence.metadata["channels"] = audio.info.channels
                if audio.tags:
                    evidence.metadata["tags"] = {k: str(v) for k, v in audio.tags.items()}
        except Exception as e:
            logger.warning(f"Audio processing failed for {evidence.id}: {e}")

    async def _process_geospatial(self, evidence_id: str):
        """Process geospatial data."""
        evidence = self._evidence[evidence_id]
        file_path = Path(evidence.file_path)
        
        try:
            import geopandas as gpd
            gdf = gpd.read_file(file_path)
            evidence.metadata["feature_count"] = len(gdf)
            evidence.metadata["crs"] = str(gdf.crs)
            evidence.metadata["bounds"] = gdf.total_bounds.tolist()
            evidence.metadata["geometry_types"] = gdf.geometry.type.unique().tolist()
            
            # Generate preview
            thumb_path = self.storage_dir / "thumbs" / f"{evidence.id}_map.png"
            thumb_path.parent.mkdir(exist_ok=True)
            gdf.plot(figsize=(6, 4))
            import matplotlib.pyplot as plt
            plt.savefig(thumb_path, dpi=100, bbox_inches='tight')
            plt.close()
            evidence.thumbnail_path = str(thumb_path)
            
        except Exception as e:
            logger.warning(f"Geospatial processing failed for {evidence.id}: {e}")

    async def _process_code(self, evidence_id: str):
        """Process code files."""
        evidence = self._evidence[evidence_id]
        file_path = Path(evidence.file_path)
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            evidence.metadata["lines"] = content.count('\n') + 1
            evidence.metadata["chars"] = len(content)
            
            # Detect language
            from pygments.lexers import guess_lexer
            from pygments.util import ClassNotFound
            try:
                lexer = guess_lexer(content)
                evidence.metadata["language"] = lexer.name
            except ClassNotFound:
                pass
            
            # Extract functions/classes (simple regex)
            import re
            functions = re.findall(r'^(def|class|function|fn)\s+(\w+)', content, re.MULTILINE)
            evidence.metadata["functions"] = [f[1] for f in functions[:20]]
            
        except Exception as e:
            logger.warning(f"Code processing failed for {evidence.id}: {e}")

    def _index_evidence(self, evidence: Evidence):
        """Index evidence for fast lookup."""
        self._evidence[evidence.id] = evidence
        
        # By type
        self._type_index.setdefault(evidence.type, []).append(evidence.id)
        
        # By status
        self._status_index.setdefault(evidence.status.value, []).append(evidence.id)
        
        # Link to events
        for event_id in evidence.event_ids:
            self._event_evidence_index.setdefault(event_id, []).append(evidence.id)

    def _link_evidence_to_event(self, evidence_id: str, event_id: str):
        """Link evidence to an event."""
        if event_id not in self._event_evidence_index:
            self._event_evidence_index[event_id] = []
        if evidence_id not in self._event_evidence_index[event_id]:
            self._event_evidence_index[event_id].append(evidence_id)
        
        # Also update evidence
        evidence = self._evidence.get(evidence_id)
        if evidence and event_id not in evidence.event_ids:
            evidence.event_ids.append(event_id)

    def _find_by_checksum(self, checksum: str) -> Optional[Evidence]:
        """Find evidence by checksum."""
        for ev in self._evidence.values():
            if ev.checksum == checksum:
                return ev
        return None

    async def get_evidence(self, evidence_id: str) -> Optional[Evidence]:
        """Get evidence by ID."""
        async with self._lock:
            return self._evidence.get(evidence_id)

    async def get_evidence_for_event(self, event_id: str) -> List[Evidence]:
        """Get all evidence for an event."""
        async with self._lock:
            evidence_ids = self._event_evidence_index.get(event_id, [])
            return [self._evidence[eid] for eid in evidence_ids if eid in self._evidence]

    async def get_evidence_by_type(self, evidence_type: EvidenceType) -> List[Evidence]:
        """Get all evidence of a specific type."""
        async with self._lock:
            ids = self._type_index.get(evidence_type, [])
            return [self._evidence[eid] for eid in ids if eid in self._evidence]

    async def get_evidence_by_status(self, status: EvidenceStatus) -> List[Evidence]:
        """Get all evidence with specific status."""
        async with self._lock:
            ids = self._status_index.get(status.value, [])
            return [self._evidence[eid] for eid in ids if eid in self._evidence]

    async def update_evidence(self, evidence_id: str, **kwargs) -> bool:
        """Update evidence metadata."""
        async with self._lock:
            evidence = self._evidence.get(evidence_id)
            if not evidence:
                return False
            
            for key, value in kwargs.items():
                if hasattr(evidence, key):
                    setattr(evidence, key, value)
            
            evidence.updated = datetime.now()
            return True

    async def delete_evidence(self, evidence_id: str, delete_file: bool = True) -> bool:
        """Delete evidence."""
        async with self._lock:
            evidence = self._evidence.get(evidence_id)
            if not evidence:
                return False
            
            # Remove from indexes
            self._type_index.get(evidence.type, []).remove(evidence.id)
            self._status_index.get(evidence.status.value, []).remove(evidence.id)
            
            for event_id in evidence.event_ids:
                if evidence.id in self._event_evidence_index.get(event_id, []):
                    self._event_evidence_index[event_id].remove(evidence.id)
            
            # Delete file if requested
            if delete_file and evidence.file_path:
                try:
                    Path(evidence.file_path).unlink(missing_ok=True)
                    if evidence.thumbnail_path:
                        Path(evidence.thumbnail_path).unlink(missing_ok=True)
                except Exception:
                    pass
            
            del self._evidence[evidence_id]
            return True

    async def search_evidence(self, 
                              query: str = "",
                              evidence_type: Optional[EvidenceType] = None,
                              status: Optional[EvidenceStatus] = None,
                              tags: List[str] = None,
                              event_id: Optional[str] = None,
                              limit: int = 50) -> List[Evidence]:
        """Search evidence."""
        async with self._lock:
            results = []
            
            for evidence in self._evidence.values():
                if evidence_type and evidence.type != evidence_type:
                    continue
                if status and evidence.status != status:
                    continue
                if tags and not all(t in evidence.tags for t in tags):
                    continue
                if event_id and event_id not in evidence.event_ids:
                    continue
                if query and query.lower() not in f"{evidence.title} {evidence.description} {' '.join(evidence.tags)}".lower():
                    continue
                
                results.append(evidence)
                if len(results) >= limit:
                    break
            
            return results

    def get_stats(self) -> Dict[str, Any]:
        """Get organizer statistics."""
        by_type = {}
        by_status = {}
        total_size = 0
        
        for ev in self._evidence.values():
            by_type[ev.type.value] = by_type.get(ev.type.value, 0) + 1
            by_status[ev.status.value] = by_status.get(ev.status.value, 0) + 1
            total_size += ev.file_size
        
        return {
            "total_evidence": len(self._evidence),
            "total_size_mb": total_size / (1024 * 1024),
            "by_type": by_type,
            "by_status": by_status,
            "pending_processing": len(self._status_index.get("pending", [])),
            "failed": len(self._status_index.get("failed", [])),
        }

    async def export_evidence_bundle(self, 
                                     evidence_ids: List[str],
                                     output_path: Path,
                                     include_files: bool = True) -> Path:
        """Export evidence bundle as ZIP."""
        import zipfile
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for evidence_id in evidence_ids:
                evidence = self._evidence.get(evidence_id)
                if not evidence:
                    continue
                
                # Add metadata
                metadata = {
                    "id": evidence.id,
                    "title": evidence.title,
                    "description": evidence.description,
                    "type": evidence.type.value,
                    "status": evidence.status.value,
                    "source": evidence.source,
                    "file_size": evidence.file_size,
                    "mime_type": evidence.mime_type,
                    "checksum": evidence.checksum,
                    "created": evidence.created.isoformat(),
                    "updated": evidence.updated.isoformat(),
                    "tags": evidence.tags,
                    "metadata": evidence.metadata,
                }
                zf.writestr(f"{evidence.id}/metadata.json", json.dumps(metadata, indent=2))
                
                if include_files and evidence.file_path:
                    file_path = Path(evidence.file_path)
                    if file_path.exists():
                        zf.write(file_path, f"{evidence.id}/{file_path.name}")
                
                if evidence.thumbnail_path:
                    thumb_path = Path(evidence.thumbnail_path)
                    if thumb_path.exists():
                        zf.write(thumb_path, f"{evidence.id}/thumbnail{thumb_path.suffix}")
        
        return output_path


# Global instance
_evidence_organizer: Optional[EvidenceOrganizer] = None


def get_evidence_organizer() -> EvidenceOrganizer:
    """Get or create the global Evidence Organizer instance."""
    global _evidence_organizer
    if _evidence_organizer is None:
        _evidence_organizer = EvidenceOrganizer()
    return _evidence_organizer