# API Reference Documentation

This document provides a comprehensive reference for all implemented functions and classes in the Social Media AI Manager project.

## Table of Contents
- [Authentication](#authentication)
- [Users](#users)
- [Roles and Permissions](#roles-and-permissions)
- [AI Analysis](#ai-analysis)
- [Data Schemas](#data-schemas)
- [Database Models](#database-models)
- [Services](#services)
- [Utils](#utils)
- [Project Dependencies](#project-dependencies)

## Authentication

### `auth.py`

#### Functions

##### `login_access_token`
```python
async def login_access_token(form_data: OAuth2PasswordRequestForm = Depends())
```
OAuth2 compatible token login, get access and refresh tokens.

**Parameters:**
- `form_data`: OAuth2 password request form with username/email and password

**Returns:**
- `TokenWithRefresh`: Access and refresh tokens

##### `refresh_access_token`
```python
async def refresh_access_token(refresh_token: str = Depends(oauth2_scheme))
```
Refresh an access token using a refresh token.

**Parameters:**
- `refresh_token`: Valid refresh token

**Returns:**
- `Token`: New access token

##### `create_user`
```python
async def create_user(new_user: UserCreate)
```
Register a new user.

**Parameters:**
- `new_user`: User creation data

**Returns:**
- `TokenWithRefresh`: Access and refresh tokens for the new user

## Users

### `user.py`

#### Functions

##### `get_current_user`
```python
async def get_current_user(current_user: User = Depends(get_authenticated_user))
```
Get current user information.

**Returns:**
- `UserInDB`: Current user details

##### `get_users`
```python
async def get_users(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, le=100, description="Maximum number of records to return"),
    current_user: User = Depends(get_authenticated_user)
)
```
Get paginated list of users (admin only).

**Parameters:**
- `skip`: Number of records to skip
- `limit`: Maximum number of records to return
- `current_user`: Authenticated user (must be admin)

**Returns:**
- `List[UserInDB]`: List of users

##### `update_user`
```python
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    current_user: User = Depends(get_authenticated_user)
)
```
Update user information.

**Parameters:**
- `user_id`: ID of the user to update
- `user_data`: Updated user data
- `current_user`: Authenticated user (must be admin or the same user)

**Returns:**
- `UserInDB`: Updated user information

##### `delete_user`
```python
async def delete_user(
    user_id: int,
    current_user: User = Depends(get_authenticated_user)
)
```
Delete a user (admin only).

**Parameters:**
- `user_id`: ID of the user to delete
- `current_user`: Authenticated user (must be admin)

## Roles and Permissions

### `roles.py`

#### Functions

##### `list_roles`
```python
async def list_roles()
```
Get paginated list of all roles with their permissions.

**Returns:**
- `Page[RoleResponse]`: Paginated list of roles

##### `get_role`
```python
async def get_role(role_name: str, db: Session = Depends(get_db))
```
Get a specific role with its permissions by name.

**Parameters:**
- `role_name`: Name of the role to retrieve
- `db`: Database session

**Returns:**
- `RoleResponse`: Role details with permissions

##### `update_role_permissions`
```python
async def update_role_permissions(
    role_name: str,
    permissions_request: PermissionsRequest,
    db: Session = Depends(get_db)
)
```
Update permissions for a role using the specified strategy.

**Parameters:**
- `role_name`: Name of the role to update
- `permissions_request`: New permissions and update strategy
- `db`: Database session

**Returns:**
- `dict`: Update result with added/removed permissions


## AI Analysis

### `ai.py`

#### Functions

##### `analyze_sentiment`
```python
@router.post("analyze/sentiment", response_model=SentimentAnalysisResult)
def analyze_sentiment(
    analysis_in: AIAnalysisRequest,
    current_user: User = Depends(get_authenticated_user),
    background_tasks: BackgroundTasks,
)
```
Analyze sentiment of text content.

**Parameters:**
- `analysis_in`: Text to analyze and options
- `current_user`: Authenticated user
- `background_tasks`: Background tasks handler

**Returns:**
- `SentimentAnalysisResult`: Sentiment analysis results

## Database Models

### `models/__init__.py`

#### Classes

##### `User`
User model representing application users.

**Attributes:**
- `id`: Primary key
- `username`: Unique username
- `email`: User's email
- `hashed_password`: Hashed password
- `is_active`: Whether the user is active
- `is_superuser`: Whether the user has admin privileges
- `created_at`: Timestamp of user creation

##### `Role`
Role model for role-based access control.

**Attributes:**
- `id`: Primary key
- `name`: Role name
- `description`: Role description
- `permissions`: Relationship to permissions

## Services

### `services/user/auth.py`

#### Functions

##### `get_authenticated_user`
```python
async def get_authenticated_user(
    token: str = Depends(oauth2_scheme),
    token_type: str = "access"
)
```
Get the currently authenticated user from the token.

**Parameters:**
- `token`: JWT token
- `token_type`: Type of token ("access" or "refresh")

**Returns:**
- `User`: Authenticated user

## Data Schemas

### User Schemas (`app/schemas/user.py`)

#### `UserBase`
Base schema for user data.

**Fields:**
- `username`: str (required)
- `email`: EmailStr (required)
- `is_active`: bool = True
- `is_superuser`: bool = False

#### `UserCreate`
Schema for creating a new user (extends UserBase).

**Additional Fields:**
- `password`: str (required, min_length=8)

#### `UserUpdate`
Schema for updating user information.

**Fields:**
- `email`: Optional[EmailStr] = None
- `password`: Optional[str] = None
- `is_active`: Optional[bool] = None
- `is_superuser`: Optional[bool] = None

#### `UserInDB`
Complete user information (extends UserBase).

**Additional Fields:**
- `id`: int
- `hashed_password`: str
- `created_at`: datetime

### Role Schemas (`app/schemas/role.py`)

#### `RoleBase`
Base schema for role data.

**Fields:**
- `name`: str (required)
- `description`: Optional[str] = None

#### `RoleCreate`
Schema for creating a new role.

#### `RoleUpdate`
Schema for updating role information.

#### `RoleInDB`
Complete role information.

**Additional Fields:**
- `id`: int
- `created_at`: datetime
- `permissions`: List[PermissionInDB]

### AI Analysis Schemas (`app/schemas/ai.py`)

#### `AIAnalysisRequest`
Schema for AI analysis requests.

**Fields:**
- `text`: str (required)
- `source_id`: Optional[str] = None
- `source_type`: Optional[str] = None
- `background`: bool = False

#### `SentimentAnalysisResult`
Schema for sentiment analysis results.

**Fields:**
- `sentiment`: str ("positive", "neutral", or "negative")
- `confidence`: float (0.0 to 1.0)
- `analysis_date`: datetime

## Project Dependencies

### Core Dependencies
- **FastAPI** - Web framework for building APIs
- **SQLAlchemy** - SQL toolkit and ORM
- **Pydantic** - Data validation and settings management
- **Alembic** - Database migration tool
- **Python-JOSE** - JWT implementation
- **Passlib** - Password hashing
- **PyJWT** - JSON Web Token implementation

### Database
- **asyncpg** - Async PostgreSQL client
- **aioredis** - Async Redis client
- **SQLAlchemy-Utils** - Various utility functions for SQLAlchemy

### Social Media Integrations
- **vk-api** - VK API client
- **python-telegram-bot** - Telegram Bot API client

### AI/ML
- **transformers** - State-of-the-art NLP models
- **torch** - PyTorch for deep learning
- **numpy** - Numerical computing

### Development Tools
- **pytest** - Testing framework
- **pytest-asyncio** - Async test support
- **black** - Code formatter
- **isort** - Import sorter
- **mypy** - Static type checker
- **pre-commit** - Git hooks

### Deployment
- **Docker** - Containerization
- **uvicorn** - ASGI server
- **gunicorn** - WSGI HTTP Server

## Utils

### `utils/token.py`

#### Functions

##### `create_access_token`
```python
def create_access_token(subject: Union[str, Any], expires_delta: timedelta = None) -> tuple[str, datetime]
```
Create a new JWT access token.

**Parameters:**
- `subject`: Subject to include in the token
- `expires_delta`: Optional expiration time delta

**Returns:**
- `tuple`: (token, expiration_datetime)

##### `create_tokens_pair`
```python
def create_tokens_pair(subject: Union[str, Any]) -> dict[str, Any]
```
Create both access and refresh tokens.

**Parameters:**
- `subject`: Subject to include in the tokens

**Returns:**
- `dict`: Dictionary with access_token, refresh_token, and expiration info
