# yt-dlp-api Project Overview

Context for AI agents working on this project.

## Project Overview

yt-dlp-api is a RESTful API service that provides a web interface to yt-dlp (a YouTube/media downloader). It runs as a FastAPI application with async task processing and persistent storage.

## Tech Stack

- **Backend:** FastAPI, Python 3.11+
- **Database:** SQLite (for task persistence)
- **Downloader:** yt-dlp library
- **Testing:** pytest, pytest-asyncio, httpx
- **Linting/Formatting:** ruff
- **Type Checking:** mypy
- **CI/CD:** GitHub Actions

## Project Structure

```
yt-dlp-api/
├── main.py                 # Single-file FastAPI application (1,378 lines)
├── tests/                  # Test suite (157 tests, 75% coverage)
│   ├── conftest.py         # Shared pytest fixtures
│   ├── test_utils.py       # Utility function tests
│   ├── test_config.py      # Configuration class tests
│   ├── test_state.py       # Database/state tests
│   ├── test_retry.py       # Retry logic tests
│   └── test_api.py         # API endpoint integration tests
├── .github/workflows/      # CI/CD workflows
│   ├── docker-image.yml    # Docker build & publish
│   └── test.yml            # Test automation
├── Dockerfile              # Multi-stage distroless build
├── requirements.txt        # Production dependencies
├── requirements-test.txt   # Test dependencies
├── pyproject.toml          # pytest, ruff, mypy, coverage config
└── Makefile                # Developer convenience commands
```

## Architecture

### Core Components ([main.py](main.py))

1. **Configuration** - Environment-based config for auth, cookies, retry logic
2. **State** - SQLite-backed task persistence with in-memory cache
3. **YtDlpService** - Wrapper around yt-dlp for video/audio/subtitle operations
4. **Retry Logic** - Exponential backoff with jitter for rate-limited requests
5. **API Endpoints** - FastAPI routes for downloads, task queries, file retrieval

### Key Classes/Functions

| Component | Lines | Purpose |
|-----------|-------|---------|
| `State` | 515-674 | Database operations, task CRUD |
| `YtDlpService` | 681-931 | yt-dlp wrapper methods |
| `retry_with_backoff` | 468-508 | Retry logic with exponential backoff |
| `resolve_task_base_dir` | 210-232 | Path traversal prevention |
| `resolve_cookie_file` | 239-279 | Cookie file validation |

### API Endpoints

- `POST /download` - Video download
- `POST /audio` - Audio-only download
- `POST /subtitles` - Subtitles download
- `GET /task/{id}` - Task status
- `GET /tasks` - List all tasks
- `GET /info` - Video metadata
- `GET /formats` - Available formats
- `POST /cookies/upload` - Upload cookies file
- `GET /task/{id}/files` - List task files
- `GET /task/{id}/file` - Download specific file
- `GET /task/{id}/zip` - Download all files as ZIP

## Development Workflow

### Commands

```bash
make install-dev    # Install test dependencies
make lint           # Run ruff linter
make format         # Format code with ruff
make test           # Run fast unit tests
make test-cov       # Run tests with coverage report
make check          # Run lint + tests
make clean          # Remove test artifacts
make docker-build   # Build Docker image locally
```

### Testing

- Tests use temporary databases and output directories (isolated)
- API tests mock yt-dlp calls (no actual downloads in CI)
- Run specific test types: `pytest -m unit` or `pytest -m integration`

### Docker

- Base image: `dhi.io` (authenticated registry)
- Multi-arch: amd64, arm64
- Distroless runtime (Chainguard)
- Non-root user (UID 65532)

## Important Notes

- **Security:** Hardened against path traversal in output paths and cookie file handling
- **Rate Limiting:** Built-in retry with exponential backoff for HTTP 429/5xx errors
- **Task Deduplication:** Identical requests return existing task ID
- **Partial Success:** Subtitle downloads support partial success (some languages may fail)
- **CI:** Docker build requires `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` secrets for dhi.io
