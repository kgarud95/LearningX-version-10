# Additional models for Phase 3: Learning Experience
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from enum import Enum
import uuid
from datetime import datetime

# Enhanced models for curriculum management

class LessonCreate(BaseModel):
    title: str
    description: Optional[str] = None
    content: str
    lesson_type: str  # "video", "text", "quiz", "assignment"
    duration_minutes: Optional[int] = None
    is_free: bool = False
    video_url: Optional[str] = None
    text_content: Optional[str] = None
    quiz_data: Optional[Dict[str, Any]] = None

class LessonUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None
    duration_minutes: Optional[int] = None
    is_free: Optional[bool] = None
    video_url: Optional[str] = None
    text_content: Optional[str] = None
    quiz_data: Optional[Dict[str, Any]] = None

class LessonResponse(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    content: str
    lesson_type: str
    duration_minutes: Optional[int] = None
    order: int
    is_free: bool = False
    video_url: Optional[str] = None
    text_content: Optional[str] = None
    quiz_data: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

class ModuleCreate(BaseModel):
    title: str
    description: Optional[str] = None
    course_id: str

class ModuleUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None

class ModuleResponse(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    course_id: str
    order: int
    lessons: List[LessonResponse] = []
    total_lessons: int = 0
    total_duration: Optional[int] = None
    created_at: datetime
    updated_at: datetime

class CourseStructure(BaseModel):
    course: Dict[str, Any]
    modules: List[ModuleResponse]
    total_modules: int
    total_lessons: int
    total_duration: Optional[int] = None
    user_progress: Optional[Dict[str, Any]] = None

class LearningSession(BaseModel):
    course_id: str
    current_lesson_id: Optional[str] = None
    user_progress: Dict[str, Any]
    next_lesson: Optional[LessonResponse] = None
    previous_lesson: Optional[LessonResponse] = None

class ReorderRequest(BaseModel):
    item_id: str
    new_order: int
    
class BulkReorderRequest(BaseModel):
    items: List[ReorderRequest]