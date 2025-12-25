# yt-dlp API Service

> **Quick Start:** `docker run -p 8000:8000 zarguell/yt-dlp-api:latest` to get started instantly!

A RESTful API service built with FastAPI and yt-dlp for video information retrieval and downloading. Refactored the original upstream https://github.com/Hipc/yt-dlp-api to support subtitle and audio specific endpoints, as well as a generic file operation endpoint to get and retrieve specific files per task.

## Features
- Asynchronous download processing (task-based)
- Video download (format selection supported)
- Audio-only download (extract audio)
- Subtitles-only download (manual and/or auto captions)
- Persistent task status storage (SQLite)
- Detailed video information queries
- Generic artifact retrieval:
  - List produced files
  - Download a specific file
  - Download a ZIP of all task files
- Optional API Key authentication (env-controlled)
- Hardened output directory handling (prevents path traversal by restricting outputs to a server-controlled root) 

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
When authentication is enabled, **all endpoints are protected**, including:
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
  "quiet": false
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
  "quiet": false
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
  "quiet": false
}
```

**Response:**
```
{
  "status": "success",
  "task_id": "task_id"
}
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
    "job_type": "video/audio/subtitles",
    "url": "video_url",
    "status": "pending/completed/failed",
    "base_output_path": "/absolute/or/relative/server/path/to/SERVER_OUTPUT_ROOT/<label>",
    "task_output_path": "/absolute/or/relative/server/path/to/SERVER_OUTPUT_ROOT/<label>/{task_id}",
    "result": {},
    "error": "error message"
  }
}
```

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
      "job_type": "video/audio/subtitles",
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

## Generic artifact retrieval (applies to ALL task types)

### 8. List Produced Files for a Task
Use this after the task reaches `completed`.

**Request:**
```
GET /task/{task_id}/files
```

### 9. Download a Specific File
Pick the `name` from `/task/{task_id}/files`.

**Request:**
```
GET /task/{task_id}/file?name={filename}
```

### 10. Download ZIP of All Files for a Task
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
- Job type (`video`, `audio`, `subtitles`)
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

## Important Notes
1. Ensure sufficient disk space for storing downloaded files.
2. For production use, add rate limiting.
3. Comply with video platform terms of service and copyright regulations.
