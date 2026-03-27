import os
import time
from groq import Groq
from langchain_nomic import NomicEmbeddings
from langchain_community.embeddings import HuggingFaceEmbeddings

from langchain_community.vectorstores import FAISS
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging
from app.utils.db import get_db
from app.config import Config
from app.models.database_models import (
    User as DBUser, Lesson as DBLesson, Conversation as DBConversation, 
    ChatHistory as DBChatHistory, SurveyResponse as DBSurveyResponse,
    LessonFAQ as DBLessonFAQ, LessonChatHistory as DBLessonChatHistory
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy import and_, or_, desc, func, String
from sqlalchemy.orm import joinedload
import pickle
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import httpx
from httpx import Timeout
from threading import Lock
import time
from collections import deque
import random
import re
import json

logger = logging.getLogger(__name__)
# Add this to your app's initialization code (before any Groq clients are created)
from groq import Groq
from functools import wraps

# model.py
from groq import Groq
from functools import wraps

original_init = Groq.__init__

@wraps(original_init)
def patched_init(self, *args, **kwargs):
    # Log the kwargs before removing 'proxies'
    logger.debug(f"Initializing Groq client with kwargs: {kwargs}")
    if 'proxies' in kwargs:
        logger.debug("Removing 'proxies' from kwargs before initializing Groq client")
        kwargs.pop('proxies')
    return original_init(self, *args, **kwargs)

Groq.__init__ = patched_init
logger.debug("Groq client patched to remove 'proxies' parameter")
class UserModel:
    """User model for handling user-related database operations"""
    
    def __init__(self, user_id: Optional[int] = None):
        self.user_id = user_id
    
    @staticmethod
    def _model_to_dict(model_instance):
        """Convert SQLAlchemy model instance to dictionary"""
        if model_instance is None:
            return None
        result = {}
        for key in model_instance.__table__.columns.keys():
            value = getattr(model_instance, key)
            # Convert datetime objects to ISO format strings
            if isinstance(value, datetime):
                value = value.isoformat()
            result[key] = value
        return result
    
    @staticmethod
    def create_user(username: str, useremail: str, password: str, 
                   class_standard: str, medium: str, groq_api_key: str = '', role: str = 'student') -> int:
        """Create a new user in the database"""
        try:
            db = get_db()
            user = DBUser(
                username=username,
                useremail=useremail,
                password=password,
                class_standard=class_standard,
                medium=medium,
                groq_api_key=groq_api_key or '',  # Default to empty string if not provided
                role=role,
                subscription_tier='free'  # Default to free tier
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            return user.id
        except IntegrityError as e:
            logger.error(f"User creation failed - integrity error: {str(e)}")
            db.rollback()
            raise ValueError("Username or email already exists")
        except Exception as e:
            logger.error(f"User creation failed: {str(e)}")
            db.rollback()
            raise

    @staticmethod
    def get_user_by_email(useremail: str) -> Dict[str, Any]:
        """Retrieve user details by email"""
        try:
            db = get_db()
            user = db.query(DBUser).filter(DBUser.useremail == useremail).first()
            return UserModel._model_to_dict(user)
        except Exception as e:
            logger.error(f"Error retrieving user by email: {str(e)}")
            raise

    @staticmethod
    def get_user_by_id(user_id: int) -> Dict[str, Any]:
        """Retrieve user details by ID"""
        try:
            db = get_db()
            user = db.query(DBUser).filter(DBUser.id == user_id).first()
            return UserModel._model_to_dict(user)
        except Exception as e:
            logger.error(f"Error retrieving user by ID: {str(e)}")
            raise

    def update_api_key(self, new_api_key: str) -> bool:
        """Update the user's API key"""
        try:
            db = get_db()
            user = db.query(DBUser).filter(DBUser.id == self.user_id).first()
            if user:
                user.groq_api_key = new_api_key
                db.commit()
                return True
            return False
        except Exception as e:
            logger.error(f"Error updating API key: {str(e)}")
            db.rollback()
            return False

    def get_role(self) -> str:
        """Get the user's role"""
        db = get_db()
        try:
            user = db.query(DBUser).filter(DBUser.id == self.user_id).first()
            return user.role if user else 'student'
        except Exception as e:
            logger.error(f"Error getting user role: {str(e)}")
            # Ensure the session is not left in an invalid transaction state
            try:
                db.rollback()
            except Exception as rollback_ex:
                logger.error(f"Error rolling back after role lookup failure: {rollback_ex}")
            return 'student'

    def is_teacher(self) -> bool:
        """Check if user is a teacher"""
        return self.get_role() == 'teacher'

    def is_student(self) -> bool:
        """Check if user is a student"""
        return self.get_role() == 'student'
    
    def is_admin(self) -> bool:
        """Check if user is an admin"""
        return self.get_role() == 'admin'


class LessonModel:
    """Lesson model for handling lesson-related database operations"""
    
    def __init__(self, lesson_id: Optional[int] = None):
        self.lesson_id = lesson_id
    
    @staticmethod
    def _lesson_to_dict(lesson_instance, include_teacher_name=False):
        """Convert SQLAlchemy lesson instance to dictionary"""
        if lesson_instance is None:
            return None
        result = {}
        for key in lesson_instance.__table__.columns.keys():
            value = getattr(lesson_instance, key)
            if isinstance(value, datetime):
                value = value.isoformat()
            result[key] = value
        # Add teacher_name if requested and available
        if include_teacher_name and hasattr(lesson_instance, 'teacher'):
            result['teacher_name'] = lesson_instance.teacher.username if lesson_instance.teacher else None
        return result
    
    @staticmethod
    def create_lesson(teacher_id: int, title: str, summary: str, learning_objectives: str,
                     focus_area: str, grade_level: str, content: str, file_name: str = None,
                     is_public: bool = True, parent_lesson_id: int = None, draft_content: str = None,
                     lesson_id: str = None, version_number: int = 1, parent_version_id: int = None,
                     original_content: str = None, status: str = "finalized", rag_thread_id: str = None) -> int:
        """Create a new lesson in the database"""
        try:
            logger.info(f"Saving lesson to database with title: '{title}'")
            db = get_db()
            
            # Generate lesson_id if not provided
            if not lesson_id:
                lesson_id = f"L{int(time.time() * 1000):06d}"  # Generate unique lesson_id
            
            # Set original_content if not provided
            if not original_content:
                original_content = content
            
            # If this is a new version of an existing lesson, get the next version number
            if parent_version_id:
                # Get the parent lesson's lesson_id and version_number
                parent_lesson = db.query(DBLesson).filter(DBLesson.id == parent_version_id).first()
                if parent_lesson:
                    lesson_id = parent_lesson.lesson_id  # Use same lesson_id as parent
                    
                    # Get the next available version number atomically
                    max_version = db.query(func.max(func.coalesce(DBLesson.version_number, 1))).filter(
                        DBLesson.lesson_id == lesson_id
                    ).scalar()
                    version_number = (max_version or 0) + 1
                    logger.info(f"Creating new version {version_number} for lesson {lesson_id}")
            
            # Retry logic for version number conflicts
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    lesson = DBLesson(
                        teacher_id=teacher_id,
                        title=title,
                        summary=summary,
                        learning_objectives=learning_objectives,
                        focus_area=focus_area,
                        grade_level=grade_level,
                        content=content,
                        file_name=file_name,
                        is_public=is_public,
                        parent_lesson_id=parent_lesson_id,
                        version=1,
                        draft_content=draft_content,
                        lesson_id=lesson_id,
                        version_number=version_number,
                        parent_version_id=parent_version_id,
                        original_content=original_content,
                        status=status,
                        rag_thread_id=rag_thread_id,
                    )
                    db.add(lesson)
                    db.commit()
                    db.refresh(lesson)
                    return lesson.id
                except IntegrityError as e:
                    error_msg = str(e.orig) if hasattr(e, 'orig') else str(e)
                    if "UNIQUE constraint failed" in error_msg or "unique constraint" in error_msg.lower():
                        # Version number conflict, get next available number
                        if attempt < max_retries - 1:
                            db.rollback()
                            # Get the next available version number
                            max_version = db.query(func.max(func.coalesce(DBLesson.version_number, 1))).filter(
                                DBLesson.lesson_id == lesson_id
                            ).scalar()
                            version_number = (max_version or 0) + 1
                            logger.warning(f"Version conflict detected, retrying with version {version_number}")
                            continue
                        else:
                            db.rollback()
                            raise e
                    else:
                        db.rollback()
                        raise e
        except Exception as e:
            logger.error(f"Error creating lesson: {str(e)}")
            db.rollback()
            raise

    @staticmethod
    def get_lesson_by_id(lesson_id: int) -> Dict[str, Any]:
        """Retrieve lesson details by ID"""
        try:
            db = get_db()
            lesson = db.query(DBLesson).join(DBUser).filter(DBLesson.id == lesson_id).first()
            if lesson:
                lesson_dict = LessonModel._lesson_to_dict(lesson, include_teacher_name=True)
                logger.info(f"Retrieved lesson ID {lesson_id} with title: '{lesson_dict.get('title', 'NO_TITLE')}'")
                return lesson_dict
            else:
                logger.warning(f"No lesson found with ID: {lesson_id}")
                return None
        except Exception as e:
            logger.error(f"Error retrieving lesson by ID: {str(e)}")
            raise

    @staticmethod
    def get_lessons_by_lesson_id(lesson_id: str) -> List[Dict[str, Any]]:
        """Get all versions of a lesson by lesson_id"""
        try:
            db = get_db()
            results = db.query(DBLesson).filter(DBLesson.lesson_id == lesson_id).order_by(DBLesson.version_number.asc()).all()
            return [LessonModel._lesson_to_dict(lesson) for lesson in results]
        except Exception as e:
            logger.error(f"Error getting lessons by lesson_id: {str(e)}")
            return []

    @staticmethod
    def get_latest_version_by_lesson_id(lesson_id: str) -> Optional[Dict[str, Any]]:
        """Get the latest version of a lesson by lesson_id"""
        try:
            db = get_db()
            result = db.query(DBLesson).filter(DBLesson.lesson_id == lesson_id).order_by(desc(DBLesson.version_number)).first()
            return LessonModel._lesson_to_dict(result) if result else None
        except Exception as e:
            logger.error(f"Error getting latest version by lesson_id: {str(e)}")
            return None

    @staticmethod
    def get_lessons_by_teacher(teacher_id: int) -> List[Dict[str, Any]]:
        """Get all lessons created by a specific teacher (including all versions)"""
        try:
            db = get_db()
            # Get all lessons for this teacher (originals and all versions)
            lessons = db.query(DBLesson).filter(DBLesson.teacher_id == teacher_id).order_by(desc(DBLesson.created_at)).all()
            return [LessonModel._lesson_to_dict(lesson) for lesson in lessons]
        except Exception as e:
            logger.error(f"Error retrieving lessons by teacher: {str(e)}")
            raise

    @staticmethod
    def get_lessons_by_teacher_paginated(
        teacher_id: int,
        page: int = 1,
        per_page: int = 10,
        search_term: str = None
    ) -> Dict[str, Any]:
        """Get teacher lessons with DB-level pagination."""
        try:
            db = get_db()
            page = max(1, int(page or 1))
            per_page = max(1, min(100, int(per_page or 10)))

            query = db.query(DBLesson).filter(DBLesson.teacher_id == teacher_id)

            if search_term:
                term = f"%{search_term.strip()}%"
                query = query.filter(
                    or_(
                        DBLesson.title.ilike(term),
                        DBLesson.focus_area.ilike(term),
                        DBLesson.grade_level.ilike(term),
                    )
                )

            total = query.count()
            lessons = (
                query
                .order_by(desc(DBLesson.created_at))
                .offset((page - 1) * per_page)
                .limit(per_page)
                .all()
            )
            total_pages = (total + per_page - 1) // per_page if total > 0 else 1

            return {
                "lessons": [LessonModel._lesson_to_dict(lesson) for lesson in lessons],
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": total_pages,
            }
        except Exception as e:
            logger.error(f"Error retrieving paginated lessons by teacher: {str(e)}")
            raise

    @staticmethod
    def get_public_lessons(grade_level: str = None, focus_area: str = None) -> List[Dict[str, Any]]:
        """Get all public lessons with optional filtering (including all versions)"""
        try:
            db = get_db()
            query = db.query(DBLesson).join(DBUser).filter(DBLesson.is_public == True)
            
            if grade_level:
                query = query.filter(DBLesson.grade_level == grade_level)
            
            if focus_area:
                query = query.filter(DBLesson.focus_area == focus_area)
            
            lessons = query.order_by(desc(DBLesson.created_at)).all()
            result = []
            for lesson in lessons:
                lesson_dict = LessonModel._lesson_to_dict(lesson)
                lesson_dict['teacher_name'] = lesson.teacher.username if lesson.teacher else None
                result.append(lesson_dict)
            return result
        except Exception as e:
            logger.error(f"Error retrieving public lessons: {str(e)}")
            raise

    @staticmethod
    def get_public_latest_lessons(grade_level: str = None, focus_area: str = None) -> List[Dict[str, Any]]:
        """Get only the latest public version per logical lesson (grouped by lesson_id)"""
        try:
            db = get_db()
            lessons_query = db.query(DBLesson).options(
                joinedload(DBLesson.teacher)
            ).filter(DBLesson.is_public.is_(True))
            
            if grade_level:
                lessons_query = lessons_query.filter(DBLesson.grade_level == grade_level)
            
            if focus_area:
                lessons_query = lessons_query.filter(DBLesson.focus_area == focus_area)
            
            # Order by created_at descending so the first occurrence of a lesson_id is the latest version
            lessons = lessons_query.order_by(DBLesson.created_at.desc()).all()
            
            latest_version_map = {}
            unversioned_lessons = []
            
            for lesson in lessons:
                lesson_dict = LessonModel._lesson_to_dict(lesson, include_teacher_name=True)
                logical_id = (lesson.lesson_id or '').strip()
                
                if logical_id:
                    if logical_id not in latest_version_map:
                        latest_version_map[logical_id] = (lesson.created_at, lesson_dict)
                else:
                    unversioned_lessons.append((lesson.created_at, lesson_dict))
            
            combined = unversioned_lessons + list(latest_version_map.values())
            combined.sort(key=lambda x: x[0] or datetime.min, reverse=True)
            
            return [entry[1] for entry in combined]
        except Exception as e:
            logger.error(f"Error retrieving latest public lessons: {str(e)}")
            raise

    @staticmethod
    def get_public_latest_lessons_paginated(
        grade_level: str = None,
        focus_area: str = None,
        page: int = 1,
        per_page: int = 10,
        search_term: str = None
    ) -> Dict[str, Any]:
        """Get latest public lessons with DB-level pagination (one row per logical lesson)."""
        try:
            db = get_db()
            page = max(1, int(page or 1))
            per_page = max(1, min(100, int(per_page or 10)))

            group_key = func.coalesce(DBLesson.lesson_id, func.cast(DBLesson.id, String))
            filtered = db.query(DBLesson).filter(DBLesson.is_public.is_(True))

            if grade_level:
                filtered = filtered.filter(DBLesson.grade_level == grade_level)
            if focus_area:
                filtered = filtered.filter(DBLesson.focus_area == focus_area)
            if search_term:
                term = f"%{search_term.strip()}%"
                filtered = filtered.filter(
                    or_(
                        DBLesson.title.ilike(term),
                        DBLesson.focus_area.ilike(term),
                        DBLesson.grade_level.ilike(term),
                    )
                )

            latest_per_lesson = (
                filtered
                .with_entities(
                    group_key.label("group_key"),
                    func.max(DBLesson.created_at).label("max_created_at")
                )
                .group_by(group_key)
                .subquery()
            )

            latest_query = (
                db.query(DBLesson)
                .options(joinedload(DBLesson.teacher))
                .join(
                    latest_per_lesson,
                    and_(
                        func.coalesce(DBLesson.lesson_id, func.cast(DBLesson.id, String)) == latest_per_lesson.c.group_key,
                        DBLesson.created_at == latest_per_lesson.c.max_created_at,
                    ),
                )
                .order_by(DBLesson.created_at.desc(), DBLesson.id.desc())
            )

            total = db.query(func.count()).select_from(latest_per_lesson).scalar() or 0
            lessons = (
                latest_query
                .offset((page - 1) * per_page)
                .limit(per_page)
                .all()
            )
            total_pages = (total + per_page - 1) // per_page if total > 0 else 1

            return {
                "lessons": [LessonModel._lesson_to_dict(lesson, include_teacher_name=True) for lesson in lessons],
                "page": page,
                "per_page": per_page,
                "total": int(total),
                "total_pages": total_pages,
            }
        except Exception as e:
            logger.error(f"Error retrieving paginated latest public lessons: {str(e)}")
            raise

    @staticmethod
    def search_lessons(search_term: str, grade_level: str = None) -> List[Dict[str, Any]]:
        """Search lessons by title, content, or focus area (including all versions)"""
        try:
            db = get_db()
            lessons_query = db.query(DBLesson).options(
                joinedload(DBLesson.teacher)
            ).filter(
                DBLesson.is_public.is_(True),
                or_(
                    DBLesson.title.ilike(f'%{search_term}%'),
                    DBLesson.content.ilike(f'%{search_term}%'),
                    DBLesson.focus_area.ilike(f'%{search_term}%')
                )
            )
            
            if grade_level:
                lessons_query = lessons_query.filter(DBLesson.grade_level == grade_level)
            
            lessons = lessons_query.order_by(DBLesson.created_at.desc()).all()
            return [
                LessonModel._lesson_to_dict(lesson, include_teacher_name=True)
                for lesson in lessons
            ]
        except Exception as e:
            logger.error(f"Error searching lessons: {str(e)}")
            raise

    def update_lesson(self, title: str = None, summary: str = None, learning_objectives: str = None,
                     focus_area: str = None, grade_level: str = None, content: str = None,
                     is_public: bool = None, detailed_answer: str = None, rag_thread_id: str = None) -> bool:
        """Update lesson details"""
        try:
            db = get_db()
            lesson = db.query(DBLesson).filter(DBLesson.id == self.lesson_id).first()
            if not lesson:
                return False
            
            if title is not None:
                lesson.title = title
            if summary is not None:
                lesson.summary = summary
            if learning_objectives is not None:
                lesson.learning_objectives = learning_objectives
            if focus_area is not None:
                lesson.focus_area = focus_area
            if grade_level is not None:
                lesson.grade_level = grade_level
            if content is not None:
                lesson.content = content
            if detailed_answer is not None:
                lesson.detailed_answer = detailed_answer
            if is_public is not None:
                lesson.is_public = is_public
            if rag_thread_id is not None:
                lesson.rag_thread_id = rag_thread_id
            
            lesson.updated_at = datetime.utcnow()
            db.commit()
            return True
        except Exception as e:
            logger.error(f"Error updating lesson: {str(e)}")
            db.rollback()
            return False

    def delete_lesson(self) -> bool:
        """Delete a lesson"""
        try:
            db = get_db()
            lesson = db.query(DBLesson).filter(DBLesson.id == self.lesson_id).first()
            if lesson:
                db.delete(lesson)
                db.commit()
                return True
            return False
        except Exception as e:
            logger.error(f"Error deleting lesson: {str(e)}")
            db.rollback()
            return False

    @staticmethod
    def create_new_version(original_lesson_id: int, teacher_id: int, title: str, summary: str, 
                          learning_objectives: str, focus_area: str, grade_level: str, 
                          content: str, file_name: str = None, is_public: bool = True) -> int:
        """Create a new version of an existing lesson"""
        try:
            logger.info(f"Creating new version of lesson {original_lesson_id} with title: '{title}'")
            logger.info(f"DEBUG: create_new_version - content length: {len(content)}")
            logger.info(f"DEBUG: create_new_version - content preview: {content[:100]}...")
            
            # Get the source lesson we are branching from (could be root or a child)
            original_lesson = LessonModel.get_lesson_by_id(original_lesson_id)
            if not original_lesson:
                raise ValueError(f"Original lesson {original_lesson_id} not found")
            
            # Determine the root/original lesson id for grouping (parent_lesson_id should always point to root)
            root_lesson_id = original_lesson.get('parent_lesson_id') or original_lesson_id
            root_lesson = LessonModel.get_lesson_by_id(root_lesson_id)
            
            new_id = LessonModel.create_lesson(
                teacher_id=teacher_id,
                title=title,
                summary=summary,
                learning_objectives=learning_objectives,
                focus_area=focus_area,
                grade_level=grade_level,
                content=content,
                file_name=file_name,
                is_public=is_public,
                parent_lesson_id=root_lesson_id,
                draft_content=None,  # New version starts with empty draft
                lesson_id=root_lesson['lesson_id'],  # Use same lesson_id as root
                parent_version_id=original_lesson_id  # Set parent version (the one we branched from)
            )

            # Mark the specific source version (original_lesson_id) as having a child version
            try:
                db = get_db()
                lesson = db.query(DBLesson).filter(DBLesson.id == original_lesson_id).first()
                if lesson:
                    lesson.has_child_version = True
                    lesson.updated_at = datetime.utcnow()
                # Also mark the root/original lesson as having a child (for UI consistency), if different
                root_id = original_lesson.get('parent_lesson_id') or original_lesson_id
                if root_id != original_lesson_id:
                    root_lesson = db.query(DBLesson).filter(DBLesson.id == root_id).first()
                    if root_lesson:
                        root_lesson.has_child_version = True
                        root_lesson.updated_at = datetime.utcnow()
                db.commit()
            except Exception as flag_err:
                logger.warning(f"Failed to set has_child_version flag for lesson {original_lesson_id}: {flag_err}")
                db.rollback()

            return new_id
        except Exception as e:
            logger.error(f"Error creating new version: {str(e)}")
            raise

    @staticmethod
    def get_lesson_versions(lesson_id: int) -> List[Dict[str, Any]]:
        """Get all versions of a lesson (including the original and all versions)"""
        try:
            db = get_db()
            lessons = (
                db.query(DBLesson)
                .filter(
                    or_(
                        DBLesson.id == lesson_id,
                        DBLesson.parent_lesson_id == lesson_id
                    )
                )
                .order_by(DBLesson.version.asc(), DBLesson.id.asc())
                .all()
            )
            
            result = []
            for lesson in lessons:
                lesson_dict = LessonModel._lesson_to_dict(lesson, include_teacher_name=True)
                
                if lesson_dict['id'] == lesson_id and lesson_dict.get('parent_lesson_id') is None:
                    lesson_dict['version'] = 1
                    lesson_dict['is_original'] = True
                else:
                    lesson_dict['is_original'] = False
                
                result.append(lesson_dict)
            
            logger.info(f"get_lesson_versions for lesson {lesson_id}: found {len(result)} versions")
            logger.info(f"Versions data: {result}")
            return result
        except Exception as e:
            logger.error(f"Error retrieving lesson versions: {str(e)}")
            raise

    @staticmethod
    def get_latest_version(lesson_id: int) -> Dict[str, Any]:
        """Get the latest version of a lesson"""
        try:
            db = get_db()
            lesson = (
                db.query(DBLesson)
                .options(joinedload(DBLesson.teacher))
                .filter(
                    or_(
                        DBLesson.id == lesson_id,
                        DBLesson.parent_lesson_id == lesson_id
                    )
                )
                .order_by(DBLesson.version.desc())
                .first()
            )
            return LessonModel._lesson_to_dict(lesson, include_teacher_name=True) if lesson else None
        except Exception as e:
            logger.error(f"Error retrieving latest version: {str(e)}")
            raise

    @staticmethod
    def check_title_exists(teacher_id: int, title: str, exclude_lesson_id: int = None) -> bool:
        """Check if a lesson title already exists for a teacher"""
        try:
            db = get_db()
            query = db.query(DBLesson).filter(
                and_(
                    DBLesson.teacher_id == teacher_id,
                    func.lower(DBLesson.title) == func.lower(title.strip())
                )
            )
            
            # Exclude a specific lesson ID (useful for updates/edits)
            if exclude_lesson_id:
                query = query.filter(DBLesson.id != exclude_lesson_id)
            
            return query.count() > 0
        except Exception as e:
            logger.error(f"Error checking title existence: {str(e)}")
            raise

    @staticmethod
    def save_draft_content(lesson_id: int, draft_content: str) -> bool:
        """Save draft content for a lesson"""
        try:
            db = get_db()
            lesson = db.query(DBLesson).filter(DBLesson.id == lesson_id).first()
            if lesson:
                lesson.draft_content = draft_content
                db.commit()
                logger.info(f"Saved draft content for lesson {lesson_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Error saving draft content: {str(e)}")
            db.rollback()
            return False

    @staticmethod
    def get_draft_content(lesson_id: int) -> str:
        """Get draft content for a lesson"""
        try:
            db = get_db()
            lesson = db.query(DBLesson).filter(DBLesson.id == lesson_id).first()
            return lesson.draft_content if lesson and lesson.draft_content else ''
        except Exception as e:
            logger.error(f"Error getting draft content: {str(e)}")
            return ''

    @staticmethod
    def clear_draft_content(lesson_id: int) -> bool:
        """Clear draft content for a lesson"""
        try:
            db = get_db()
            lesson = db.query(DBLesson).filter(DBLesson.id == lesson_id).first()
            if lesson:
                lesson.draft_content = None
                db.commit()
                logger.info(f"Cleared draft content for lesson {lesson_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Error clearing draft content: {str(e)}")
            db.rollback()
            return False

    @staticmethod
    def create_new_version_from_draft(original_lesson_id: int, teacher_id: int, title: str, summary: str, 
                                     learning_objectives: str, focus_area: str, grade_level: str, 
                                     draft_content: str, file_name: str = None, is_public: bool = True) -> int:
        """Create a new version of a lesson using draft content"""
        try:
            logger.info(f"Creating new version from draft for lesson {original_lesson_id}")
            
            # Get the original lesson to get its lesson_id
            original_lesson = LessonModel.get_lesson_by_id(original_lesson_id)
            if not original_lesson:
                raise ValueError(f"Original lesson {original_lesson_id} not found")
            
            # Determine the actual original lesson ID (root of the hierarchy)
            actual_original_id = original_lesson.get('parent_lesson_id') or original_lesson_id
            actual_original_lesson = LessonModel.get_lesson_by_id(actual_original_id)
            
            # Create new version with draft content as the main content; copy RAG thread for "Ask Question"
            rag_thread_id = original_lesson.get('rag_thread_id') or actual_original_lesson.get('rag_thread_id')
            new_lesson_id = LessonModel.create_lesson(
                teacher_id=teacher_id,
                title=title,
                summary=summary,
                learning_objectives=learning_objectives,
                focus_area=focus_area,
                grade_level=grade_level,
                content=draft_content,  # Draft content becomes the new version's content
                file_name=file_name,
                is_public=is_public,
                parent_lesson_id=actual_original_id,  # Always point to the root lesson
                draft_content=None,  # New version starts with empty draft
                lesson_id=actual_original_lesson['lesson_id'],  # Use same lesson_id as root
                parent_version_id=original_lesson_id,  # Set parent version (the one we're creating from)
                original_content=draft_content,  # Draft content becomes original content for new version
                status="finalized",
                rag_thread_id=rag_thread_id,
            )
            # Mark the source version as having a child version
            try:
                db = get_db()
                lesson = db.query(DBLesson).filter(DBLesson.id == original_lesson_id).first()
                if lesson:
                    lesson.has_child_version = True
                    lesson.updated_at = datetime.utcnow()
                # Also mark the root lesson as having a child
                if actual_original_id != original_lesson_id:
                    root_lesson = db.query(DBLesson).filter(DBLesson.id == actual_original_id).first()
                    if root_lesson:
                        root_lesson.has_child_version = True
                        root_lesson.updated_at = datetime.utcnow()
                db.commit()
            except Exception as flag_err:
                logger.warning(f"Failed to set has_child_version flag for lesson {original_lesson_id}: {flag_err}")
                db.rollback()
            return new_lesson_id
        except Exception as e:
            logger.error(f"Error creating new version from draft: {str(e)}")
            raise

# app/models/models.py (ChatModel part)
# from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from app.utils.llm_factory import create_llm

from typing import Optional, List, Dict, Any
import logging
import time

logger = logging.getLogger(__name__)

class TokenBucket:
    """Token bucket rate limiter implementation"""
    def __init__(self, rate: float, capacity: int):
        self.rate = rate  # tokens per second
        self.capacity = capacity  # maximum tokens
        self.tokens = capacity  # current tokens
        self.last_update = time.time()
        self.lock = Lock()
        self.request_history = deque(maxlen=100)  # Track last 100 requests
        self.error_count = 0
        self.last_error_time = 0
        self.daily_limit_hit = False
        self.daily_limit_reset_time = 0

    def acquire(self, tokens: int = 1) -> bool:
        """Acquire tokens from the bucket"""
        with self.lock:
            now = time.time()
            
            # Check if we're in daily limit cooldown
            if self.daily_limit_hit and now < self.daily_limit_reset_time:
                return False
            
            # Add new tokens based on time passed
            time_passed = now - self.last_update
            new_tokens = time_passed * self.rate
            
            # If we've had recent errors, reduce the rate by 25% instead of 50%
            if self.error_count > 0 and now - self.last_error_time < 60:
                new_tokens *= 0.75  # Reduce rate by 25% after errors
            
            self.tokens = min(self.capacity, self.tokens + new_tokens)
            self.last_update = now

            if self.tokens >= tokens:
                self.tokens -= tokens
                self.request_history.append(now)
                return True
            return False

    def record_error(self, error_data: Optional[Dict] = None):
        """Record an error occurrence"""
        with self.lock:
            self.error_count += 1
            self.last_error_time = time.time()
            
            # Handle daily limit error
            if error_data and isinstance(error_data, dict):
                if error_data.get('code') == 'rate_limit_exceeded' and error_data.get('type') == 'tokens':
                    self.daily_limit_hit = True
                    # Parse the reset time from the error message
                    try:
                        reset_time_str = error_data.get('message', '').split('try again in ')[1].split('.')[0]
                        minutes, seconds = map(int, reset_time_str.split('m'))
                        self.daily_limit_reset_time = time.time() + (minutes * 60) + seconds
                    except:
                        # Default to 1 hour if parsing fails
                        self.daily_limit_reset_time = time.time() + 3600
            
            # Clear error count after 5 minutes
            if time.time() - self.last_error_time > 300:
                self.error_count = 0
                self.daily_limit_hit = False

# class ChatModel:
#     """Model for handling chat-related operations"""
    
#     def __init__(self, api_key: str, user_id: int = None):
#         self.api_key = api_key
#         self.user_id = user_id
#         self._chat_model = None
#         self.vector_store = VectorStoreModel(user_id=user_id)
#         # Optimize timeout settings for faster responses
#         self.timeout = Timeout(
#             timeout=15.0,  # Reduced from 20.0
#             connect=3.0,   # Reduced from 5.0
#             read=10.0,     # Reduced from 15.0
#             write=3.0      # Reduced from 5.0
#         )
#         # Increase rate limiter capacity and rate
#         self.rate_limiter = TokenBucket(rate=10/60, capacity=10)  # Increased from 5/60 and capacity 5
#         self.last_request_time = 0
#         self.min_request_interval = 0.5  # Reduced from 1.0 seconds
#         self.daily_limit = 100000  # Daily token limit
#         self.used_tokens = 0  # Track used tokens
#         self.requested_tokens = 0  # Track requested tokens
    
#     @property
#     def chat_model(self):
#         """Lazy initialization of chat model"""
#         if not self._chat_model:
#             try:
#                 self._chat_model = ChatGroq(
#                     api_key=self.api_key,
#                     model_name="llama-3.3-70b-versatile",
#                     timeout=self.timeout,
#                     max_retries=3,
                  

#                 )
#             except Exception as e:
#                 logger.error(f"Failed to initialize chat model: {str(e)}")
#                 raise
#         return self._chat_model

#     def _wait_for_token(self):
#         """Wait for a token to become available with jitter"""
#         while not self.rate_limiter.acquire():
#             # Add random jitter to prevent thundering herd
#             jitter = random.uniform(0.1, 0.5)
#             time.sleep(jitter)
            
#             # Ensure minimum interval between requests
#             now = time.time()
#             if now - self.last_request_time < self.min_request_interval:
#                 time.sleep(self.min_request_interval - (now - self.last_request_time))

#     def _handle_error(self, error: Exception) -> Dict:
#         """Handle errors and update rate limiter state"""
#         error_info = {
#             'error': str(error),
#             'retry_after': None,
#             'is_daily_limit': False
#         }
        
#         if isinstance(error, httpx.HTTPStatusError):
#             try:
#                 error_data = error.response.json()
#                 self.rate_limiter.record_error(error_data.get('error', {}))
                
#                 if error.response.status_code == 429:
#                     if error_data.get('error', {}).get('type') == 'tokens':
#                         error_info['is_daily_limit'] = True
#                         error_info['retry_after'] = self.rate_limiter.daily_limit_reset_time - time.time()
#                         logger.warning(f"Daily token limit reached. Reset in {error_info['retry_after']} seconds")
#                     else:
#                         logger.warning("Rate limit hit, increasing delay")
#                         time.sleep(10)
#                 elif error.response.status_code >= 500:
#                     logger.warning("Server error, adding delay")
#                     time.sleep(5)
#             except:
#                 logger.error("Error parsing error response")
        
#         elif isinstance(error, httpx.TimeoutException):
#             logger.warning("Request timeout, adding delay")
#             time.sleep(3)
        
#         return error_info

#     @retry(
#         stop=stop_after_attempt(3),  # Increased from 2
#         wait=wait_exponential(multiplier=0.5, min=1, max=5),  # More aggressive retry
#         retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException)),
#         reraise=True
#     )
#     def generate_response(self, input_text: str, 
#                          system_prompt: Optional[str] = None, 
#                          chat_history: Optional[List[Dict]] = None) -> str:
#         """Generate a response using the chat model with vector store context"""
#         try:
#             # Wait for rate limiter token with reduced jitter
#             self._wait_for_token()
#             self.last_request_time = time.time()
            
#             # Initialize messages list
#             messages = []
            
#             # Add system prompt if provided
#             if system_prompt:
#                 messages.append({
#                     "role": "system",
#                     "content": system_prompt
#                 })
            
#             # Add formatted chat history if provided
#             if chat_history:
#                 # Ensure chat history is properly formatted
#                 for msg in chat_history:
#                     if 'role' in msg and 'content' in msg:
#                         messages.append({
#                             "role": msg['role'],
#                             "content": msg['content']
#                         })
            
#             # Add current user message
#             messages.append({
#                 "role": "user",
#                 "content": input_text
#             })
            
#             # Log the messages being sent to the model for debugging
#             logger.debug(f"Sending messages to model: {messages}")
            
#             # Generate response with timeout
#             try:
#                 response = self.chat_model.invoke(messages)
                
#                 # Update token usage
#                 if hasattr(response, 'usage'):
#                     self.used_tokens += response.usage.get('total_tokens', 0)
#                     self.requested_tokens += response.usage.get('prompt_tokens', 0)
#                 else:
#                     # Estimate token usage if not provided
#                     estimated_tokens = len(input_text.split()) * 1.3  # Rough estimation
#                     self.used_tokens += int(estimated_tokens)
#                     self.requested_tokens += int(estimated_tokens)
                
#                 return response.content
#             except Exception as e:
#                 error_info = self._handle_error(e)
#                 if error_info['is_daily_limit']:
#                     raise DailyLimitError(error_info)
#                 raise
            
#         except Exception as e:
#             logger.error(f"Error in generate_response: {str(e)}")
#             if isinstance(e, DailyLimitError):
#                 raise
#             error_info = self._handle_error(e)
#             if error_info['is_daily_limit']:
#                 raise DailyLimitError(error_info)
#             raise

#     def get_token_usage(self) -> Dict[str, Any]:
#         """Get current token usage information"""
#         now = time.time()
#         wait_time = None
        
#         if self.rate_limiter.daily_limit_hit:
#             remaining_time = self.rate_limiter.daily_limit_reset_time - now
#             if remaining_time > 0:
#                 minutes = int(remaining_time // 60)
#                 seconds = int(remaining_time % 60)
#                 wait_time = f"{minutes}m{seconds}s"
        
#         return {
#             'daily_limit': f"{self.daily_limit:,}",
#             'used_tokens': f"{self.used_tokens:,}",
#             'requested_tokens': f"{self.requested_tokens:,}",
#             'wait_time': wait_time
#         } 
#     ## added 2 utility functions  --update_token_usage and  get_token_status
#     def _update_token_usage(self, response):
#         """Update token usage counts from response"""
#         with self.token_lock:
#             if hasattr(response, 'usage'):
#                 tokens_used = response.usage.get('total_tokens', 0)
#             else:
#                 tokens_used = len(response.content) // 4  # Fallback estimation
#                 print("estimated tokens used : {tokens_used}")
            
#             self.used_tokens += tokens_used
            
#             # Update database
#             from app.utils.db import update_token_usage
#             update_token_usage(self.user_id, tokens_used)
            
#             # Check if we've hit the daily limit
#             if self.used_tokens >= self.daily_limit:
#                 raise DailyLimitError({
#                     'retry_after': self.token_reset_time - time.time(),
#                     'is_daily_limit': True
#                 })
    

#     def get_token_status(self):
#         """Return current token usage information"""
#         with self.token_lock:
#             now = time.time()
#             if now > self.token_reset_time:
#                 # Reset counters if 24 hours have passed
#                 from app.utils.db import record_token_reset
#                 record_token_reset(
#                     self.user_id,
#                     self.used_tokens,
#                     self.used_tokens >= self.daily_limit
#                 )
#                 self.used_tokens = 0
#                 self.token_reset_time = now + 86400
            
#             # Get usage from database for accurate reporting
#             from app.utils.db import get_token_usage
#             db_usage = get_token_usage(self.user_id)
            
#             return {
#                 'used': db_usage['today']['tokens_used'],
#                 'remaining': max(0, self.daily_limit - db_usage['today']['tokens_used']),
#                 'limit': self.daily_limit,
#                 'reset_time': self.token_reset_time,
#                 'reset_in': max(0, self.token_reset_time - now),
#                 'history': db_usage['history']
#             }


from langchain_community.embeddings import HuggingFaceEmbeddings

class ChatModel:
    """Model for handling chat-related operations"""
    
    def __init__(self, api_key: str, user_id: int = None):
        self.api_key = api_key
        self.user_id = user_id
        self._chat_model = None
        self.vector_store = VectorStoreModel(user_id=user_id)
        
        # Token tracking attributes
        self.token_lock = Lock()  # Critical missing piece
        self.used_tokens = 0
        self.requested_tokens = 0
        self.daily_limit = 100000
        self.token_reset_time = time.time() + 86400  # 24 hours from now
        
        # Request rate limiting
        self.timeout = Timeout(
            timeout=15.0,
            connect=3.0,
            read=10.0,
            write=3.0
        )
        self.rate_limiter = TokenBucket(rate=10/60, capacity=10)
        self.last_request_time = 0
        self.min_request_interval = 0.5
    
    @property
    def chat_model(self):
        """Lazy initialization of chat model"""
        if not self._chat_model:
            try:
                # Get LLM provider from system settings
                from app.utils.db import get_db
                from app.models.database_models import SystemSettings
                db = get_db()
                setting = db.query(SystemSettings).filter(SystemSettings.key == 'llm_provider').first()
                provider = setting.value if setting else os.getenv('LLM_PROVIDER', 'openai').lower()
                
                # If OpenAI is set from admin, use environment variable instead of database key
                api_key_to_use = None
                if provider == 'openai' and setting:
                    api_key_to_use = os.getenv('OPENAI_API_KEY')
                elif provider in ['openai', 'groq']:
                    api_key_to_use = self.api_key
                
                # Enforce Groq-only when Groq is selected - no fallback to OpenAI
                if provider == 'groq':
                    if not api_key_to_use:
                        raise ValueError(
                            "Groq API key is required when Groq is selected as the LLM provider. "
                            "Please configure your Groq API key in the chat interface."
                        )
                
                # Use dynamic LLM factory - supports OpenAI, Groq, and vLLM
                # For OpenAI and Groq, use the api_key (from env if OpenAI set from admin, else from self.api_key)
                # For vLLM, api_key is not needed
                self._chat_model = create_llm(
                    temperature=0.7,
                    max_tokens=1024,
                    api_key=api_key_to_use,
                    provider=provider
                )
            except Exception as e:
                logger.error(f"Failed to initialize chat model: {str(e)}")
                raise
        return self._chat_model

    def _wait_for_token(self):
        """Wait for a token to become available with jitter"""
        while not self.rate_limiter.acquire():
            jitter = random.uniform(0.1, 0.5)
            time.sleep(jitter)
            
            now = time.time()
            if now - self.last_request_time < self.min_request_interval:
                time.sleep(self.min_request_interval - (now - self.last_request_time))

    def _handle_error(self, error: Exception) -> Dict:
        """Handle errors and update rate limiter state"""
        error_info = {
            'error': str(error),
            'retry_after': None,
            'is_daily_limit': False
        }
        
        if isinstance(error, httpx.HTTPStatusError):
            try:
                error_data = error.response.json()
                self.rate_limiter.record_error(error_data.get('error', {}))
                
                if error.response.status_code == 429:
                    if error_data.get('error', {}).get('type') == 'tokens':
                        error_info['is_daily_limit'] = True
                        error_info['retry_after'] = self.rate_limiter.daily_limit_reset_time - time.time()
                        logger.warning(f"Daily token limit reached. Reset in {error_info['retry_after']} seconds")
                    else:
                        logger.warning("Rate limit hit, increasing delay")
                        time.sleep(10)
                elif error.response.status_code >= 500:
                    logger.warning("Server error, adding delay")
                    time.sleep(5)
            except:
                logger.error("Error parsing error response")
        
        elif isinstance(error, httpx.TimeoutException):
            logger.warning("Request timeout, adding delay")
            time.sleep(3)
        
        return error_info

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=1, max=5),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException)),
        reraise=True
    )
    def generate_response(self, input_text: str, 
                         system_prompt: Optional[str] = None, 
                         chat_history: Optional[List[Dict]] = None) -> str:
        """Generate a response using the chat model with vector store context"""
        try:
            self._wait_for_token()
            self.last_request_time = time.time()
            
            messages = []
            
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            
            if chat_history:
                for msg in chat_history:
                    if 'role' in msg and 'content' in msg:
                        messages.append({
                            "role": msg['role'],
                            "content": msg['content']
                        })
            
            messages.append({"role": "user", "content": input_text})
            
            logger.debug(f"Sending messages to model: {messages}")
            
            try:
                response = self.chat_model.invoke(messages)
                self._update_token_usage(response)  # Updated to use the proper method
                return response.content
            except Exception as e:
                error_info = self._handle_error(e)
                if error_info['is_daily_limit']:
                    raise DailyLimitError(error_info)
                raise
            
        except Exception as e:
            logger.error(f"Error in generate_response: {str(e)}")
            if isinstance(e, DailyLimitError):
                raise
            error_info = self._handle_error(e)
            if error_info['is_daily_limit']:
                raise DailyLimitError(error_info)
            raise

    def _update_token_usage(self, response):
        """Update token usage counts from response"""
        with self.token_lock:
            try:
                if hasattr(response, 'usage'):
                    tokens_used = response.usage.get('total_tokens', 0)
                else:
                    tokens_used = len(response.content) // 4  # Fallback estimation
                    logger.debug(f"Estimated tokens used: {tokens_used}")
                
                self.used_tokens += tokens_used
                self.requested_tokens += tokens_used
                
                # Update database
                if self.user_id:
                    from app.utils.db import update_token_usage
                    update_token_usage(self.user_id, tokens_used)
                
                if self.used_tokens >= self.daily_limit:
                    raise DailyLimitError({
                        'retry_after': self.token_reset_time - time.time(),
                        'is_daily_limit': True
                    })
            except Exception as e:
                logger.error(f"Error updating token usage: {str(e)}")
                raise

    def get_token_status(self) -> Dict[str, Any]:
        """Get current token usage information"""
        with self.token_lock:
            now = time.time()
            
            # Reset if 24 hours have passed
            if now > self.token_reset_time:
                if self.user_id:
                    from app.utils.db import record_token_reset
                    record_token_reset(
                        self.user_id,
                        self.used_tokens,
                        self.used_tokens >= self.daily_limit
                    )
                self.used_tokens = 0
                self.token_reset_time = now + 86400
            
            # Get usage from database if available
            db_usage = {'today': {'tokens_used': self.used_tokens}, 'history': []}
            if self.user_id:
                try:
                    from app.utils.db import get_token_usage
                    db_usage = get_token_usage(self.user_id)
                except Exception as e:
                    logger.error(f"Error getting token usage from DB: {str(e)}")
            
            return {
                'used': db_usage['today']['tokens_used'],
                'remaining': max(0, self.daily_limit - db_usage['today']['tokens_used']),
                'limit': self.daily_limit,
                'reset_time': self.token_reset_time,
                'reset_in': max(0, self.token_reset_time - now),
                'history': db_usage.get('history', [])
            }


class DailyLimitError(Exception):
    """Custom exception for daily token limit errors"""
    def __init__(self, error_info: Dict):
        self.error_info = error_info
        self.retry_after = error_info.get('retry_after', 3600)  # Default to 1 hour
        super().__init__(f"Daily token limit reached. Please try again in {int(self.retry_after/60)} minutes")

# app/models/models.py (VectorStoreModel part)
from typing import List, Optional
from langchain_nomic import NomicEmbeddings
from langchain_community.vectorstores import FAISS
import logging
import os
import pickle

logger = logging.getLogger(__name__)

class VectorStoreModel:
    """Model for handling vector store operations with multi-user support"""
    
    _instance = None
    VECTOR_STORE_PATH = "shared_vector_store.pkl"
    
    def __new__(cls, user_id: int = None):
        if cls._instance is None:
            instance = super(VectorStoreModel, cls).__new__(cls)
            instance._initialized = False
            cls._instance = instance
        return cls._instance
    
    def __init__(self, user_id: int = None):
        if not hasattr(self, '_initialized') or not self._initialized:
            self._embeddings = None
            self._vectorstore = None
            self._initialized = True
            self._load_existing_store()
        self.user_id = user_id
    
    def _load_existing_store(self):
        """Load existing vector store if available"""
        try:
            if os.path.exists(self.VECTOR_STORE_PATH):
                with open(self.VECTOR_STORE_PATH, 'rb') as f:
                    self._vectorstore = pickle.load(f)
                logger.info("Loaded shared vector store")
        except Exception as e:
            logger.error(f"Error loading shared vector store: {str(e)}")
    
    def _save_store(self):
        """Save vector store to disk"""
        try:
            if self._vectorstore:
                with open(self.VECTOR_STORE_PATH, 'wb') as f:
                    pickle.dump(self._vectorstore, f)
                logger.info("Saved shared vector store to disk")
        except Exception as e:
            logger.error(f"Error saving shared vector store: {str(e)}")
    
    @property
    def embeddings(self):
        """Lazy initialization of embeddings"""
        if not self._embeddings:
            try:
                nomic_api_key = Config.NOMIC_API_KEY
                if nomic_api_key == 'your_nomic_api_key_here':
                    raise ValueError("Please configure your Nomic API key in the config file")
                
                # self._embeddings = NomicEmbeddings(
                #     model="nomic-embed-text-v1.5",
                #     nomic_api_key=nomic_api_key
                # )
                self.embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

            except Exception as e:
                logger.error(f"Failed to create embeddings: {str(e)}")
                raise
        return self._embeddings

    def create_vectorstore(self, documents: List) -> None:
        """Create or update vector store with documents"""
        if not self.user_id:
            raise ValueError("User ID is required for vector store operations")
            
        try:
            # Add user_id to metadata of each document
            for doc in documents:
                doc.metadata['user_id'] = self.user_id
            
            if not self._vectorstore:
                self._vectorstore = FAISS.from_documents(
                    documents, 
                    self.embeddings
                )
            else:
                self._vectorstore.add_documents(documents)
            
            # Save the updated store
            self._save_store()
            logger.info(f"Successfully processed {len(documents)} documents into shared vector store for user {self.user_id}")
        except Exception as e:
            logger.error(f"Error creating/updating vector store for user {self.user_id}: {str(e)}")
            raise

    def search_similar(self, query: str, k: int = 3) -> List:
        """Search for similar documents in the vector store with user isolation"""
        if not self.user_id:
            raise ValueError("User ID is required for vector store operations")
            
        try:
            if not self._vectorstore:
                logger.warning(f"Vector store not initialized or empty")
                return []
            
            # Search with metadata filter for user_id
            results = self._vectorstore.similarity_search(
                query,
                k=k,
                filter={"user_id": self.user_id}  # Only return documents belonging to this user
            )
            
            logger.info(f"Found {len(results)} relevant documents for query from user {self.user_id}")
            return results
        except Exception as e:
            logger.error(f"Error searching vector store for user {self.user_id}: {str(e)}")
            return []

    def delete_user_documents(self) -> None:
        """Delete all documents for a specific user"""
        if not self.user_id:
            raise ValueError("User ID is required for vector store operations")
            
        try:
            if self._vectorstore and hasattr(self._vectorstore, 'delete'):
                # Delete documents with matching user_id
                self._vectorstore.delete(filter={"user_id": self.user_id})
                self._save_store()
                logger.info(f"Deleted all documents for user {self.user_id}")
        except Exception as e:
            logger.error(f"Error deleting documents for user {self.user_id}: {str(e)}")
            raise

# class ConversationModel:
#     """Model for handling conversation-related database operations"""
    
#     def __init__(self, user_id: int):
#         self.user_id = user_id
    
#     def create_conversation(self, title: str) -> int:
#         """Create a new conversation"""
#         try:
#             db = get_db()
#             cursor = db.execute(
#                 'INSERT INTO conversations (user_id, title) VALUES (?, ?)',
#                 (self.user_id, title)
#             )
#             db.commit()
#             return cursor.lastrowid
#         except Exception as e:
#             logger.error(f"Error creating conversation: {str(e)}")
#             raise

#     def get_conversations(self, limit: int = 4) -> List[Dict]:
#         """Get user's recent conversations"""
#         try:
#             db = get_db()
#             conversations = db.execute(
#                 '''SELECT c.id, c.title, MAX(ch.created_at) as last_message
#                    FROM conversations c
#                    LEFT JOIN chat_history ch ON c.id = ch.conversation_id
#                    WHERE c.user_id = ?
#                    GROUP BY c.id
#                    ORDER BY last_message DESC
#                    LIMIT ?''',
#                 (self.user_id, limit)
#             ).fetchall()
#             return [dict(conv) for conv in conversations]
#         except Exception as e:
#             logger.error(f"Error retrieving conversations: {str(e)}")
#             raise

#     def save_message(self, conversation_id: int, message: str, role: str) -> int:
#         """Save a message to the chat history"""
#         try:
#             db = get_db()
#             cursor = db.execute(
#                 'INSERT INTO chat_history (conversation_id, message, role) VALUES (?, ?, ?)',
#                 (conversation_id, message, role)
#             )
            
#             # Update conversation's last activity
#             db.execute(
#                 'UPDATE conversations SET updated_at = ? WHERE id = ?',
#                 (datetime.now().isoformat(), conversation_id)
#             )
            
#             db.commit()
#             return cursor.lastrowid
#         except Exception as e:
#             logger.error(f"Error saving message: {str(e)}")
#             raise

#     def get_chat_history(self, conversation_id: int) -> List[Dict]:
#         """Get chat history for a conversation"""
#         try:
#             db = get_db()
#             messages = db.execute(
#                 '''SELECT message, role, created_at
#                    FROM chat_history
#                    WHERE conversation_id = ?
#                    ORDER BY created_at''',
#                 (conversation_id,)
#             ).fetchall()
#             return [dict(msg) for msg in messages]
#         except Exception as e:
#             logger.error(f"Error retrieving chat history: {str(e)}")
#             raise

#     def delete_conversation(self, conversation_id: int) -> None:
#         """Delete a conversation and its associated messages"""
#         try:
#             db = get_db()
#             # Delete the conversation (this will cascade delete chat_history due to foreign key constraint)
#             db.execute(
#                 'DELETE FROM conversations WHERE id = ? AND user_id = ?',
#                 (conversation_id, self.user_id)
#             )
#             db.commit()
#         except Exception as e:
#             logger.error(f"Error deleting conversation: {str(e)}")
#             raise



class ConversationModel:
    """Model for handling conversation-related database operations"""
    
    def __init__(self, user_id: int):
        self.user_id = user_id
    
    @staticmethod
    def _model_to_dict(model_instance):
        """Convert SQLAlchemy model instance to dictionary"""
        if model_instance is None:
            return None
        result = {}
        for key in model_instance.__table__.columns.keys():
            value = getattr(model_instance, key)
            if isinstance(value, datetime):
                value = value.isoformat()
            result[key] = value
        return result
    
    def create_conversation(self, title: str) -> int:
        """Create a new conversation"""
        try:
            db = get_db()
            conversation = DBConversation(user_id=self.user_id, title=title)
            db.add(conversation)
            db.commit()
            db.refresh(conversation)
            return conversation.id
        except Exception as e:
            logger.error(f"Error creating conversation: {str(e)}")
            db.rollback()
            raise
    
    def get_conversations(self, limit: int = 20) -> List[Dict]:
        """Get user's recent conversations"""
        try:
            db = get_db()
            # Query conversations with their last message time
            # Use subquery or alias to properly order by coalesced values
            last_msg_subq = db.query(
                DBChatHistory.conversation_id,
                func.max(DBChatHistory.created_at).label('last_message')
            ).group_by(DBChatHistory.conversation_id).subquery()
            
            conversations = db.query(
                DBConversation.id, 
                DBConversation.title, 
                last_msg_subq.c.last_message.label('last_message'),
                DBConversation.updated_at,
                DBConversation.created_at
            ).outerjoin(
                last_msg_subq, DBConversation.id == last_msg_subq.c.conversation_id
            ).filter(
                DBConversation.user_id == self.user_id
            ).order_by(
                # Order by last_message if available, otherwise by updated_at, then by created_at
                desc(func.coalesce(
                    last_msg_subq.c.last_message, 
                    DBConversation.updated_at, 
                    DBConversation.created_at
                ))
            ).limit(limit).all()
            
            result = []
            for conv in conversations:
                result.append({
                    'id': conv.id,
                    'title': conv.title,
                    'last_message': conv.last_message.isoformat() if conv.last_message else None,
                    'updated_at': conv.updated_at.isoformat() if conv.updated_at else None,
                    'created_at': conv.created_at.isoformat() if conv.created_at else None
                })
            return result
        except Exception as e:
            logger.error(f"Error retrieving conversations: {str(e)}")
            raise
    
    def get_conversation_by_id(self, conversation_id: int) -> Dict:
        """Get a specific conversation by ID"""
        try:
            db = get_db()
            conversation = db.query(DBConversation).filter(
                and_(DBConversation.id == conversation_id, DBConversation.user_id == self.user_id)
            ).first()
            return ConversationModel._model_to_dict(conversation)
        except Exception as e:
            logger.error(f"Error retrieving conversation: {str(e)}")
            raise
    
    def update_conversation_title(self, conversation_id: int, new_title: str) -> bool:
        """Update the title of a conversation"""
        try:
            db = get_db()
            conversation = db.query(DBConversation).filter(
                and_(DBConversation.id == conversation_id, DBConversation.user_id == self.user_id)
            ).first()
            if conversation:
                conversation.title = new_title
                db.commit()
                return True
            return False
        except Exception as e:
            logger.error(f"Error updating conversation title: {str(e)}")
            db.rollback()
            raise
    
    def save_message(self, conversation_id: int, message: str, role: str) -> int:
        """Save a message to the chat history - verifies user ownership"""
        try:
            db = get_db()
            
            # First verify that the conversation belongs to this user
            conversation = db.query(DBConversation).filter(
                and_(DBConversation.id == conversation_id, DBConversation.user_id == self.user_id)
            ).first()
            
            if not conversation:
                logger.warning(f"User {self.user_id} attempted to save message to conversation {conversation_id} without ownership")
                raise ValueError(f"Conversation {conversation_id} does not belong to user {self.user_id}")
            
            # Now safely save the message
            chat_message = DBChatHistory(
                conversation_id=conversation_id,
                message=message,
                role=role
            )
            db.add(chat_message)
            
            # Update conversation's last activity
            conversation.updated_at = datetime.utcnow()
            
            db.commit()
            db.refresh(chat_message)
            return chat_message.id
        except Exception as e:
            logger.error(f"Error saving message: {str(e)}")
            db.rollback()
            raise
    
    def get_chat_history(self, conversation_id: int) -> List[Dict]:
        """Get chat history for a conversation - verifies user ownership"""
        try:
            db = get_db()
            # First verify that the conversation belongs to this user
            conversation = db.query(DBConversation).filter(
                and_(DBConversation.id == conversation_id, DBConversation.user_id == self.user_id)
            ).first()
            
            if not conversation:
                logger.warning(f"User {self.user_id} attempted to access conversation {conversation_id} without ownership")
                return []
            
            # Now safely retrieve messages for this conversation
            messages = db.query(DBChatHistory).filter(
                DBChatHistory.conversation_id == conversation_id
            ).order_by(DBChatHistory.created_at.asc()).all()
            
            result = []
            for msg in messages:
                result.append({
                    'message': msg.message,
                    'role': msg.role,
                    'created_at': msg.created_at.isoformat() if msg.created_at else None
                })
            return result
        except Exception as e:
            logger.error(f"Error retrieving chat history: {str(e)}")
            raise

    def delete_conversation(self, conversation_id: int) -> None:
        """Delete a conversation and its associated messages"""
        try:
            db = get_db()
            conversation = db.query(DBConversation).filter(
                and_(DBConversation.id == conversation_id, DBConversation.user_id == self.user_id)
            ).first()
            if conversation:
                db.delete(conversation)  # Cascade delete will handle chat_history
                db.commit()
        except Exception as e:
            logger.error(f"Error deleting conversation: {str(e)}")
            db.rollback()
            raise

    def duplicate_conversation(self, conversation_id: int) -> Optional[int]:
        """
        Duplicate a conversation and all of its messages for the current user.

        Returns the new conversation ID, or None if the source conversation
        does not belong to this user.
        """
        try:
            db = get_db()

            # Ensure the conversation belongs to this user
            source = db.query(DBConversation).filter(
                and_(DBConversation.id == conversation_id, DBConversation.user_id == self.user_id)
            ).first()
            if not source:
                logger.warning(
                    "User %s attempted to duplicate conversation %s without ownership",
                    self.user_id,
                    conversation_id,
                )
                return None

            # Create the new conversation with a copied title
            new_title = f"{source.title} (Copy)"
            new_conv = DBConversation(
                user_id=self.user_id,
                title=new_title,
                is_active=source.is_active,
            )
            db.add(new_conv)
            db.flush()  # Ensure new_conv.id is available

            # Copy all chat history rows
            messages = (
                db.query(DBChatHistory)
                .filter(DBChatHistory.conversation_id == source.id)
                .order_by(DBChatHistory.created_at.asc())
                .all()
            )
            for msg in messages:
                cloned = DBChatHistory(
                    conversation_id=new_conv.id,
                    message=msg.message,
                    role=msg.role,
                    created_at=msg.created_at,
                )
                db.add(cloned)

            db.commit()
            db.refresh(new_conv)
            return new_conv.id
        except Exception as e:
            logger.error(f"Error duplicating conversation: {str(e)}")
            db.rollback()
            raise

    def reset_all_chats(self) -> None:
        """Delete all conversations and chat history for the user"""
        try:
            db = get_db()
            db.query(DBConversation).filter(DBConversation.user_id == self.user_id).delete()
            db.commit()
            logger.info(f"All chats reset for user {self.user_id}")
        except Exception as e:
            logger.error(f"Error resetting all chats: {str(e)}")
            db.rollback()
            raise








class SurveyModel:
    """Model for handling survey-related database operations"""
    
    def __init__(self, user_id: int):
        self.user_id = user_id
    
    @staticmethod
    def _model_to_dict(model_instance):
        """Convert SQLAlchemy model instance to dictionary"""
        if model_instance is None:
            return None
        result = {}
        for key in model_instance.__table__.columns.keys():
            value = getattr(model_instance, key)
            if isinstance(value, datetime):
                value = value.isoformat()
            result[key] = value
        return result
    
    def save_survey_response(self, rating: int, message: str = None) -> int:
        """Save a survey response to the database"""
        try:
            if not isinstance(rating, int) or rating < 1 or rating > 10:
                raise ValueError("Rating must be an integer between 1 and 10")

            db = get_db()
            response = DBSurveyResponse(
                user_id=self.user_id,
                rating=rating,
                message=message
            )
            db.add(response)
            db.commit()
            db.refresh(response)
            return response.id
        except Exception as e:
            logger.error(f"Error saving survey response: {str(e)}")
            db.rollback()
            raise
    
    def get_user_survey_responses(self) -> List[Dict]:
        """Get all survey responses for a user"""
        try:
            db = get_db()
            responses = db.query(DBSurveyResponse).filter(
                DBSurveyResponse.user_id == self.user_id
            ).order_by(desc(DBSurveyResponse.created_at)).all()
            
            result = []
            for resp in responses:
                result.append({
                    'rating': resp.rating,
                    'message': resp.message,
                    'created_at': resp.created_at.isoformat() if resp.created_at else None
                })
            return result
        except Exception as e:
            logger.error(f"Error retrieving survey responses: {str(e)}")
            raise

    def has_submitted_survey(self):
        """Check if the user has already submitted a survey"""
        try:
            logger.info(f"Checking survey submission status for user_id: {self.user_id}")
            db = get_db()
            count = db.query(DBSurveyResponse).filter(
                DBSurveyResponse.user_id == self.user_id
            ).count()
            
            status = "has" if count > 0 else "has not"
            logger.info(f"Survey check result: User {self.user_id} {status} submitted a survey (count: {count})")
            
            return count > 0
        except Exception as e:
            logger.error(f"Error checking survey submission for user {self.user_id}: {str(e)}")
            raise


class LessonFAQ:
    @staticmethod
    def log_question(lesson_id, question):
        """Log a question for a lesson FAQ"""
        try:
            db = get_db()
            canonical = question.strip()
            
            # Check if a record with this canonical already exists for this lesson
            existing = db.query(DBLessonFAQ).filter(
                and_(
                    DBLessonFAQ.lesson_id == lesson_id,
                    func.coalesce(DBLessonFAQ.canonical_question, DBLessonFAQ.question) == canonical
                )
            ).first()
            
            if existing:
                existing.count += 1
            else:
                faq = DBLessonFAQ(
                    lesson_id=lesson_id,
                    question=canonical,
                    canonical_question=canonical,
                    count=1
                )
                db.add(faq)
            
            db.commit()
        except Exception as e:
            logger.error(f"Error logging FAQ question: {str(e)}")
            db.rollback()
            raise

    @staticmethod
    def get_top_faqs(lesson_id, limit=5):
        """Get top FAQs for a lesson"""
        try:
            db = get_db()
            faqs = db.query(
                func.coalesce(DBLessonFAQ.canonical_question, DBLessonFAQ.question).label('question'),
                DBLessonFAQ.count
            ).filter(
                DBLessonFAQ.lesson_id == lesson_id
            ).order_by(
                desc(DBLessonFAQ.count)
            ).limit(limit).all()
            
            return [{'question': faq.question, 'count': faq.count} for faq in faqs]
        except Exception as e:
            logger.error(f"Error getting top FAQs: {str(e)}")
            return []


class LessonChatHistory:
    """Model for handling lesson-specific chat history"""
    
    @staticmethod
    def save_qa(lesson_id: int, user_id: int, question: str, answer: str, canonical_question: str | None = None) -> int:
        """Save a Q&A pair for a specific lesson and user"""
        try:
            db = get_db()
            chat_history = DBLessonChatHistory(
                lesson_id=lesson_id,
                user_id=user_id,
                question=question,
                answer=answer,
                canonical_question=canonical_question
            )
            db.add(chat_history)
            db.commit()
            db.refresh(chat_history)
            return chat_history.id
        except Exception as e:
            logger.error(f"Error saving lesson chat history: {str(e)}")
            db.rollback()
            raise

    @staticmethod
    def update_canonical_question(chat_history_id: int, canonical_question: str) -> None:
        """Update canonical question for an existing lesson chat history row."""
        try:
            db = get_db()
            row = db.query(DBLessonChatHistory).filter(DBLessonChatHistory.id == chat_history_id).first()
            if not row:
                return
            row.canonical_question = canonical_question
            db.commit()
        except Exception as e:
            logger.error(f"Error updating canonical question for lesson chat history: {str(e)}")
            db.rollback()
            raise
    
    @staticmethod
    def get_lesson_chat_history(lesson_id: int, user_id: int) -> List[Dict]:
        """Get chat history for a specific lesson and user"""
        try:
            db = get_db()
            history = db.query(DBLessonChatHistory).filter(
                and_(
                    DBLessonChatHistory.lesson_id == lesson_id,
                    DBLessonChatHistory.user_id == user_id
                )
            ).order_by(DBLessonChatHistory.created_at.asc()).all()
            
            result = []
            for record in history:
                result.append({
                    'question': record.question,
                    'answer': record.answer,
                    'created_at': record.created_at.isoformat() if record.created_at else None
                })
            return result
        except Exception as e:
            logger.error(f"Error getting lesson chat history: {str(e)}")
            return []
    
    @staticmethod
    def clear_lesson_chat_history(lesson_id: int, user_id: int) -> None:
        """Clear chat history for a specific lesson and user"""
        try:
            db = get_db()
            db.query(DBLessonChatHistory).filter(
                and_(
                    DBLessonChatHistory.lesson_id == lesson_id,
                    DBLessonChatHistory.user_id == user_id
                )
            ).delete()
            db.commit()
        except Exception as e:
            logger.error(f"Error clearing lesson chat history: {str(e)}")
            db.rollback()
            raise
