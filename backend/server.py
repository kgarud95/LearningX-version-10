from fastapi import FastAPI, APIRouter, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timedelta
from enum import Enum
import httpx

# Import authentication utilities
from auth import (
    Token, LoginCredentials, RegisterCredentials, EmergentAuthRequest, GoogleAuthRequest,
    verify_password, get_password_hash, create_access_token, verify_token, get_current_user,
    verify_emergent_session, verify_google_oauth_code, create_session, verify_session, invalidate_session
)

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app without a prefix
app = FastAPI(title="Learning Platform API", version="1.0.0")

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Security
security = HTTPBearer()

# Enums
class UserRole(str, Enum):
    STUDENT = "student"
    INSTRUCTOR = "instructor"
    ADMIN = "admin"

class CourseStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"

class LessonType(str, Enum):
    VIDEO = "video"
    TEXT = "text"
    QUIZ = "quiz"
    ASSIGNMENT = "assignment"

class EnrollmentStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

# User Models
class User(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    email: str
    name: str
    password_hash: Optional[str] = None
    role: UserRole = UserRole.STUDENT
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True
    auth_provider: str = "email"  # email, google, emergent

class UserCreate(BaseModel):
    email: str
    name: str
    password: str
    role: UserRole = UserRole.STUDENT

class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    role: UserRole
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    created_at: datetime
    is_active: bool
    auth_provider: str

# Course Models
class Lesson(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: Optional[str] = None
    content: str  # For text lessons, video URL, or quiz JSON
    lesson_type: LessonType
    duration_minutes: Optional[int] = None
    order: int
    is_free: bool = False

class Module(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: Optional[str] = None
    lessons: List[Lesson] = []
    order: int

class Course(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: str
    short_description: Optional[str] = None
    instructor_id: str
    category: str
    price: float = 0.0  # 0 for free courses
    thumbnail_url: Optional[str] = None
    modules: List[Module] = []
    status: CourseStatus = CourseStatus.DRAFT
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    total_duration_minutes: Optional[int] = None
    language: str = "English"
    level: str = "Beginner"  # Beginner, Intermediate, Advanced
    tags: List[str] = []

class CourseCreate(BaseModel):
    title: str
    description: str
    short_description: Optional[str] = None
    category: str
    price: float = 0.0
    thumbnail_url: Optional[str] = None
    language: str = "English"
    level: str = "Beginner"
    tags: List[str] = []

class CourseResponse(BaseModel):
    id: str
    title: str
    description: str
    short_description: Optional[str] = None
    instructor_id: str
    instructor_name: Optional[str] = None
    category: str
    price: float
    thumbnail_url: Optional[str] = None
    status: CourseStatus
    created_at: datetime
    total_duration_minutes: Optional[int] = None
    language: str
    level: str
    tags: List[str]
    total_lessons: int = 0
    total_modules: int = 0

# Enrollment Models
class Enrollment(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    course_id: str
    enrollment_date: datetime = Field(default_factory=datetime.utcnow)
    completion_date: Optional[datetime] = None
    status: EnrollmentStatus = EnrollmentStatus.ACTIVE
    progress_percentage: float = 0.0
    last_accessed: Optional[datetime] = None
    payment_id: Optional[str] = None  # For paid courses

class EnrollmentCreate(BaseModel):
    course_id: str

class EnrollmentResponse(BaseModel):
    id: str
    user_id: str
    course_id: str
    course_title: str
    course_thumbnail: Optional[str] = None
    instructor_name: str
    enrollment_date: datetime
    completion_date: Optional[datetime] = None
    status: EnrollmentStatus
    progress_percentage: float
    last_accessed: Optional[datetime] = None

# Progress Models
class LessonProgress(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    course_id: str
    lesson_id: str
    completed: bool = False
    completion_date: Optional[datetime] = None
    time_spent_minutes: int = 0
    last_position: Optional[int] = None  # For video lessons (in seconds)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class ProgressUpdate(BaseModel):
    lesson_id: str
    completed: bool = False
    time_spent_minutes: int = 0
    last_position: Optional[int] = None

# Auth dependency
async def get_current_user_dependency(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Get current authenticated user"""
    return await get_current_user(db, await verify_token(credentials))

# API Routes

# Health check
@api_router.get("/")
async def root():
    return {"message": "Learning Platform API is running!", "version": "1.0.0"}

# Authentication Routes

@api_router.post("/auth/register", response_model=Token)
async def register(user_data: RegisterCredentials):
    """Register a new user with email and password"""
    # Check if user already exists
    existing_user = await db.users.find_one({"email": user_data.email})
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Create user
    user_dict = user_data.dict()
    user_dict["password_hash"] = get_password_hash(user_data.password)
    del user_dict["password"]
    user_dict["auth_provider"] = "email"
    
    user_obj = User(**user_dict)
    await db.users.insert_one(user_obj.dict())
    
    # Create access token
    access_token_expires = timedelta(minutes=int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", 30)))
    access_token = create_access_token(
        data={"sub": user_obj.id, "email": user_obj.email},
        expires_delta=access_token_expires
    )
    
    user_response = UserResponse(**user_obj.dict())
    return Token(
        access_token=access_token,
        token_type="bearer",
        user=user_response.dict()
    )

@api_router.post("/auth/login", response_model=Token)
async def login(credentials: LoginCredentials):
    """Login with email and password"""
    user = await db.users.find_one({"email": credentials.email})
    if not user or not verify_password(credentials.password, user.get("password_hash", "")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Create access token
    access_token_expires = timedelta(minutes=int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", 30)))
    access_token = create_access_token(
        data={"sub": user["id"], "email": user["email"]},
        expires_delta=access_token_expires
    )
    
    user_response = UserResponse(**user)
    return Token(
        access_token=access_token,
        token_type="bearer",
        user=user_response.dict()
    )

@api_router.post("/auth/emergent", response_model=Token)
async def emergent_auth(request: EmergentAuthRequest):
    """Authenticate using Emergent's managed auth"""
    # For Emergent auth, we expect the session_id from the redirect
    # This is a simplified version - in production, you'd handle the full OAuth flow
    return {
        "access_token": "emergent_auth_token",
        "token_type": "bearer",
        "user": {
            "id": "emergent_user_id",
            "email": "user@example.com",
            "name": "Emergent User"
        },
        "redirect_url": f"https://auth.emergentagent.com/?redirect={request.redirect_url}"
    }

@api_router.post("/auth/emergent/session", response_model=Token)
async def verify_emergent_session_endpoint(session_id: str):
    """Verify Emergent session and create local user"""
    try:
        # Verify session with Emergent
        emergent_user = await verify_emergent_session(session_id)
        
        # Find or create user
        user = await db.users.find_one({"email": emergent_user["email"]})
        if not user:
            # Create new user
            user_data = {
                "email": emergent_user["email"],
                "name": emergent_user["name"],
                "avatar_url": emergent_user.get("picture"),
                "auth_provider": "emergent"
            }
            user_obj = User(**user_data)
            await db.users.insert_one(user_obj.dict())
            user = user_obj.dict()
        
        # Create session
        session_token = emergent_user["session_token"]
        await create_session(db, user["id"], session_token)
        
        # Create access token
        access_token_expires = timedelta(minutes=int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", 30)))
        access_token = create_access_token(
            data={"sub": user["id"], "email": user["email"]},
            expires_delta=access_token_expires
        )
        
        user_response = UserResponse(**user)
        return Token(
            access_token=access_token,
            token_type="bearer",
            user=user_response.dict()
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Emergent authentication failed: {str(e)}"
        )

@api_router.post("/auth/google", response_model=Token)
async def google_auth(request: GoogleAuthRequest):
    """Authenticate using Google OAuth"""
    try:
        # Verify Google OAuth code
        google_user = await verify_google_oauth_code(request.code, request.redirect_uri)
        
        # Find or create user
        user = await db.users.find_one({"email": google_user["email"]})
        if not user:
            # Create new user
            user_data = {
                "email": google_user["email"],
                "name": google_user["name"],
                "avatar_url": google_user.get("picture"),
                "auth_provider": "google"
            }
            user_obj = User(**user_data)
            await db.users.insert_one(user_obj.dict())
            user = user_obj.dict()
        
        # Create access token
        access_token_expires = timedelta(minutes=int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", 30)))
        access_token = create_access_token(
            data={"sub": user["id"], "email": user["email"]},
            expires_delta=access_token_expires
        )
        
        user_response = UserResponse(**user)
        return Token(
            access_token=access_token,
            token_type="bearer",
            user=user_response.dict()
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Google authentication failed: {str(e)}"
        )

@api_router.get("/auth/me", response_model=UserResponse)
async def get_current_user_info(current_user: dict = Depends(get_current_user_dependency)):
    """Get current authenticated user information"""
    return UserResponse(**current_user)

@api_router.post("/auth/logout")
async def logout(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Logout user and invalidate session"""
    # For JWT, we can't really invalidate the token on the server side
    # In a production system, you'd maintain a blacklist of tokens
    return {"message": "Successfully logged out"}

# User Management (now with auth)
@api_router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(user_id: str, current_user: dict = Depends(get_current_user_dependency)):
    """Get user by ID (authenticated endpoint)"""
    user = await db.users.find_one({"id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse(**user)

# Course Management (now with auth)
@api_router.post("/courses", response_model=CourseResponse)
async def create_course(course_data: CourseCreate, current_user: dict = Depends(get_current_user_dependency)):
    """Create a new course (authenticated endpoint)"""
    course_dict = course_data.dict()
    course_dict["instructor_id"] = current_user["id"]
    
    course_obj = Course(**course_dict)
    await db.courses.insert_one(course_obj.dict())
    
    response_dict = course_obj.dict()
    response_dict["instructor_name"] = current_user["name"]
    response_dict["total_lessons"] = sum(len(module.lessons) for module in course_obj.modules)
    response_dict["total_modules"] = len(course_obj.modules)
    
    return CourseResponse(**response_dict)

@api_router.get("/courses", response_model=List[CourseResponse])
async def get_courses(
    category: Optional[str] = None,
    level: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 20,
    skip: int = 0
):
    """Get courses (public endpoint)"""
    # Build query
    query = {"status": CourseStatus.PUBLISHED}
    if category:
        query["category"] = category
    if level:
        query["level"] = level
    if search:
        query["$or"] = [
            {"title": {"$regex": search, "$options": "i"}},
            {"description": {"$regex": search, "$options": "i"}},
            {"tags": {"$in": [search]}}
        ]
    
    courses = await db.courses.find(query).skip(skip).limit(limit).to_list(None)
    
    # Enrich with instructor names
    course_responses = []
    for course in courses:
        instructor = await db.users.find_one({"id": course["instructor_id"]})
        instructor_name = instructor.get("name", "Unknown") if instructor else "Unknown"
        
        course["instructor_name"] = instructor_name
        course["total_lessons"] = sum(len(module.get("lessons", [])) for module in course.get("modules", []))
        course["total_modules"] = len(course.get("modules", []))
        
        course_responses.append(CourseResponse(**course))
    
    return course_responses

@api_router.get("/courses/{course_id}", response_model=CourseResponse)
async def get_course(course_id: str):
    """Get course by ID (public endpoint)"""
    course = await db.courses.find_one({"id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    
    # Get instructor name
    instructor = await db.users.find_one({"id": course["instructor_id"]})
    instructor_name = instructor.get("name", "Unknown") if instructor else "Unknown"
    
    course["instructor_name"] = instructor_name
    course["total_lessons"] = sum(len(module.get("lessons", [])) for module in course.get("modules", []))
    course["total_modules"] = len(course.get("modules", []))
    
    return CourseResponse(**course)

# Enrollment Management (now with auth)
@api_router.post("/enrollments", response_model=EnrollmentResponse)
async def enroll_in_course(enrollment_data: EnrollmentCreate, current_user: dict = Depends(get_current_user_dependency)):
    """Enroll in a course (authenticated endpoint)"""
    # Check if course exists
    course = await db.courses.find_one({"id": enrollment_data.course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    
    # Check if already enrolled
    existing_enrollment = await db.enrollments.find_one({
        "user_id": current_user["id"],
        "course_id": enrollment_data.course_id
    })
    if existing_enrollment:
        raise HTTPException(status_code=400, detail="Already enrolled in this course")
    
    # Create enrollment
    enrollment_dict = enrollment_data.dict()
    enrollment_dict["user_id"] = current_user["id"]
    
    enrollment_obj = Enrollment(**enrollment_dict)
    await db.enrollments.insert_one(enrollment_obj.dict())
    
    # Get instructor name
    instructor = await db.users.find_one({"id": course["instructor_id"]})
    instructor_name = instructor.get("name", "Unknown") if instructor else "Unknown"
    
    response_dict = enrollment_obj.dict()
    response_dict["course_title"] = course["title"]
    response_dict["course_thumbnail"] = course.get("thumbnail_url")
    response_dict["instructor_name"] = instructor_name
    
    return EnrollmentResponse(**response_dict)

@api_router.get("/enrollments", response_model=List[EnrollmentResponse])
async def get_user_enrollments(current_user: dict = Depends(get_current_user_dependency)):
    """Get user's enrollments (authenticated endpoint)"""
    enrollments = await db.enrollments.find({"user_id": current_user["id"]}).to_list(None)
    
    enrollment_responses = []
    for enrollment in enrollments:
        course = await db.courses.find_one({"id": enrollment["course_id"]})
        if course:
            instructor = await db.users.find_one({"id": course["instructor_id"]})
            instructor_name = instructor.get("name", "Unknown") if instructor else "Unknown"
            
            enrollment["course_title"] = course["title"]
            enrollment["course_thumbnail"] = course.get("thumbnail_url")
            enrollment["instructor_name"] = instructor_name
            
            enrollment_responses.append(EnrollmentResponse(**enrollment))
    
    return enrollment_responses

# Import curriculum models
from curriculum_models import (
    LessonCreate, LessonUpdate, LessonResponse, ModuleCreate, ModuleUpdate, 
    ModuleResponse, CourseStructure, LearningSession, ReorderRequest, BulkReorderRequest
)

# Curriculum Management - Module APIs
@api_router.post("/courses/{course_id}/modules", response_model=ModuleResponse)
async def create_module(course_id: str, module_data: ModuleCreate, current_user: dict = Depends(get_current_user_dependency)):
    """Create a new module in a course"""
    # Verify course exists and user is the instructor
    course = await db.courses.find_one({"id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    
    if course["instructor_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Only the course instructor can add modules")
    
    # Get the next order number
    existing_modules = course.get("modules", [])
    next_order = len(existing_modules) + 1
    
    # Create module
    module_dict = module_data.dict()
    module_dict["id"] = str(uuid.uuid4())
    module_dict["order"] = next_order
    module_dict["lessons"] = []
    module_dict["created_at"] = datetime.utcnow()
    module_dict["updated_at"] = datetime.utcnow()
    
    # Add module to course
    await db.courses.update_one(
        {"id": course_id},
        {"$push": {"modules": module_dict}}
    )
    
    # Return response
    response_dict = module_dict.copy()
    response_dict["total_lessons"] = 0
    response_dict["total_duration"] = 0
    
    return ModuleResponse(**response_dict)

@api_router.get("/courses/{course_id}/modules", response_model=List[ModuleResponse])
async def get_course_modules(course_id: str):
    """Get all modules for a course"""
    course = await db.courses.find_one({"id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    
    modules = course.get("modules", [])
    
    # Enrich modules with additional data
    module_responses = []
    for module in modules:
        module_dict = module.copy()
        module_dict["course_id"] = course_id
        module_dict["total_lessons"] = len(module.get("lessons", []))
        module_dict["total_duration"] = sum(
            lesson.get("duration_minutes", 0) for lesson in module.get("lessons", [])
        )
        
        # Convert lessons to response format
        lessons = []
        for lesson in module.get("lessons", []):
            lesson_dict = lesson.copy()
            if "created_at" not in lesson_dict:
                lesson_dict["created_at"] = datetime.utcnow()
            if "updated_at" not in lesson_dict:
                lesson_dict["updated_at"] = datetime.utcnow()
            lessons.append(LessonResponse(**lesson_dict))
        
        module_dict["lessons"] = lessons
        module_responses.append(ModuleResponse(**module_dict))
    
    return module_responses

@api_router.put("/modules/{module_id}", response_model=ModuleResponse)
async def update_module(module_id: str, module_data: ModuleUpdate, current_user: dict = Depends(get_current_user_dependency)):
    """Update a module"""
    # Find course containing the module
    course = await db.courses.find_one({"modules.id": module_id})
    if not course:
        raise HTTPException(status_code=404, detail="Module not found")
    
    if course["instructor_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Only the course instructor can update modules")
    
    # Update module in course
    update_data = {f"modules.$.{k}": v for k, v in module_data.dict(exclude_unset=True).items()}
    update_data["modules.$.updated_at"] = datetime.utcnow()
    
    await db.courses.update_one(
        {"id": course["id"], "modules.id": module_id},
        {"$set": update_data}
    )
    
    # Return updated module
    updated_course = await db.courses.find_one({"id": course["id"]})
    updated_module = next(m for m in updated_course["modules"] if m["id"] == module_id)
    
    response_dict = updated_module.copy()
    response_dict["course_id"] = course["id"]
    response_dict["total_lessons"] = len(updated_module.get("lessons", []))
    response_dict["total_duration"] = sum(
        lesson.get("duration_minutes", 0) for lesson in updated_module.get("lessons", [])
    )
    
    return ModuleResponse(**response_dict)

@api_router.delete("/modules/{module_id}")
async def delete_module(module_id: str, current_user: dict = Depends(get_current_user_dependency)):
    """Delete a module"""
    # Find course containing the module
    course = await db.courses.find_one({"modules.id": module_id})
    if not course:
        raise HTTPException(status_code=404, detail="Module not found")
    
    if course["instructor_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Only the course instructor can delete modules")
    
    # Remove module from course
    await db.courses.update_one(
        {"id": course["id"]},
        {"$pull": {"modules": {"id": module_id}}}
    )
    
    # Delete associated lesson progress
    await db.lesson_progress.delete_many({"course_id": course["id"], "lesson_id": {"$in": [lesson["id"] for lesson in course.get("modules", []) if lesson["id"] == module_id]}})
    
    return {"message": "Module deleted successfully"}

# Lesson APIs
@api_router.post("/modules/{module_id}/lessons", response_model=LessonResponse)
async def create_lesson(module_id: str, lesson_data: LessonCreate, current_user: dict = Depends(get_current_user_dependency)):
    """Create a new lesson in a module"""
    # Find course and module
    course = await db.courses.find_one({"modules.id": module_id})
    if not course:
        raise HTTPException(status_code=404, detail="Module not found")
    
    if course["instructor_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Only the course instructor can add lessons")
    
    # Find the specific module
    module = next(m for m in course["modules"] if m["id"] == module_id)
    
    # Get the next order number
    existing_lessons = module.get("lessons", [])
    next_order = len(existing_lessons) + 1
    
    # Create lesson
    lesson_dict = lesson_data.dict()
    lesson_dict["id"] = str(uuid.uuid4())
    lesson_dict["order"] = next_order
    lesson_dict["created_at"] = datetime.utcnow()
    lesson_dict["updated_at"] = datetime.utcnow()
    
    # Add lesson to module
    await db.courses.update_one(
        {"id": course["id"], "modules.id": module_id},
        {"$push": {"modules.$.lessons": lesson_dict}}
    )
    
    return LessonResponse(**lesson_dict)

@api_router.get("/lessons/{lesson_id}", response_model=LessonResponse)
async def get_lesson(lesson_id: str):
    """Get a specific lesson"""
    course = await db.courses.find_one({"modules.lessons.id": lesson_id})
    if not course:
        raise HTTPException(status_code=404, detail="Lesson not found")
    
    # Find the lesson
    for module in course["modules"]:
        for lesson in module.get("lessons", []):
            if lesson["id"] == lesson_id:
                lesson_dict = lesson.copy()
                if "created_at" not in lesson_dict:
                    lesson_dict["created_at"] = datetime.utcnow()
                if "updated_at" not in lesson_dict:
                    lesson_dict["updated_at"] = datetime.utcnow()
                return LessonResponse(**lesson_dict)
    
    raise HTTPException(status_code=404, detail="Lesson not found")

@api_router.put("/lessons/{lesson_id}", response_model=LessonResponse)
async def update_lesson(lesson_id: str, lesson_data: LessonUpdate, current_user: dict = Depends(get_current_user_dependency)):
    """Update a lesson"""
    course = await db.courses.find_one({"modules.lessons.id": lesson_id})
    if not course:
        raise HTTPException(status_code=404, detail="Lesson not found")
    
    if course["instructor_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Only the course instructor can update lessons")
    
    # Update lesson in nested structure
    update_fields = {}
    for key, value in lesson_data.dict(exclude_unset=True).items():
        update_fields[f"modules.$[module].lessons.$[lesson].{key}"] = value
    
    update_fields["modules.$[module].lessons.$[lesson].updated_at"] = datetime.utcnow()
    
    await db.courses.update_one(
        {"id": course["id"]},
        {"$set": update_fields},
        array_filters=[
            {"module.lessons.id": lesson_id},
            {"lesson.id": lesson_id}
        ]
    )
    
    # Return updated lesson
    return await get_lesson(lesson_id)

@api_router.delete("/lessons/{lesson_id}")
async def delete_lesson(lesson_id: str, current_user: dict = Depends(get_current_user_dependency)):
    """Delete a lesson"""
    course = await db.courses.find_one({"modules.lessons.id": lesson_id})
    if not course:
        raise HTTPException(status_code=404, detail="Lesson not found")
    
    if course["instructor_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Only the course instructor can delete lessons")
    
    # Remove lesson from module
    await db.courses.update_one(
        {"id": course["id"]},
        {"$pull": {"modules.$[].lessons": {"id": lesson_id}}}
    )
    
    # Delete associated progress
    await db.lesson_progress.delete_many({"lesson_id": lesson_id})
    
    return {"message": "Lesson deleted successfully"}

# Course Structure and Learning APIs
@api_router.get("/courses/{course_id}/structure", response_model=CourseStructure)
async def get_course_structure(course_id: str, current_user: dict = Depends(get_current_user_dependency)):
    """Get complete course structure with user progress"""
    course = await db.courses.find_one({"id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    
    # Get user's enrollment and progress
    enrollment = await db.enrollments.find_one({
        "user_id": current_user["id"],
        "course_id": course_id
    })
    
    if not enrollment:
        raise HTTPException(status_code=403, detail="You must be enrolled to access course structure")
    
    # Get user's lesson progress
    user_progress = {}
    progress_records = await db.lesson_progress.find({
        "user_id": current_user["id"],
        "course_id": course_id
    }).to_list(None)
    
    for progress in progress_records:
        user_progress[progress["lesson_id"]] = {
            "completed": progress.get("completed", False),
            "time_spent": progress.get("time_spent_minutes", 0),
            "last_position": progress.get("last_position"),
            "completion_date": progress.get("completion_date")
        }
    
    # Get modules with lessons
    modules = await get_course_modules(course_id)
    
    # Calculate totals
    total_lessons = sum(len(module.lessons) for module in modules)
    total_duration = sum(
        lesson.duration_minutes or 0 
        for module in modules 
        for lesson in module.lessons
    )
    
    return CourseStructure(
        course=course,
        modules=modules,
        total_modules=len(modules),
        total_lessons=total_lessons,
        total_duration=total_duration,
        user_progress=user_progress
    )

@api_router.get("/courses/{course_id}/learn", response_model=LearningSession)
async def get_learning_session(course_id: str, lesson_id: Optional[str] = None, current_user: dict = Depends(get_current_user_dependency)):
    """Get learning session with current lesson and navigation"""
    course = await db.courses.find_one({"id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    
    # Check enrollment
    enrollment = await db.enrollments.find_one({
        "user_id": current_user["id"],
        "course_id": course_id
    })
    
    if not enrollment:
        raise HTTPException(status_code=403, detail="You must be enrolled to access this course")
    
    # Get user progress
    progress_records = await db.lesson_progress.find({
        "user_id": current_user["id"],
        "course_id": course_id
    }).to_list(None)
    
    user_progress = {p["lesson_id"]: p for p in progress_records}
    
    # Get all lessons in order
    all_lessons = []
    for module in course.get("modules", []):
        for lesson in sorted(module.get("lessons", []), key=lambda x: x.get("order", 0)):
            all_lessons.append(lesson)
    
    # Determine current lesson
    current_lesson = None
    if lesson_id:
        current_lesson = next((l for l in all_lessons if l["id"] == lesson_id), None)
    else:
        # Find first incomplete lesson or first lesson
        for lesson in all_lessons:
            if lesson["id"] not in user_progress or not user_progress[lesson["id"]].get("completed", False):
                current_lesson = lesson
                break
        
        if not current_lesson and all_lessons:
            current_lesson = all_lessons[0]
    
    # Find next and previous lessons
    current_index = next((i for i, l in enumerate(all_lessons) if l["id"] == current_lesson["id"]), 0) if current_lesson else 0
    
    next_lesson = all_lessons[current_index + 1] if current_index + 1 < len(all_lessons) else None
    previous_lesson = all_lessons[current_index - 1] if current_index > 0 else None
    
    # Convert to response format
    next_lesson_response = LessonResponse(**next_lesson) if next_lesson else None
    previous_lesson_response = LessonResponse(**previous_lesson) if previous_lesson else None
    
    return LearningSession(
        course_id=course_id,
        current_lesson_id=current_lesson["id"] if current_lesson else None,
        user_progress=user_progress,
        next_lesson=next_lesson_response,
        previous_lesson=previous_lesson_response
    )

# Progress Tracking (enhanced)
@api_router.post("/progress")
async def update_progress(progress_data: ProgressUpdate, current_user: dict = Depends(get_current_user_dependency)):
    """Update lesson progress (authenticated endpoint)"""
    # Find the lesson to get course_id
    course = await db.courses.find_one({"modules.lessons.id": progress_data.lesson_id})
    if not course:
        raise HTTPException(status_code=404, detail="Lesson not found")
    
    # Update or create progress record
    existing_progress = await db.lesson_progress.find_one({
        "user_id": current_user["id"],
        "lesson_id": progress_data.lesson_id
    })
    
    if existing_progress:
        # Update existing progress
        update_data = progress_data.dict()
        update_data["updated_at"] = datetime.utcnow()
        if progress_data.completed and not existing_progress.get("completed"):
            update_data["completion_date"] = datetime.utcnow()
        
        await db.lesson_progress.update_one(
            {"id": existing_progress["id"]},
            {"$set": update_data}
        )
    else:
        # Create new progress record
        progress_dict = progress_data.dict()
        progress_dict["user_id"] = current_user["id"]
        progress_dict["course_id"] = course["id"]
        progress_dict["id"] = str(uuid.uuid4())
        if progress_data.completed:
            progress_dict["completion_date"] = datetime.utcnow()
        progress_dict["created_at"] = datetime.utcnow()
        progress_dict["updated_at"] = datetime.utcnow()
        
        progress_obj = LessonProgress(**progress_dict)
        await db.lesson_progress.insert_one(progress_obj.dict())
    
    # Update overall course progress
    await update_course_progress(current_user["id"], course["id"])
    
    return {"message": "Progress updated successfully"}

@api_router.get("/courses/{course_id}/progress")
async def get_course_progress(course_id: str, current_user: dict = Depends(get_current_user_dependency)):
    """Get user's progress for a specific course"""
    # Verify enrollment
    enrollment = await db.enrollments.find_one({
        "user_id": current_user["id"],
        "course_id": course_id
    })
    
    if not enrollment:
        raise HTTPException(status_code=403, detail="You must be enrolled to view progress")
    
    # Get progress records
    progress_records = await db.lesson_progress.find({
        "user_id": current_user["id"],
        "course_id": course_id
    }).to_list(None)
    
    return {
        "course_id": course_id,
        "overall_progress": enrollment.get("progress_percentage", 0),
        "last_accessed": enrollment.get("last_accessed"),
        "lesson_progress": progress_records
    }

async def update_course_progress(user_id: str, course_id: str):
    """Update overall course progress percentage"""
    course = await db.courses.find_one({"id": course_id})
    if not course:
        return
    
    # Get total lessons in course
    total_lessons = sum(len(module.get("lessons", [])) for module in course.get("modules", []))
    if total_lessons == 0:
        return
    
    # Get completed lessons
    completed_lessons = await db.lesson_progress.count_documents({
        "user_id": user_id,
        "course_id": course_id,
        "completed": True
    })
    
    # Calculate progress percentage
    progress_percentage = (completed_lessons / total_lessons) * 100
    
    # Update enrollment progress
    await db.enrollments.update_one(
        {"user_id": user_id, "course_id": course_id},
        {
            "$set": {
                "progress_percentage": progress_percentage,
                "last_accessed": datetime.utcnow()
            }
        }
    )

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()