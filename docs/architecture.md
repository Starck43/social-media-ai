# 📁 Project Structure

```powershell
social-media-ai/
├── 📁 app/                          # Main application
│   ├── 📁 api/                      # API endpoints (FastAPI)
│   │   └── v1/                      # API version 1
│   │       ├── endpoints/           # Endpoint modules
│   │       │   ├── __init__.py
│   │       │   ├── ai.py            # AI-related endpoints
│   │       │   ├── auth.py          # Authentication endpoints
│   │       │   ├── roles.py         # Role management endpoints
│   │       │   ├── social.py        # Social media integration endpoints
│   │       │   └── user.py          # User management endpoints
│   │       └── __init__.py
│   │
│   ├── 📁 core/                     # Core application components
│   │   ├── __init__.py
│   │   ├── config.py                # Application configuration
│   │   ├── database.py              # Database connection and session management
│   │   ├── decorators.py            # Custom decorators
│   │   ├── hashing.py               # Password hashing utilities
│   │   ├── logger.py                # Logging configuration
│   │   └── permissions.py           # Permission management
│   │
│   ├── 📁 models/                   # Database models and managers
│   │   ├── __init__.py
│   │   ├── user.py                 # User and role models
│   │   └── 📁 managers/             # Model managers
│   │
│   ├── 📁 schemas/                  # Pydantic schemas
│   │
│   ├── 📁 services/                 # Business logic services
│   │   └── user/                   # User-related services
│   │       ├── __init__.py
│   │       └── permissions.py       # Role and permission management
│   │
│   ├── 📁 types/                    # Custom type definitions
│   │
│   ├── 📁 utils/                    # Utility functions
│   │
│   └── main.py                      # FastAPI application entry point
│
├── 📁 cli/                          # Command line interface
│   ├── commands/                   # CLI command modules
│   │   └── roles.py                # Role management commands
│   ├── __init__.py
│   └── main.py                     # CLI entry point
│
├── 📁 scripts/                      # Utility scripts
│   ├── migrations/                 # Database migrations
│   │   ├── __init__.py
│   │   └── deps.py                 # Migration dependencies
│   └── setup/                      # Setup scripts
│
├── 📁 docker/                       # Docker configuration
│   ├── nginx/                      # Nginx configuration
│   │   └── default.conf
│   ├── Dockerfile                  # Application Dockerfile
│   └── docker-compose.yml          # Docker Compose configuration
│
├── 📁 docs/                         # Documentation
│   └── architecture.md             # This file
│
├── 📁 tests/                        # Tests
├── 📁 logs/                         # Application logs
│
├── .dockerignore                   # Docker ignore rules
├── .env                            # Environment variables (gitignored)
├── .env.example                    # Environment variables example
├── .gitignore                      # Git ignore rules
├── README.md                       # Project documentation
├── alembic.ini                     # Alembic configuration
├── docker-compose.yml              # Docker Compose configuration
├── init.sql                        # Database initialization script
├── pyproject.toml                  # Python project configuration
└── requirements.txt                # Python dependencies
```

## 🔑 Key Components

### Core Application (`/app`)
- **API Layer**: RESTful endpoints organized by version
- **Services**: Business logic and domain services
- **Models**: Database models and ORM definitions
- **Core**: Application configuration and shared utilities

### Command Line Interface (`/cli`)

The CLI provides powerful tools for managing roles and permissions. All permission codenames follow the `app_label.model_name.action_type` pattern.

#### Permission Structure Examples:
```
account.user.view        # View user information
account.user.create      # Create new users
account.user.edit        # Edit existing users
account.user.delete      # Delete users

social.post.view         # View social media posts
social.post.create       # Create new posts
social.post.analyze      # Analyze post content
social.post.delete       # Delete posts

dashboard.statistics.view   # View dashboard statistics
dashboard.statistics.export # Export statistics

content.article.publish    # Publish articles
content.media.upload       # Upload media files
```

#### Installation for Development:

To enable the simplified command format, install the package in development mode:

```bash
# Install in development mode
pip install -e .

# Verify installation
cli --help
```

#### Common Commands:

1. **List all roles**
   ```bash
   # After installing in development mode
   cli roles list
   
   # Or using Python module directly
   python -m cli.main roles list
   ```

2. **Show role details**
   ```bash
   # After installing in development mode
   cli roles show 1
   
   # Or using Python module directly
   python -m cli.main roles show 1
   ```

3. **Update role permissions**
   ```bash
   # Replace all permissions
   python -m cli.main roles update admin "account.*" "social.*" --strategy replace
   
   # Add specific permissions
   python -m cli.main roles update editor "social.post.*" --strategy merge
   
   # Use wildcards and exclusions
   python -m cli.main roles update moderator "social.*" "!social.post.delete" --strategy merge
   
   # Update actions for specific resources
   python -m cli.main roles update analyst "dashboard.*.view" --strategy update_actions
   
   # Preview changes without applying them (dry run)
   python -m cli.main roles update admin "account.*" --dry-run
   ```

   **Dry Run Option (`--dry-run`):**
   - Preview changes before applying them
   - No changes will be made to the database
   - Shows which permissions would be added/removed
   - Useful for testing complex permission patterns
   - Example output includes expanded permissions and changes that would be made

4. **Assign default permissions**
   ```bash
   python -m cli.main roles preset
   ```

#### Permission Update Strategies:
- `replace`: Completely replace existing permissions
- `merge`: Add new permissions to existing ones
- `synchronize`: Match exactly the specified permissions (add missing, remove extra)
- `update_actions`: Update action types for existing resources

#### Examples with Wildcards:
- `account.*` - All account-related permissions
- `*.view` - All view permissions across all apps
- `social.post.*` - All post-related permissions in social app
- `!social.post.delete` - Exclude post deletion permission
- `social.* !social.post.delete` - All social permissions except post deletion

### Infrastructure (`/docker`)
- Containerization configuration
- Nginx reverse proxy setup
- Environment configuration


## 🛠 Development

### Prerequisites
- Python 3.10+
- PostgreSQL 13+
- Redis (for caching and task queue)

### Setup

1. **Install dependencies**
   ```bash
   # Install Python dependencies
   pip install -r requirements.txt
   
   # Install development dependencies
   pip install -r requirements-dev.txt
   ```

2. **Database setup**
   ```bash
   # Initialize database (if needed)
   psql -U your_user -d your_db -f init.sql
   
   # Run migrations
   alembic upgrade head
   ```

3. **Environment variables**
   Create a `.env` file in the project root with the following variables:
   ```
   DATABASE_URL=postgresql://user:password@localhost/dbname
   SECRET_KEY=your-secret-key
   DEBUG=True
   ```

### Running the Application

1. **Development server**
   ```bash
   uvicorn app.main:app --reload
   ```
   The API will be available at `http://localhost:8000`
   
2. **CLI Commands**
   ```bash
   # Show available commands
   python -m cli.main --help
   
   # Example: List roles
   python -m cli.main roles list
   ```

3. **Running Tests**
   ```bash
   # Run all tests
   pytest
   
   # Run specific test file
   pytest tests/test_file.py
   
   # Run with coverage report
   pytest --cov=app
   ```

4. **Docker**
   ```bash
   # Build and start containers
   docker-compose up --build
   
   # Run specific service
   docker-compose up -d db redis
   
   # View logs
   docker-compose logs -f
   ```

### Development Workflow

1. **Code Style**
   - Follow PEP 8 guidelines
   - Use type hints for better code documentation
   - Run linters before committing:
     ```bash
     black .
     isort .
     flake8 .
     mypy .
     ```

2. **Git**
   - Create feature branches from `main`
   - Write meaningful commit messages
   - Open pull requests for code review

3. **Documentation**
   - Update documentation when adding new features
   - Keep docstrings up to date
   - Document API endpoints using OpenAPI (automatically generated by FastAPI)

## 🔄 CI/CD

The project includes Docker configuration for containerized deployment. Use the provided `docker-compose.yml` for local development and production deployment.


## 🛠️ Database Migrations

1. **Create a new migration** (after model changes):
   ```bash
   alembic revision --autogenerate -m "Description of changes"
   ```

2. **Apply migrations**:
   ```bash
   alembic upgrade head
   ```

3. **Revert a migration**:
   ```bash
   alembic downgrade -1
   ```

4. **Show migration history**:
   ```bash
   alembic history
   ```

5. **Check current version**:
   ```bash
   alembic current
   ```

6. **Create a data migration** (for data updates):
   ```bash
   alembic revision -m "update_user_roles"
   # Then manually edit the generated migration file
   ```

7. **Run a specific migration**:
   ```bash
   alembic upgrade <revision_id>
   ```
