import asyncio
import datetime
import json
import os
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, List, Optional, Sequence
from zipfile import ZIP_DEFLATED, ZipFile

import uvicorn
import yt_dlp
from fastapi import Depends, FastAPI, HTTPException, Query, Security
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask


# ----------------------------
# Auth settings
# ----------------------------

DEFAULT_API_KEY_HEADER_NAME = "X-API-Key"
DEFAULT_API_KEY_ENABLED_ENV = "API_KEY_AUTH_ENABLED"
DEFAULT_MASTER_API_KEY_ENV = "API_MASTER_KEY"


def _env_truthy(value: Optional[str], *, default: bool = False) -> bool:
    """
    Parse common truthy/falsey strings from environment variables.
    """
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default


class AuthConfig(BaseModel):
    """
    Authentication configuration loaded from environment variables.

    - enabled: global kill-switch for API key auth
    - master_key: master API key value used for authentication
    - header_name: header used to pass key (default X-API-Key)
    """

    enabled: bool = Field(default=False)
    master_key: Optional[str] = Field(default=None)
    header_name: str = Field(default=DEFAULT_API_KEY_HEADER_NAME)

    @classmethod
    def from_env(cls) -> "AuthConfig":
        enabled = _env_truthy(os.getenv(DEFAULT_API_KEY_ENABLED_ENV), default=False)
        master_key = os.getenv(DEFAULT_MASTER_API_KEY_ENV)
        header_name = os.getenv("API_KEY_HEADER_NAME", DEFAULT_API_KEY_HEADER_NAME).strip()
        return cls(enabled=enabled, master_key=master_key, header_name=header_name)


auth_config = AuthConfig.from_env()

# Create a header extractor using the configured header name.
# auto_error=False so we can return consistent errors ourselves.
api_key_header = APIKeyHeader(name=auth_config.header_name, auto_error=False)


async def require_api_key(api_key: Optional[str] = Security(api_key_header)) -> None:
    """
    Global API key dependency.
    - If auth is disabled, allow requests through.
    - If enabled, require header match to master key.
    """
    if not auth_config.enabled:
        return

    if not auth_config.master_key:
        raise HTTPException(
            status_code=500,
            detail=(
                f"API key auth is enabled but {DEFAULT_MASTER_API_KEY_ENV} is not set."
            ),
        )

    if not api_key or api_key != auth_config.master_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


# ----------------------------
# Utilities
# ----------------------------

def normalize_string(value: str, max_length: int = 200) -> str:
    """
    Trim whitespace, replace unsafe filename characters with underscores,
    and cap length to keep filenames manageable.
    """
    value = value.strip()
    unsafe_chars = ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]
    for ch in unsafe_chars:
        value = value.replace(ch, "_")

    if len(value) > max_length:
        value = value[: max_length - 3] + "..."
    return value


def ensure_dir(path: str) -> str:
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


# ----------------------------
# Domain models
# ----------------------------

class JobType(str, Enum):
    video = "video"
    subtitles = "subtitles"
    audio = "audio"


class Task(BaseModel):
    id: str
    job_type: JobType
    url: str

    # The client requested base_output_path; server writes to task_output_path.
    base_output_path: str
    task_output_path: str

    # "format" is used as a stable key for de-duplication; it can be a real yt-dlp format string
    # or a synthesized string for subtitles/audio settings.
    format: str

    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class DownloadRequest(BaseModel):
    url: str
    output_path: str = "./downloads"
    format: str = "bestvideo+bestaudio/best"
    quiet: bool = False


class SubtitlesRequest(BaseModel):
    url: str
    output_path: str = "./downloads"
    languages: List[str] = Field(default_factory=lambda: ["en", "en.*"])
    write_automatic: bool = True
    write_manual: bool = True
    convert_to: Optional[str] = "srt"
    quiet: bool = False


class AudioRequest(BaseModel):
    url: str
    output_path: str = "./downloads"
    audio_format: str = "mp3"
    audio_quality: Optional[str] = None
    quiet: bool = False


# ----------------------------
# Persistence (SQLite)
# ----------------------------

class State:
    def __init__(self, db_file: str = "tasks.db"):
        self.tasks: Dict[str, Task] = {}
        self.db_file = db_file
        self._init_db()
        self._load_tasks()

    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db_file)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                url TEXT NOT NULL,
                base_output_path TEXT NOT NULL,
                task_output_path TEXT NOT NULL,
                format TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT,
                error TEXT,
                timestamp TEXT NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()

    def _load_tasks(self) -> None:
        try:
            conn = sqlite3.connect(self.db_file)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, job_type, url, base_output_path, task_output_path,
                       format, status, result, error
                FROM tasks
                """
            )
            for row in cur.fetchall():
                (
                    task_id,
                    job_type,
                    url,
                    base_output_path,
                    task_output_path,
                    fmt,
                    status,
                    result_json,
                    error,
                ) = row
                result = json.loads(result_json) if result_json else None
                self.tasks[task_id] = Task(
                    id=task_id,
                    job_type=JobType(job_type),
                    url=url,
                    base_output_path=base_output_path,
                    task_output_path=task_output_path,
                    format=fmt,
                    status=status,
                    result=result,
                    error=error,
                )
            conn.close()
        except Exception as exc:
            print(f"Error loading tasks from database: {exc}")

    def _save_task(self, task: Task) -> None:
        try:
            self.tasks[task.id] = task
            conn = sqlite3.connect(self.db_file)
            cur = conn.cursor()

            timestamp = datetime.datetime.now().isoformat()
            result_json = json.dumps(task.result) if task.result else None

            cur.execute(
                """
                INSERT OR REPLACE INTO tasks
                (id, job_type, url, base_output_path, task_output_path, format,
                 status, result, error, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.job_type.value,
                    task.url,
                    task.base_output_path,
                    task.task_output_path,
                    task.format,
                    task.status,
                    result_json,
                    task.error,
                    timestamp,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            print(f"Error saving task to database: {exc}")

    def add_task(self, job_type: JobType, url: str, base_output_path: str, fmt: str) -> str:
        task_id = str(uuid.uuid4())

        base = Path(base_output_path)
        task_dir = base / task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        task = Task(
            id=task_id,
            job_type=job_type,
            url=url,
            base_output_path=str(base),
            task_output_path=str(task_dir),
            format=fmt,
            status="pending",
        )
        self._save_task(task)
        return task_id

    def get_task(self, task_id: str) -> Optional[Task]:
        return self.tasks.get(task_id)

    def update_task(
        self,
        task_id: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        task = self.tasks.get(task_id)
        if not task:
            return

        task.status = status
        if result is not None:
            task.result = result
        if error is not None:
            task.error = error

        self._save_task(task)

    def list_tasks(self) -> List[Task]:
        return list(self.tasks.values())


state = State()


# ----------------------------
# yt-dlp service
# ----------------------------

class YtDlpService:
    @staticmethod
    def get_info(url: str, quiet: bool = False) -> Dict[str, Any]:
        opts = {"quiet": quiet, "no_warnings": quiet, "skip_download": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return ydl.sanitize_info(info)

    @staticmethod
    def list_formats(url: str) -> List[Dict[str, Any]]:
        info = YtDlpService.get_info(url=url, quiet=True)
        return info.get("formats", []) if info else []

    @staticmethod
    def download_video(url: str, output_path: str, fmt: str, quiet: bool) -> Dict[str, Any]:
        ensure_dir(output_path)
        outtmpl = str(Path(output_path) / "%(title).180s.%(ext)s")
        ydl_opts = {
            "outtmpl": outtmpl,
            "quiet": quiet,
            "no_warnings": quiet,
            "format": fmt,
            "no_abort_on_error": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.sanitize_info(info)

    @staticmethod
    def download_audio(
        url: str,
        output_path: str,
        audio_format: str,
        audio_quality: Optional[str],
        quiet: bool,
    ) -> Dict[str, Any]:
        ensure_dir(output_path)
        outtmpl = str(Path(output_path) / "%(title).180s.%(ext)s")
        ydl_opts: Dict[str, Any] = {
            "outtmpl": outtmpl,
            "quiet": quiet,
            "no_warnings": quiet,
            "format": "bestaudio/best",
            "extractaudio": True,
            "audioformat": audio_format,
            "no_abort_on_error": True,
        }
        if audio_quality is not None:
            ydl_opts["audioquality"] = audio_quality

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.sanitize_info(info)

    @staticmethod
    def download_subtitles(
        url: str,
        output_path: str,
        languages: Sequence[str],
        write_manual: bool,
        write_automatic: bool,
        convert_to: Optional[str],
        quiet: bool,
    ) -> Dict[str, Any]:
        ensure_dir(output_path)
        outtmpl = str(Path(output_path) / "%(title).180s.%(ext)s")
        ydl_opts: Dict[str, Any] = {
            "outtmpl": outtmpl,
            "quiet": quiet,
            "no_warnings": quiet,
            "skip_download": True,
            "subtitleslangs": list(languages),
            "no_abort_on_error": True,
        }
        if write_manual:
            ydl_opts["writesubtitles"] = True
        if write_automatic:
            ydl_opts["writeautomaticsub"] = True
        if convert_to:
            ydl_opts["convertsubtitles"] = convert_to

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.sanitize_info(info)


service = YtDlpService()


# ----------------------------
# Async execution
# ----------------------------

async def run_in_threadpool(func, *args, **kwargs):
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as executor:
        return await loop.run_in_executor(executor, lambda: func(*args, **kwargs))


async def process_task(task_id: str, job_type: JobType, payload: Dict[str, Any]) -> None:
    try:
        if job_type == JobType.video:
            result = await run_in_threadpool(service.download_video, **payload)
        elif job_type == JobType.audio:
            result = await run_in_threadpool(service.download_audio, **payload)
        elif job_type == JobType.subtitles:
            result = await run_in_threadpool(service.download_subtitles, **payload)
        else:
            raise ValueError(f"Unsupported job type: {job_type}")

        state.update_task(task_id, "completed", result=result)
    except Exception as exc:
        state.update_task(task_id, "failed", error=str(exc))


# ----------------------------
# File endpoints (generic)
# ----------------------------

def _require_completed_task(task_id: str) -> Task:
    task = state.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
    if task.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Task is not completed yet. Current status: {task.status}",
        )
    return task


def list_task_files(task: Task) -> List[Path]:
    task_dir = Path(task.task_output_path)
    if not task_dir.exists():
        return []
    files = [p for p in task_dir.iterdir() if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


# ----------------------------
# FastAPI
# ----------------------------

# Apply the API key dependency globally to all endpoints via dependencies=[...]
# When auth_config.enabled is False, the dependency becomes a no-op.
app = FastAPI(
    title="yt-dlp API",
    description="API for downloading videos, audio, and subtitles using yt-dlp",
    dependencies=[Depends(require_api_key)],
)


@app.post("/download", response_class=JSONResponse)
async def api_download_video(request: DownloadRequest):
    existing = next(
        (
            t
            for t in state.tasks.values()
            if t.job_type == JobType.video
            and t.url == request.url
            and t.base_output_path == str(Path(request.output_path))
            and t.format == request.format
        ),
        None,
    )
    if existing:
        return {"status": "success", "task_id": existing.id}

    task_id = state.add_task(JobType.video, request.url, request.output_path, request.format)
    task = state.get_task(task_id)
    assert task is not None

    asyncio.create_task(
        process_task(
            task_id=task_id,
            job_type=JobType.video,
            payload={
                "url": request.url,
                "output_path": task.task_output_path,
                "fmt": request.format,
                "quiet": request.quiet,
            },
        )
    )
    return {"status": "success", "task_id": task_id}


@app.post("/audio", response_class=JSONResponse)
async def api_download_audio(request: AudioRequest):
    fmt_key = f"audio:{request.audio_format}:q={request.audio_quality}"
    existing = next(
        (
            t
            for t in state.tasks.values()
            if t.job_type == JobType.audio
            and t.url == request.url
            and t.base_output_path == str(Path(request.output_path))
            and t.format == fmt_key
        ),
        None,
    )
    if existing:
        return {"status": "success", "task_id": existing.id}

    task_id = state.add_task(JobType.audio, request.url, request.output_path, fmt_key)
    task = state.get_task(task_id)
    assert task is not None

    asyncio.create_task(
        process_task(
            task_id=task_id,
            job_type=JobType.audio,
            payload={
                "url": request.url,
                "output_path": task.task_output_path,
                "audio_format": request.audio_format,
                "audio_quality": request.audio_quality,
                "quiet": request.quiet,
            },
        )
    )
    return {"status": "success", "task_id": task_id}


@app.post("/subtitles", response_class=JSONResponse)
async def api_download_subtitles(request: SubtitlesRequest):
    fmt_key = (
        f"subs:{','.join(request.languages)}:"
        f"manual={request.write_manual}:auto={request.write_automatic}:conv={request.convert_to}"
    )
    existing = next(
        (
            t
            for t in state.tasks.values()
            if t.job_type == JobType.subtitles
            and t.url == request.url
            and t.base_output_path == str(Path(request.output_path))
            and t.format == fmt_key
        ),
        None,
    )
    if existing:
        return {"status": "success", "task_id": existing.id}

    task_id = state.add_task(JobType.subtitles, request.url, request.output_path, fmt_key)
    task = state.get_task(task_id)
    assert task is not None

    asyncio.create_task(
        process_task(
            task_id=task_id,
            job_type=JobType.subtitles,
            payload={
                "url": request.url,
                "output_path": task.task_output_path,
                "languages": request.languages,
                "write_manual": request.write_manual,
                "write_automatic": request.write_automatic,
                "convert_to": request.convert_to,
                "quiet": request.quiet,
            },
        )
    )
    return {"status": "success", "task_id": task_id}


@app.get("/task/{task_id}", response_class=JSONResponse)
async def get_task_status(task_id: str):
    task = state.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")

    data: Dict[str, Any] = {
        "id": task.id,
        "job_type": task.job_type,
        "url": task.url,
        "status": task.status,
        "base_output_path": task.base_output_path,
        "task_output_path": task.task_output_path,
    }
    if task.status == "completed" and task.result:
        data["result"] = task.result
    if task.status == "failed" and task.error:
        data["error"] = task.error

    return {"status": "success", "data": data}


@app.get("/tasks", response_class=JSONResponse)
async def list_all_tasks():
    return {"status": "success", "data": state.list_tasks()}


@app.get("/info", response_class=JSONResponse)
async def api_get_video_info(url: str = Query(..., description="Video URL")):
    try:
        return {"status": "success", "data": service.get_info(url=url, quiet=True)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/formats", response_class=JSONResponse)
async def api_list_formats(url: str = Query(..., description="Video URL")):
    try:
        return {"status": "success", "data": service.list_formats(url)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/task/{task_id}/files", response_class=JSONResponse)
async def api_task_files(task_id: str):
    task = _require_completed_task(task_id)
    files = list_task_files(task)
    return {
        "status": "success",
        "data": [{"name": f.name, "size_bytes": f.stat().st_size} for f in files],
    }


@app.get("/task/{task_id}/file", response_class=FileResponse)
async def api_task_file(task_id: str, name: str = Query(..., description="Exact filename from /task/{task_id}/files")):
    task = _require_completed_task(task_id)
    allow = {p.name: p for p in list_task_files(task)}
    if name not in allow:
        raise HTTPException(status_code=404, detail="File not found for this task")

    p = allow[name]
    return FileResponse(path=str(p), filename=p.name, media_type="application/octet-stream")


@app.get("/task/{task_id}/zip", response_class=FileResponse)
async def api_task_zip(task_id: str):
    task = _require_completed_task(task_id)
    files = list_task_files(task)
    if not files:
        raise HTTPException(status_code=404, detail="No files found to zip")

    tmp = NamedTemporaryFile(delete=False, suffix=".zip")
    tmp_path = Path(tmp.name)
    tmp.close()

    def cleanup() -> None:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

    try:
        with ZipFile(tmp_path, "w", compression=ZIP_DEFLATED) as zf:
            for f in files:
                zf.write(f, arcname=f.name)

        return FileResponse(
            path=str(tmp_path),
            filename=f"task-{task_id}.zip",
            media_type="application/zip",
            background=BackgroundTask(cleanup),
        )
    except Exception as exc:
        cleanup()
        raise HTTPException(status_code=500, detail=f"Failed to create zip: {exc}")


def start_api() -> None:
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    print("Starting yt-dlp API server...")
    start_api()
