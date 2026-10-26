"""
memory.py
=========
Persistent memory using ChromaDB for candidate decisions and learning.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional


class CandidateMemory:
    """Store and retrieve candidate decisions with vector similarity."""
    
    def __init__(self, persist_dir: str = "./data/memory"):
        self.persist_dir = persist_dir
        os.makedirs(persist_dir, exist_ok=True)
        self._collection = None
        self._init_chroma()
    
    def _init_chroma(self):
        """Initialize ChromaDB if available."""
        try:
            import chromadb
            from chromadb.config import Settings
            self._client = chromadb.PersistentClient(path=self.persist_dir)
            self._collection = self._client.get_or_create_collection(
                name="candidate_decisions",
                metadata={"hnsw:space": "cosine"}
            )
        except Exception:
            # Fallback to simple JSON storage
            self._client = None
            self._collection = None
            self._fallback_file = os.path.join(self.persist_dir, "decisions.json")
            if os.path.exists(self._fallback_file):
                with open(self._fallback_file, 'r') as f:
                    self._fallback_decisions = json.load(f)
            else:
                self._fallback_decisions = []
    
    def remember_decision(self, email: str, decision: str, reason: str, 
                          timestamp: str = None, profile: Dict = None) -> None:
        """Store a decision about a candidate.
        
        Args:
            email: Candidate email
            decision: 'shortlisted', 'rejected', 'approved', etc.
            reason: Reason for decision
            timestamp: ISO timestamp
            profile: Candidate profile for similarity search
        """
        if timestamp is None:
            timestamp = datetime.utcnow().isoformat() + "Z"
        
        entry = {
            "id": email,
            "email": email,
            "decision": decision,
            "reason": reason,
            "timestamp": timestamp,
            "profile": profile or {},
        }
        
        if self._collection is not None:
            try:
                # Create document for embedding
                doc = f"{decision} {reason} {' '.join(profile.get('skills', [])) if profile else ''}"
                self._collection.upsert(
                    ids=[email],
                    documents=[doc],
                    metadatas=[{"decision": decision, "reason": reason, "timestamp": timestamp}]
                )
            except Exception:
                pass
        else:
            # Fallback
            self._fallback_decisions = [d for d in self._fallback_decisions if d.get("email") != email]
            self._fallback_decisions.append(entry)
            try:
                with open(self._fallback_file, 'w') as f:
                    json.dump(self._fallback_decisions, f, indent=2)
            except Exception:
                pass
    
    def get_previous_decision(self, email: str) -> Optional[Dict[str, Any]]:
        """Get previous decision for a candidate."""
        if self._collection is not None:
            try:
                result = self._collection.get(ids=[email])
                if result['ids']:
                    return {
                        "email": email,
                        "decision": result['metadatas'][0].get('decision'),
                        "reason": result['metadatas'][0].get('reason'),
                        "timestamp": result['metadatas'][0].get('timestamp'),
                    }
            except Exception:
                pass
        else:
            for d in self._fallback_decisions:
                if d.get("email") == email:
                    return d
        return None
    
    def search_similar(self, profile: Dict) -> List[Dict[str, Any]]:
        """Search for similar profiles and their decisions."""
        results = []
        if self._collection is not None:
            try:
                doc = ' '.join(profile.get('skills', []))
                res = self._collection.query(query_texts=[doc], n_results=5)
                if res['ids'] and res['ids'][0]:
                    for i, id_ in enumerate(res['ids'][0]):
                        results.append({
                            "email": id_,
                            "decision": res['metadatas'][0][i].get('decision'),
                            "reason": res['metadatas'][0][i].get('reason'),
                            "timestamp": res['metadatas'][0][i].get('timestamp'),
                            "distance": res['distances'][0][i] if res.get('distances') else 1.0,
                        })
            except Exception:
                pass
        else:
            # Simple fallback - just return all
            results = self._fallback_decisions[:5]
        return results


# Module-level singleton
candidate_memory = CandidateMemory()


__all__ = ["CandidateMemory", "candidate_memory"]
