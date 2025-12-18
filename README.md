# yt-dlp API Service

> **Quick Start:** `docker run -p 8000:8000 zarguell/yt-dlp` to get started instantly!

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

## Requirements
- Python 3.7+
- FastAPI
- yt-dlp
- uvicorn
- pydantic
- sqlite3
- (Recommended) ffmpeg/ffprobe available in PATH for audio extraction and subtitle conversion

## Quick Start
1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Start the server:
```bash
python main.py
```

The server will start at: http://localhost:8000

## Output layout (important)
All downloads are isolated per task. If a request uses:
- `output_path = ./downloads`
the service will write files into:
- `./downloads/{task_id}/...`

This prevents filename collisions and makes it safe to list/zip/download artifacts for a single task.

## API Documentation

### 1. Submit Video Download Task
**Request:**
```http
POST /download
```

**Request Body:**
```json
{
  "url": "video_url",
  "output_path": "./downloads",
  "format": "bestvideo+bestaudio/best",
  "quiet": false
}
```

**Response:**
```json
{
  "status": "success",
  "task_id": "task_id"
}
```

### 2. Submit Audio-Only Task
Downloads best available audio and converts/extracts to the chosen format (ffmpeg recommended).

**Request:**
```http
POST /audio
```

**Request Body:**
```json
{
  "url": "video_url",
  "output_path": "./downloads",
  "audio_format": "mp3",
  "audio_quality": null,
  "quiet": false
}
```

**Response:**
```json
{
  "status": "success",
  "task_id": "task_id"
}
```

### 3. Submit Subtitles-Only Task
Downloads subtitles without downloading the media file.

**Request:**
```http
POST /subtitles
```

**Request Body:**
```json
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
```json
{
  "status": "success",
  "task_id": "task_id"
}
```

### 4. Get Task Status
**Request:**
```http
GET /task/{task_id}
```

**Response:**
```json
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
```http
GET /tasks
```

**Response:**
```json
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
```http
GET /info?url={video_url}
```

**Response:**
```json
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
```http
GET /formats?url={video_url}
```

**Response:**
```json
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
```http
GET /task/{task_id}/files
```

**Response:**
```json
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
```http
GET /task/{task_id}/file?name={filename}
```

**Response:**
- Success: returns file stream directly
- Failure: returns JSON error:
```json
{ "detail": "error message" }
```

### 10. Download ZIP of All Files for a Task
This bundles all files inside the task folder into one zip download. The zip is created temporarily and removed after sending the response using a background cleanup task.[1]

**Request:**
```http
GET /task/{task_id}/zip
```

**Response:**
- Success: returns `application/zip` attachment
- Failure: returns JSON error

## Error Handling
All API endpoints return appropriate HTTP status codes and detailed error messages:

- 404: Resource not found
- 400: Bad request parameters / task not completed
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
```bash
# Build image
docker build -t yt-dlp-api .

# Run container (persist downloads on host)
docker run -p 8000:8000 -v $(pwd)/downloads:/app/downloads yt-dlp-api
```

## Important Notes
1. Ensure sufficient disk space for storing downloaded files.
2. For production use, add authentication, rate limiting, and restrict allowed output paths.
3. Comply with video platform terms of service and copyright regulations.

