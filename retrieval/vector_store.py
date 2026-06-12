"""ChromaDB vector store for requirement-evidence retrieval."""
import logging
from typing import Any

import chromadb
from chromadb.config import Settings

from models.evidence import VendorEvidence, EvidenceType
from models.requirement import SourceRef
from retrieval.embeddings import embed_texts
from config import RETRIEVAL_TOP_K

logger = logging.getLogger(__name__)


class VendorVectorStore:
    """ChromaDB-backed vector store for vendor evidence retrieval.
    
    Creates one collection per vendor for isolated retrieval.
    """
    
    def __init__(self):
        self.client = chromadb.Client(Settings(anonymized_telemetry=False))
        self._collections: dict[str, Any] = {}
    
    def _get_collection(self, vendor_id: str):
        """Get or create a collection for a vendor."""
        if vendor_id not in self._collections:
            # Clean collection name (ChromaDB has naming rules)
            safe_name = vendor_id.replace(" ", "_").replace("-", "_")[:63]
            self._collections[vendor_id] = self.client.get_or_create_collection(
                name=safe_name,
                metadata={"hnsw:space": "cosine"}
            )
        return self._collections[vendor_id]
    
    def index_evidence(self, vendor_id: str, evidence_list: list[VendorEvidence]) -> int:
        """Index vendor evidence into the vector store.
        
        Args:
            vendor_id: Vendor identifier
            evidence_list: List of VendorEvidence objects to index
        
        Returns:
            Number of items indexed
        """
        if not evidence_list:
            return 0
        
        collection = self._get_collection(vendor_id)
        
        # Build documents and metadata
        documents = []
        metadatas = []
        ids = []
        
        for i, ev in enumerate(evidence_list):
            # Create a rich text representation for embedding
            doc_text = f"{ev.field}: {ev.value}"
            if ev.evidence_type:
                doc_text += f" (type: {ev.evidence_type.value})"
            if ev.valid_until:
                doc_text += f" (valid until: {ev.valid_until.isoformat()})"
            
            documents.append(doc_text)
            metadatas.append({
                "field": ev.field,
                "evidence_type": ev.evidence_type.value,
                "source_file": ev.source_ref.file,
                "source_page": ev.source_ref.page,
                "valid_until": ev.valid_until.isoformat() if ev.valid_until else "",
                "value_str": str(ev.value)
            })
            ids.append(f"{vendor_id}_{i}")
        
        # Generate embeddings
        embeddings = embed_texts(documents)
        
        # Add to collection
        collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas
        )
        
        logger.info(f"Indexed {len(documents)} evidence items for vendor '{vendor_id}'")
        return len(documents)
    
    def retrieve_relevant(
        self,
        vendor_id: str,
        query_text: str,
        top_k: int | None = None,
        evidence_type_filter: EvidenceType | None = None
    ) -> list[dict]:
        """Retrieve the most relevant evidence for a query.
        
        Args:
            vendor_id: Vendor to search
            query_text: Requirement text to match against
            top_k: Number of results to return
            evidence_type_filter: Optional filter by evidence type
        
        Returns:
            List of dicts with keys: document, metadata, distance
        """
        top_k = top_k or RETRIEVAL_TOP_K
        collection = self._get_collection(vendor_id)
        
        if collection.count() == 0:
            return []
        
        # Build query embedding
        query_embedding = embed_texts([query_text])[0]
        
        # Build where filter if needed
        where_filter = None
        if evidence_type_filter:
            where_filter = {"evidence_type": evidence_type_filter.value}
        
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, collection.count()),
            where=where_filter if where_filter else None
        )
        
        # Flatten results
        items = []
        if results and results['documents'] and results['documents'][0]:
            for j in range(len(results['documents'][0])):
                items.append({
                    "document": results['documents'][0][j],
                    "metadata": results['metadatas'][0][j] if results['metadatas'] else {},
                    "distance": results['distances'][0][j] if results['distances'] else None
                })
        
        return items
    
    def clear_vendor(self, vendor_id: str):
        """Delete all evidence for a vendor."""
        if vendor_id in self._collections:
            safe_name = vendor_id.replace(" ", "_").replace("-", "_")[:63]
            try:
                self.client.delete_collection(safe_name)
            except Exception:
                pass
            del self._collections[vendor_id]
    
    def clear_all(self):
        """Delete all collections."""
        for vid in list(self._collections.keys()):
            self.clear_vendor(vid)
