import os
import shutil
import logging
from typing import List, Optional, Tuple
from datetime import datetime
from werkzeug.utils import secure_filename
from app.utils.db import get_db
from app.load_testing.models import TestDocumentSet, TestDocument

logger = logging.getLogger(__name__)

class DocumentSetManager:
    """Manages test document sets and file storage"""
    
    UPLOAD_FOLDER = 'tests/assets/documents'
    
    @classmethod
    def _get_storage_path(cls, set_id: int) -> str:
        return os.path.join(cls.UPLOAD_FOLDER, str(set_id))
    
    @classmethod
    def create_document_set(cls, name: str) -> TestDocumentSet:
        """Create a new empty document set"""
        db = get_db()
        try:
            doc_set = TestDocumentSet(name=name)
            db.add(doc_set)
            db.commit()
            
            # Create storage directory
            set_path = cls._get_storage_path(doc_set.id)
            os.makedirs(set_path, exist_ok=True)
            
            return doc_set
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to create document set: {str(e)}")
            raise e

    @classmethod
    def add_document(cls, set_id: int, file_obj) -> Tuple[Optional[TestDocument], Optional[str]]:
        """
        Add a document to a set.
        Returns (TestDocument, error_message)
        """
        db = get_db()
        try:
            doc_set = db.query(TestDocumentSet).get(set_id)
            if not doc_set:
                return None, "Document set not found"
            
            filename = secure_filename(file_obj.filename)
            if not filename:
                return None, "Invalid filename"
                
            set_path = cls._get_storage_path(set_id)
            file_path = os.path.join(set_path, filename)
            
            # Save file
            file_obj.save(file_path)
            
            # Create record
            doc = TestDocument(
                doc_set_id=set_id,
                filename=filename,
                file_path=file_path,
                file_size_bytes=os.path.getsize(file_path)
            )
            db.add(doc)
            db.commit()
            
            return doc, None
            
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to add document: {str(e)}")
            return None, str(e)

    @classmethod
    def delete_document_set(cls, set_id: int) -> bool:
        """Delete a document set and all its files"""
        db = get_db()
        try:
            doc_set = db.query(TestDocumentSet).get(set_id)
            if not doc_set:
                return False
                
            # Remove files from disk
            set_path = cls._get_storage_path(set_id)
            if os.path.exists(set_path):
                shutil.rmtree(set_path)
                
            # Database cascade should handle TestDocument records if configured,
            # but let's be explicit if needed. 
            # Models define cascade="all, delete-orphan", so deleting set is enough.
            db.delete(doc_set)
            db.commit()
            return True
            
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to delete document set {set_id}: {str(e)}")
            return False

    @classmethod
    def get_all_sets(cls) -> List[TestDocumentSet]:
        db = get_db()
        return db.query(TestDocumentSet).order_by(TestDocumentSet.created_at.desc()).all()

    @classmethod
    def get_set_details(cls, set_id: int) -> Optional[dict]:
        db = get_db()
        doc_set = db.query(TestDocumentSet).get(set_id)
        if not doc_set:
            return None
            
        return {
            'id': doc_set.id,
            'name': doc_set.name,
            'created_at': doc_set.created_at.isoformat(),
            'document_count': len(doc_set.documents),
            'documents': [{
                'id': d.id,
                'filename': d.filename,
                'size': d.file_size_bytes,
                'uploaded_at': d.uploaded_at.isoformat()
            } for d in doc_set.documents]
        }
