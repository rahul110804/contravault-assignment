"""Stage A: PDF ingestion with layout-aware extraction and OCR fallback."""
import logging
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
import json

import pymupdf  # PyMuPDF
import pymupdf4llm

from models.requirement import SourceRef
from utils.pdf_utils import is_page_scanned, clean_text, extract_vendor_id_from_filename
from config import INTERMEDIATES_DIR, OCR_LANGUAGES, OCR_DPI, SCANNED_TEXT_THRESHOLD

logger = logging.getLogger(__name__)


@dataclass
class PageContent:
    """Extracted content from a single PDF page."""
    file: str  # filename (basename)
    page_number: int  # 1-indexed
    text: str
    extraction_method: str  # 'native' or 'ocr'
    
    @property
    def source_ref(self) -> SourceRef:
        return SourceRef(file=self.file, page=self.page_number)
    
    @property
    def has_content(self) -> bool:
        return len(self.text.strip()) > 0


@dataclass  
class DocumentBundle:
    """All extracted pages from a document or set of documents."""
    document_id: str  # tender or vendor_id
    pages: list[PageContent] = field(default_factory=list)
    extraction_timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages if p.has_content)
    
    @property
    def page_count(self) -> int:
        return len(self.pages)
    
    def pages_from_file(self, filename: str) -> list[PageContent]:
        return [p for p in self.pages if p.file == filename]
    
    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "page_count": self.page_count,
            "extraction_timestamp": self.extraction_timestamp,
            "pages": [
                {
                    "file": p.file,
                    "page_number": p.page_number,
                    "text": p.text,
                    "extraction_method": p.extraction_method
                }
                for p in self.pages
            ]
        }
    
    def save_intermediate(self, path: Path | None = None) -> Path:
        if path is None:
            path = INTERMEDIATES_DIR / f"{self.document_id}_ingest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        logger.info(f"Saved intermediate: {path}")
        return path


def ingest_pdf(file_path: str | Path, file_bytes: bytes | None = None) -> list[PageContent]:
    """Extract text from a PDF file with OCR fallback for scanned pages.
    
    Args:
        file_path: Path to the PDF or filename if using file_bytes
        file_bytes: Optional raw PDF bytes (for Streamlit uploads)
    
    Returns:
        List of PageContent, one per page
    """
    file_path = Path(file_path)
    filename = file_path.name
    pages = []
    
    try:
        if file_bytes:
            doc = pymupdf.open(stream=file_bytes, filetype="pdf")
        else:
            doc = pymupdf.open(str(file_path))
        
        logger.info(f"Processing {filename}: {len(doc)} pages")
        
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            page_num = page_idx + 1  # 1-indexed
            
            # Try native text extraction first
            text = page.get_text(sort=True)
            
            if len(text.strip()) < SCANNED_TEXT_THRESHOLD:
                # Page appears scanned — try OCR
                logger.debug(f"{filename} p.{page_num}: native text too short ({len(text.strip())} chars), trying OCR")
                try:
                    tp = page.get_textpage_ocr(
                        dpi=OCR_DPI,
                        language=OCR_LANGUAGES,
                        full=True
                    )
                    text = page.get_text(sort=True, textpage=tp)
                    method = "ocr"
                except Exception as e:
                    logger.warning(f"{filename} p.{page_num}: OCR failed: {e}")
                    method = "ocr_failed"
            else:
                method = "native"
            
            text = clean_text(text)
            
            pages.append(PageContent(
                file=filename,
                page_number=page_num,
                text=text,
                extraction_method=method
            ))
        
        doc.close()
        logger.info(f"Extracted {len(pages)} pages from {filename} "
                    f"({sum(1 for p in pages if p.extraction_method == 'native')} native, "
                    f"{sum(1 for p in pages if p.extraction_method == 'ocr')} OCR)")
    
    except Exception as e:
        logger.error(f"Failed to process {filename}: {e}")
        raise
    
    return pages


def ingest_tender(file_path: str | Path, file_bytes: bytes | None = None) -> DocumentBundle:
    """Ingest a tender PDF and return a DocumentBundle."""
    pages = ingest_pdf(file_path, file_bytes)
    bundle = DocumentBundle(document_id="tender", pages=pages)
    bundle.save_intermediate()
    return bundle


def ingest_vendor_bundle(
    vendor_id: str,
    file_paths: list[str | Path] | None = None,
    file_bytes_list: list[tuple[str, bytes]] | None = None
) -> DocumentBundle:
    """Ingest one or more vendor PDFs into a single bundle.
    
    Args:
        vendor_id: Identifier for the vendor
        file_paths: List of file paths (for CLI usage)
        file_bytes_list: List of (filename, bytes) tuples (for Streamlit uploads)
    """
    all_pages = []
    
    if file_bytes_list:
        for filename, fbytes in file_bytes_list:
            pages = ingest_pdf(filename, fbytes)
            all_pages.extend(pages)
    elif file_paths:
        for fp in file_paths:
            pages = ingest_pdf(fp)
            all_pages.extend(pages)
    else:
        raise ValueError("Must provide either file_paths or file_bytes_list")
    
    bundle = DocumentBundle(document_id=vendor_id, pages=all_pages)
    bundle.save_intermediate()
    return bundle
