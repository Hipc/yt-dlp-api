# yt-dlp API Service

> **Quick Start:** `docker run -p 8000:8000 hipc/yt-dlp` to get started instantly!

[English](README.md) | [中文](README_CN.md)

A RESTful API service built with FastAPI and yt-dlp for video information retrieval and downloading.

## Features

- Asynchronous download processing
- Multiple video format support
- Persistent task status storage
- Download progress reporting
- Task controls: stop, restart, delete
- Detailed video information queries
- RESTful API design

## Requirements

- Python 3.7+
- FastAPI
- yt-dlp
- uvicorn
- pydantic
- sqlite3

## Quick Start

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Start the server:
```bash
python main.py
```

The server will start at http://localhost:8000

## API Key (Optional)

Set `YTDLP_API_KEY` to enable API key checks. If not set, no API key is required.

Send the key using one of the following headers:

- `X-API-Key: <your_key>`
- `Authorization: Bearer <your_key>`

## API Documentation

### 1. Submit Download Task

**Request:**
```http
POST /download
```

**Request Body:**
```json
{
    "url": "video_url",
    "output_path": "./downloads",  // Optional, defaults to "./downloads"
    "format": "bestvideo+bestaudio/best",  // Optional, defaults to best quality
    "quiet": false  // Optional, whether to download quietly
}
```

**Response:**
```json
{
    "status": "success",
    "task_id": "task_id"
}
```

### 2. Get Task Status

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
        "url": "video_url",
        "status": "pending/downloading/canceling/canceled/completed/failed",
        "progress": {
            "percent": 12.34,
            "downloaded_bytes": 12345678,
            "total_bytes": 98765432,
            "total_bytes_estimate": 98765432,
            "speed": 123456,
            "eta": 120,
            "elapsed": 12.3,
            "filename": "/path/to/file"
        },
        "result": {}, // Contains download info when completed
        "error": "error message" // Contains error when failed/canceled
    }
}
```

### 3. List All Tasks

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
            "url": "video_url",
            "status": "task_status",
            "progress": {}
            // ... other task information
        }
    ]
}
```

### 4. Get Video Information

**Request:**
```http
GET /info?url={video_url}
```

**Response:**
```json
{
    "status": "success",
    "data": {
        // Detailed video information
    }
}
```

### 5. List Available Video Formats

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
            "resolution": "resolution",
            // ... other format information
        }
    ]
}
```

### 6. Download Completed Task's Video File

**Request:**
```http
GET /download/{task_id}/file
```

**Response:**
- Success: Returns video file stream directly
- Failure: Returns error message
```json
{
    "detail": "error message"
}
```

### 7. Stop a Running Task

**Request:**
```http
POST /task/{task_id}/stop
```

**Response:**
```json
{
    "status": "success",
    "data": {
        "id": "task_id",
        "status": "canceling"
    }
}
```

### 8. Restart a Finished Task

**Request:**
```http
POST /task/{task_id}/restart?quiet=false
```

**Response:**
```json
{
    "status": "success",
    "data": {
        "id": "task_id",
        "status": "pending"
    }
}
```

### 9. Delete a Task (and Files)

**Request:**
```http
DELETE /task/{task_id}
```

**Response:**
```json
{
    "status": "success",
    "data": {
        "id": "task_id",
        "cancel_requested": true,
        "deleted_files": 3
    }
}
```

## Error Handling

All API endpoints return appropriate HTTP status codes and detailed error messages when errors occur:

- 404: Resource not found
- 400: Bad request parameters
- 500: Internal server error

## Data Persistence

The service uses SQLite database to store task information, with the database file defaulting to `tasks.db`. Task information includes:

- Task ID
- Video URL
- Output path
- Download format
- Task status
- Download result
- Download progress
- Error message
- Timestamp

## Docker Support

The project includes a Dockerfile and can be built and run using the following commands:

```bash
# Build image
docker build -t yt-dlp-api .

# Run container
docker run -p 8000:8000 -v $(pwd)/downloads:/app/downloads yt-dlp-api
```

## Important Notes

1. Ensure sufficient disk space for storing downloaded videos
2. Configure appropriate security measures in production environment
3. Comply with video platform terms of service and copyright regulations
4. Deleting a task also deletes downloaded files and partial cache files under its output directory
