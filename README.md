# yt-dlp API Service

> **Quick Start:** `docker run -p 8000:8000 zarguell/yt-dlp-api:latest` to get started instantly!

A RESTful API service built with FastAPI and yt-dlp for video information retrieval and downloading. Refactored the original upstream https://github.com/Hipc/yt-dlp-api to support subtitle and audio specific endpoints, as well as a generic file operation endpoint to get and retrieve specific files per task.

## Features
- Asynchronous download processing (task-based)
- Video download (format selection supported)
- Audio-only download (extract audio)
- Subtitles-only download (manual and/or auto captions)
- Enhanced subtitle download with policy-based language selection
  - Automatic English subtitle selection (best_one, all_english, or explicit modes)
  - Manual vs automatic subtitle preference control
  - Format normalization (SRT, VTT, or both)
  - Intelligent language ranking with regex support
- Persistent task status storage (SQLite)
- Detailed video information queries
- Generic artifact retrieval:
  - List produced files
  - Download a specific file
  - Download a ZIP of all task files
- Optional API Key authentication (env-controlled)
- Hardened output directory handling (prevents path traversal by restricting outputs to a server-controlled root)
- Built-in rate limiting to avoid being blocked by video platforms
- Optional cookie-based authentication for accessing premium/restricted content

## Requirements
- Python 3.10+ (3.11+ recommended)
- FastAPI
- yt-dlp
- uvicorn
- pydantic
- sqlite3
- (Recommended) ffmpeg/ffprobe available in PATH for audio extraction and subtitle conversion

## Configuration (env vars)

### Server configuration

#### Host and Port
- `HOST` (optional)
  - Host address to bind the uvicorn server to.
  - Default: `0.0.0.0` (all interfaces)
- `PORT` (optional)
  - Port number for the API server.
  - Default: `8000`
- `LOG_LEVEL` (optional)
  - Logging level for the application.
  - Default: `INFO`
  - Valid values: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
- `MAX_WORKERS` (optional)
  - Maximum number of worker threads for processing downloads.
  - Default: `4`

### Retry Configuration

The service includes built-in retry logic with exponential backoff for handling rate limits (HTTP 429) and transient errors. These environment variables set default values that apply to all requests unless overridden.

#### Default Retry Environment Variables
- `DEFAULT_MAX_RETRIES` (optional)
  - Maximum number of retry attempts for failed downloads.
  - Default: `3`
- `DEFAULT_RETRY_BACKOFF` (optional)
  - Initial backoff delay in seconds before first retry.
  - Default: `5.0`
- `DEFAULT_RETRY_BACKOFF_MULTIPLIER` (optional)
  - Exponential backoff multiplier (e.g., 2.0 = double the delay each retry).
  - Default: `2.0`
- `DEFAULT_RETRY_JITTER` (optional)
  - Add random jitter to backoff delays to avoid thundering herd problems.
  - Default: `true`
  - Valid values: `true`, `false`, `1`, `0`

**How retry works:**
- On retryable errors (HTTP 429, 500, 502, 503, 504), the service automatically retries
- Backoff delay = `DEFAULT_RETRY_BACKOFF * (DEFAULT_RETRY_BACKOFF_MULTIPLIER ^ attempt_number)`
- With defaults: 5s, 10s, 20s delays for retries 1, 2, 3
- Jitter adds ±25% random variation to avoid synchronized retries

#### Per-Request Override
You can override default retry settings per request:
```json
{
  "url": "https://www.youtube.com/watch?v=xxx",
  "max_retries": 5,
  "retry_backoff": 10.0
}
```

### Cookie Configuration

The service supports using cookies for authentication, which is useful for accessing premium content, age-restricted videos, or avoiding rate limits. Cookies can be configured globally via environment variables or uploaded per-request.

#### Cookie Environment Variables
- `COOKIES_FILE` (optional)
  - Path to a cookies.txt file to use for all downloads by default.
  - Can be an absolute path or relative to the current working directory.
  - Default: `None` (no cookies used)
  - Example: `/path/to/cookies.txt` or `./cookies/cookies.txt`

- `COOKIES_DIR` (optional)
  - Directory where uploaded cookie files are stored.
  - Also used as the base directory for relative paths in per-request cookie files.
  - Default: `./cookies`

#### Using Cookies Globally (Local/Docker)

**Local Python:**
```bash
export COOKIES_FILE=/path/to/cookies.txt
python main.py
```

**Docker with mounted cookies:**
```bash
docker run -p 8000:8000 \
  -v "$(pwd)/cookies.txt:/app/cookies.txt:ro" \
  -e COOKIES_FILE=/app/cookies.txt \
  zarguell/yt-dlp-api:latest
```

**Docker with cookies directory:**
```bash
docker run -p 8000:8000 \
  -v "$(pwd)/cookies:/app/cookies:ro" \
  -e COOKIES_FILE=/app/cookies/youtube.txt \
  zarguell/yt-dlp-api:latest
```

#### Uploading Cookies Per-Request

You can upload a cookies.txt file via the API and reference it in download requests.

**Step 1: Upload cookies**
```bash
curl -X POST http://localhost:8000/cookies/upload \
  -F "file=@/path/to/cookies.txt"
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "cookie_file": "abc123_cookies.txt",
    "path": "/app/cookies/abc123_cookies.txt",
    "size_bytes": 1234
  }
}
```

**Step 2: Use uploaded cookies in download**
```bash
curl -X POST http://localhost:8000/download \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.youtube.com/watch?v=xxx",
    "cookie_file": "abc123_cookies.txt"
  }'
```

#### Cookie Priority

When both global and per-request cookies are configured:
1. Per-request `cookie_file` parameter takes precedence
2. If not provided, falls back to `COOKIES_FILE` environment variable
3. If neither is set, no cookies are used

#### Cookie File Format

The service expects cookies in the standard Netscape cookie format used by browser extensions like "Get cookies.txt LOCALLY" or "ExportThisCookie":

```
# Netscape HTTP Cookie File
# This file is compatible with yt-dlp

.youtube.com	TRUE	/	FALSE	1735689700	SID	xxxxxxxxx
.youtube.com	TRUE	/	FALSE	1735689700	HSID	xxxxxxxxx
.youtube.com	TRUE	/	TRUE	0	PREF	xxxxxxxxx
```

#### Tips for Using Cookies

1. **For YouTube**: Use browser extensions to export cookies from your authenticated session
2. **Regular refresh**: Cookies expire and need to be refreshed periodically
3. **Security**: Never commit cookies.txt to version control
4. **Docker mounts**: Use `:ro` (read-only) flag when mounting cookie files for security
5. **Rate limiting**: Using cookies can help avoid rate limits, especially for subtitles

### Output storage (important)
To prevent path traversal vulnerabilities, the API does **not** allow clients to write to arbitrary filesystem paths. Instead, the request `output_path` field is treated as a **folder label** (a simple subdirectory name) that is created under a server-controlled root directory. 

#### Environment variables
- `SERVER_OUTPUT_ROOT` (optional)
  - Root directory where all task folders are created.
  - Default: `./downloads` (relative to the process working directory inside the container/app).

#### Client `output_path` behavior (breaking change)
- `output_path` is now a **folder label**, not a filesystem path.
- Examples:
  - `"output_path": "default"` → writes under `${SERVER_OUTPUT_ROOT}/default/{task_id}/...`
  - `"output_path": "projectA"` → writes under `${SERVER_OUTPUT_ROOT}/projectA/{task_id}/...`
- Invalid values (rejected with HTTP 400):
  - Anything containing `/` or `\`
  - Anything containing `..`
  - Empty strings (treated as `"default"`)

## Authentication (API Key)

The service supports API key authentication using a single master key loaded from an environment variable, and a global toggle to enable/disable auth. FastAPI extracts the key from a header using `APIKeyHeader`, and a global dependency enforces it across all routes. 

### Environment variables
- `API_KEY_AUTH_ENABLED`
  - When set to a truthy value (`true`, `1`, `yes`, `on`), API key auth is enabled.
  - When disabled/absent, no API key is required.
- `API_MASTER_KEY`
  - The master API key value clients must send.
  - Required when `API_KEY_AUTH_ENABLED` is enabled.
- `API_KEY_HEADER_NAME` (optional)
  - Header name to read the key from.
  - Defaults to `X-API-Key`.

### Header
Send the API key in:
- `X-API-Key: <your master key>`

### Important
Even if authentication is enabled, the following endpoints will still be accessible without API Key:
- `/docs`
- `/redoc`
- `/openapi.json`

### Example (curl)
```
export API_KEY_AUTH_ENABLED=true
export API_MASTER_KEY="super-secret"
# optional:
# export API_KEY_HEADER_NAME="X-API-Key"

curl -H "X-API-Key: super-secret" \
  "http://localhost:8000/info?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

## Quick Start
1. Install dependencies:
```
pip install -r requirements.txt
```

2. Start the server:
```
python main.py
```

The server will start at: http://localhost:8000

If API key auth is enabled, remember to include `X-API-Key` on every request (including browser access to `/docs`).

## Output layout (important)
All downloads are isolated per task under `SERVER_OUTPUT_ROOT` to prevent collisions and to support safe artifact listing/zip/download. 

If a request uses:
- `output_path = "default"`

and `SERVER_OUTPUT_ROOT` is:
- `./downloads`

the service will write files into:
- `./downloads/default/{task_id}/...`

## API Documentation

### 1. Submit Video Download Task
**Request:**
```
POST /download
```

**Request Body:**
```
{
  "url": "video_url",
  "output_path": "default",
  "format": "bestvideo+bestaudio/best",
  "quiet": false,
  "cookie_file": "optional_cookies.txt"
}
```

**Response:**
```
{
  "status": "success",
  "task_id": "task_id"
}
```

### 2. Submit Audio-Only Task
Downloads best available audio and converts/extracts to the chosen format (ffmpeg recommended).

**Request:**
```
POST /audio
```

**Request Body:**
```
{
  "url": "video_url",
  "output_path": "default",
  "audio_format": "mp3",
  "audio_quality": null,
  "quiet": false,
  "cookie_file": "optional_cookies.txt"
}
```

**Response:**
```
{
  "status": "success",
  "task_id": "task_id"
}
```

### 3. Submit Subtitles-Only Task
Downloads subtitles without downloading the media file.

**Request:**
```
POST /subtitles
```

**Request Body:**
```
{
  "url": "video_url",
  "output_path": "default",
  "languages": ["en", "en.*"],
  "write_automatic": true,
  "write_manual": true,
  "convert_to": "srt",
  "quiet": false,
  "cookie_file": "optional_cookies.txt"
}
```

**Response:**
```
{
  "status": "success",
  "task_id": "task_id"
}
```

Note: As a recommendation, limit languages to only necessary to avoid rate limiting. From my testing, without cookies configured, Google will Rate Limit via 429 after ~2 requests for subtitles.

### 3.1. Enhanced Subtitles Download (V2)
A policy-based subtitle endpoint that automatically selects the best English subtitles without requiring manual language codes.

**Request:**
```
POST /v2/subtitles
```

**Request Body:**
```json
{
  "url": "video_url",
  "output_path": "default",
  "english_mode": "best_one",
  "prefer": "manual_then_auto",
  "formats": "srt",
  "english_rank": ["en", "en-US", "en-GB", "en.*"]
}
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | string | (required) | Video URL |
| `output_path` | string | `"default"` | Output folder label |
| `english_mode` | string | `"best_one"` | English selection policy |
| `languages` | array | `[]` | Explicit language list (only when `english_mode="explicit"`) |
| `prefer` | string | `"manual_then_auto"` | Manual vs automatic subtitle preference |
| `formats` | string | `"srt"` | Output format: `srt`, `vtt`, or `both` |
| `english_rank` | array | `["en", "en-US", "en-GB", "en.*"]` | Language priority order for `best_one` mode |
| `quiet` | boolean | `false` | Suppress yt-dlp output |
| `cookie_file` | string | `null` | Cookies file path |

**English Mode Options:**

| Value | Description |
|-------|-------------|
| `best_one` | Automatically selects the single best English subtitle track based on `english_rank` preference |
| `all_english` | Downloads all English variants (en, en-US, en-GB, etc.) |
| `explicit` | Uses the exact `languages` list provided |

**Subtitle Preference Options:**

| Value | Description |
|-------|-------------|
| `manual_then_auto` | Prefer manual subtitles, fall back to automatic captions |
| `auto_only` | Only download automatic captions |
| `manual_only` | Only download manual subtitles |

**Format Options:**

| Value | Description |
|-------|-------------|
| `srt` | Return SRT format only (converted if necessary) |
| `vtt` | Return WebVTT format only |
| `both` | Return both SRT and VTT formats |

**Response:**
```json
{
  "status": "success",
  "task_id": "task_id"
}
```

**Examples:**

*Best English subtitle (automatic):*
```bash
curl -X POST http://localhost:8000/v2/subtitles \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  }'
```

*All English variants in both formats:*
```bash
curl -X POST http://localhost:8000/v2/subtitles \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "english_mode": "all_english",
    "formats": "both"
  }'
```

*Automatic captions only (VTT):*
```bash
curl -X POST http://localhost:8000/v2/subtitles \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "prefer": "auto_only",
    "formats": "vtt"
  }'
```

*Specific languages with explicit mode:*
```bash
curl -X POST http://localhost:8000/v2/subtitles \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "english_mode": "explicit",
    "languages": ["es", "fr", "de"]
  }'
```

*Custom language ranking (prefer en-GB):*
```bash
curl -X POST http://localhost:8000/v2/subtitles \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "english_rank": ["en-GB", "en", "en-US"]
  }'
```

### 4. Get Task Status
**Request:**
```
GET /task/{task_id}
```

**Response:**
```
{
  "status": "success",
  "data": {
    "id": "task_id",
    "job_type": "video/audio/subtitles/subtitles_v2",
    "url": "video_url",
    "status": "pending/completed/failed/partial",
    "base_output_path": "/absolute/or/relative/server/path/to/SERVER_OUTPUT_ROOT/<label>",
    "task_output_path": "/absolute/or/relative/server/path/to/SERVER_OUTPUT_ROOT/<label>/{task_id}",
    "result": {},
    "error": "error message"
  }
}
```

**Task Statuses:**
- `pending`: Task is queued and waiting to process
- `running`: Task is currently processing
- `completed`: Task finished successfully with all requested content downloaded
- `partial`: Some content was downloaded but not all (e.g., some subtitles failed due to rate limiting)
- `failed`: Task failed completely (no content downloaded or non-retryable error)

**Partial Success Handling:**
For subtitle downloads, if some subtitles download successfully before hitting a rate limit (HTTP 429), the task status will be `partial`. You can still access the downloaded files via the artifact endpoints. The `result` field will include:
- `downloaded`: List of successfully downloaded files with metadata
- `failed`: List of errors for failed downloads
- `partial`: `true` indicating partial success

### 5. List All Tasks
**Request:**
```
GET /tasks
```

**Response:**
```
{
  "status": "success",
  "data": [
    {
      "id": "task_id",
      "job_type": "video/audio/subtitles/subtitles_v2",
      "url": "video_url",
      "status": "task_status",
      "base_output_path": "/.../SERVER_OUTPUT_ROOT/<label>",
      "task_output_path": "/.../SERVER_OUTPUT_ROOT/<label>/{task_id}"
    }
  ]
}
```

### 6. Get Video Information (No download)
**Request:**
```
GET /info?url={video_url}
```

### 7. List Available Video Formats (No download)
**Request:**
```
GET /formats?url={video_url}
```

### 8. Upload Cookies File
Upload a cookies.txt file for authenticated downloads.

**Request:**
```
POST /cookies/upload
```

**Request (multipart/form-data):**
```
file=@/path/to/cookies.txt
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "cookie_file": "uuid_cookies.txt",
    "path": "/app/cookies/uuid_cookies.txt",
    "size_bytes": 1234
  }
}
```

**Usage:**
Use the returned `cookie_file` name in download requests:
```bash
curl -X POST http://localhost:8000/download \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.youtube.com/watch?v=xxx",
    "cookie_file": "uuid_cookies.txt"
  }'
```

## Generic artifact retrieval (applies to ALL task types)

### 9. List Produced Files for a Task
Use this after the task reaches `completed`.

**Request:**
```
GET /task/{task_id}/files
```

### 10. Download a Specific File
Pick the `name` from `/task/{task_id}/files`.

**Request:**
```
GET /task/{task_id}/file?name={filename}
```

### 11. Download ZIP of All Files for a Task
**Request:**
```
GET /task/{task_id}/zip
```

## Error Handling
All API endpoints return appropriate HTTP status codes and detailed error messages:
- 404: Resource not found
- 400: Bad request parameters / task not completed / invalid output_path label
- 401: Invalid or missing API key (when auth enabled)
- 500: Internal server error

## Data Persistence
The service uses an SQLite database (`tasks.db`) to store task information, including:
- Task ID
- Job type (`video`, `audio`, `subtitles`, `subtitles_v2`)
- Video URL
- Base output path (resolved server base dir: `${SERVER_OUTPUT_ROOT}/{output_path_label}`) 
- Task output path (actual folder used: `${SERVER_OUTPUT_ROOT}/{output_path_label}/{task_id}`)
- Download format / settings key
- Task status
- Download result (yt-dlp metadata)
- Error message
- Timestamp

## Docker Support

### Docker configuration environment variables

The Docker image supports additional configuration variables:

#### User configuration
- `APP_USER` (optional)
  - Username to run the application process as.
  - Default: `nonroot`
- `APP_UID` (optional)
  - User ID for the application user.
  - Default: `65532`
- `APP_GID` (optional)
  - Group ID for the application user.
  - Default: `65532`

> **Note:** The container runs as a non-privileged user (UID 65532) by default for security. When mounting volumes, ensure the mounted directory has appropriate permissions for this user, or override the user settings via environment variables.

### Default Docker run (no env required)
This works without any extra environment variables because `SERVER_OUTPUT_ROOT` defaults to `./downloads`.
```
docker run -p 8000:8000 zarguell/yt-dlp-api:latest
```

### Custom port and host
```
docker run -p 8080:8080 \
  -e PORT=8080 \
  -e HOST=0.0.0.0 \
  zarguell/yt-dlp-api:latest
```

### Persist downloads on the host (recommended)
Mount a host folder to the container's download root, and (optionally) set `SERVER_OUTPUT_ROOT` to match the mount point.

> **Important:** The default user (UID 65532) must have write permissions to the mounted directory. You may need to adjust permissions on the host or override the user configuration.
```
docker run -p 8000:8000 \
  -e SERVER_OUTPUT_ROOT=/app/downloads \
  -v "$(pwd)/downloads:/app/downloads" \
  zarguell/yt-dlp-api:latest
```

### Persist downloads with custom user/permissions
If you need to match a specific host UID/GID:
```
docker run -p 8000:8000 \
  -e APP_UID=1000 \
  -e APP_GID=1000 \
  -e APP_USER=myuser \
  -v "$(pwd)/downloads:/app/downloads" \
  zarguell/yt-dlp-api:latest
```

### Docker + API Key auth example
```
docker run -p 8000:8000 \
  -e API_KEY_AUTH_ENABLED=true \
  -e API_MASTER_KEY="super-secret" \
  zarguell/yt-dlp-api:latest
```

## Testing

The project includes comprehensive unit and integration tests to ensure code quality and functionality.

### Running Tests

**Quick test run:**
```bash
# Install test dependencies
pip install -r requirements-test.txt

# Run unit tests only (fast)
make test
# or
pytest -m "not slow" -v
```

**Run tests with coverage:**
```bash
make test-cov
# or
pytest -v --cov --cov-report=html
```

**Run all tests:**
```bash
make test-all
# or
pytest -v
```

**Using Makefile commands:**
```bash
make help           # Show all available commands
make install-dev    # Install dev dependencies
make lint           # Run linters
make format         # Format code
make test           # Run fast tests
make test-cov       # Run tests with coverage
make check          # Run lint + tests
make clean          # Clean test artifacts
```

### Test Structure

- `tests/test_utils.py` - Unit tests for utility functions
- `tests/test_config.py` - Unit tests for configuration classes
- `tests/test_state.py` - Unit tests for database operations
- `tests/test_retry.py` - Unit tests for retry logic
- `tests/test_api.py` - Integration tests for API endpoints (with mocked yt-dlp)
- `tests/conftest.py` - Shared fixtures and test utilities

### CI/CD

Tests run automatically on:
- Every push to `main` branch
- Pull requests to `main` branch
- Can be triggered manually via workflow dispatch

The CI pipeline includes:
1. Docker build test
2. Linting (ruff) and type checking (mypy)
3. Unit and integration tests with coverage
4. Multi-Python version testing (3.11, 3.12, 3.13)

## Important Notes
1. Ensure sufficient disk space for storing downloaded files.
2. For production use, add rate limiting.
3. Comply with video platform terms of service and copyright regulations.
