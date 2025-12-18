# yt-dlp API Service

> **Quick Start:** `docker run -p 8000:8000 zarguell/yt-dlp` to get started instantly!

A RESTful API service built with FastAPI and yt-dlp for video information retrieval and downloading. Refactored the original upstream [https://github.com/Hipc/yt-dlp-api](https://github.com/Hipc/yt-dlp-api) to support subtitle and audio specific endpoints, as well as a generic file operation endpoint to get and retrieve specific files per task.

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

## Requirements
- Python 3.10+ (3.11+ recommended)
- FastAPI
- yt-dlp
- uvicorn
- pydantic
- sqlite3
- (Recommended) ffmpeg/ffprobe available in PATH for audio extraction and subtitle conversion

## Authentication (API Key)

The service supports API key authentication using a single **master key** loaded from an environment variable, and a global toggle to enable/disable auth. FastAPI extracts the key from a header using `APIKeyHeader`, and a global dependency enforces it across all routes. [web:21][web:16]

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

curl -H "X-API-Key: super-secret" "http://localhost:8000/info?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ"
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
All downloads are isolated per task. If a request uses:
- `output_path = ./downloads`
the service will write files into:
- `./downloads/{task_id}/...`

This prevents filename collisions and makes it safe to list/zip/download artifacts for a single task.

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
  "output_path": "./downloads",
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
  "output_path": "./downloads",
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
  "output_path": "./downloads",
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
    "base_output_path": "./downloads",
    "task_output_path": "./downloads/{task_id}",
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
      "base_output_path": "./downloads",
      "task_output_path": "./downloads/{task_id}"
    }
  ]
}
```

### 6. Get Video Information (No download)
**Request:**
```
GET /info?url={video_url}
```

**Response:**
```
{
  "status": "success",
  "data": {
    "title": "…",
    "duration": 123,
    "formats": []
  }
}
```

### 7. List Available Video Formats (No download)
**Request:**
```
GET /formats?url={video_url}
```

**Response:**
```
{
  "status": "success",
  "data": [
    {
      "format_id": "format_id",
      "ext": "file_extension",
      "resolution": "resolution"
    }
  ]
}
```

## Generic artifact retrieval (applies to ALL task types)

### 8. List Produced Files for a Task
Use this after the task reaches `completed`.

**Request:**
```
GET /task/{task_id}/files
```

**Response:**
```
{
  "status": "success",
  "data": [
    { "name": "Some Video Title.mp4", "size_bytes": 12345678 },
    { "name": "Some Video Title.en.srt", "size_bytes": 45678 }
  ]
}
```

### 9. Download a Specific File
Pick the `name` from `/task/{task_id}/files`.

**Request:**
```
GET /task/{task_id}/file?name={filename}
```

**Response:**
- Success: returns file stream directly
- Failure: returns JSON error:
```
{ "detail": "error message" }
```

### 10. Download ZIP of All Files for a Task
This bundles all files inside the task folder into one zip download. The zip is created temporarily and removed after sending the response using a background cleanup task.

**Request:**
```
GET /task/{task_id}/zip
```

**Response:**
- Success: returns `application/zip` attachment
- Failure: returns JSON error

## Error Handling
All API endpoints return appropriate HTTP status codes and detailed error messages:
- 404: Resource not found
- 400: Bad request parameters / task not completed
- 401: Invalid or missing API key (when auth enabled)
- 500: Internal server error

## Data Persistence
The service uses an SQLite database (`tasks.db`) to store task information, including:
- Task ID
- Job type (`video`, `audio`, `subtitles`)
- Video URL
- Base output path (requested by client)
- Task output path (actual folder used: `{base}/{task_id}`)
- Download format / settings key
- Task status
- Download result (yt-dlp metadata)
- Error message
- Timestamp

## Docker Support
```
# Build image
docker build -t yt-dlp-api .

# Run container (persist downloads on host)
docker run -p 8000:8000 -v $(pwd)/downloads:/app/downloads yt-dlp-api
```

### Docker + API Key auth example
```
docker run -p 8000:8000 \
  -e API_KEY_AUTH_ENABLED=true \
  -e API_MASTER_KEY="super-secret" \
  zarguell/yt-dlp
```

## Important Notes
1. Ensure sufficient disk space for storing downloaded files.
2. For production use, add rate limiting and restrict allowed output paths.
3. Comply with video platform terms of service and copyright regulations.
