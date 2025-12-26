import asyncio
import contextvars
import datetime
import json
import logging
import os
import random
import sqlite3
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, TypeVar, cast
from zipfile import ZIP_DEFLATED, ZipFile

import uvicorn
import yt_dlp
from fastapi import Depends, FastAPI, File, HTTPException, Query, Security, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.requests import Request

# ----------------------------
# Logging setup
# ----------------------------

_request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    """Attach request_id to all log records for correlation."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_ctx.get()
        return True


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("yt-dlp-api")
logger.addFilter(RequestIdFilter())


# ----------------------------
# Auth settings
# ----------------------------

DEFAULT_API_KEY_HEADER_NAME = "X-API-Key"
DEFAULT_API_KEY_ENABLED_ENV = "API_KEY_AUTH_ENABLED"
DEFAULT_MASTER_API_KEY_ENV = "API_MASTER_KEY"

# Retry configuration environment variables
DEFAULT_MAX_RETRIES_ENV = "DEFAULT_MAX_RETRIES"
DEFAULT_RETRY_BACKOFF_ENV = "DEFAULT_RETRY_BACKOFF"
DEFAULT_RETRY_BACKOFF_MULTIPLIER_ENV = "DEFAULT_RETRY_BACKOFF_MULTIPLIER"
DEFAULT_RETRY_JITTER_ENV = "DEFAULT_RETRY_JITTER"

# Cookie configuration environment variables
DEFAULT_COOKIES_FILE_ENV = "COOKIES_FILE"


def _env_truthy(value: str | None, *, default: bool = False) -> bool:
    """Parse common truthy/falsey strings from environment variables."""
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default


def _env_int(value: str | None, *, default: int) -> int:
    """Parse integer from environment variable with default."""
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(value: str | None, *, default: float) -> float:
    """Parse float from environment variable with default."""
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


class AuthConfig(BaseModel):
    """
    Authentication configuration loaded from environment variables.

    - enabled: global kill-switch for API key auth
    - master_key: master API key value used for authentication
    - header_name: header used to pass key (default X-API-Key)
    """

    enabled: bool = Field(default=False)
    master_key: str | None = Field(default=None)
    header_name: str = Field(default=DEFAULT_API_KEY_HEADER_NAME)

    @classmethod
    def from_env(cls) -> "AuthConfig":
        enabled = _env_truthy(os.getenv(DEFAULT_API_KEY_ENABLED_ENV), default=False)
        master_key = os.getenv(DEFAULT_MASTER_API_KEY_ENV)
        header_name = os.getenv("API_KEY_HEADER_NAME", DEFAULT_API_KEY_HEADER_NAME).strip()
        cfg = cls(enabled=enabled, master_key=master_key, header_name=header_name)
        logger.info(
            "Auth config loaded enabled=%s header_name=%s master_key_set=%s",
            cfg.enabled,
            cfg.header_name,
            bool(cfg.master_key),
        )
        return cfg


class CookieConfig(BaseModel):
    """
    Cookie configuration loaded from environment variables.

    - cookies_file: path to a cookies.txt file to use for all downloads (optional)
    """

    cookies_file: str | None = Field(default=None)

    @classmethod
    def from_env(cls) -> "CookieConfig":
        cookies_file = os.getenv(DEFAULT_COOKIES_FILE_ENV)
        if cookies_file:
            cookies_file = cookies_file.strip()
            # Verify the file exists
            if not Path(cookies_file).is_file():
                logger.warning("COOKIES_FILE points to non-existent file=%s", cookies_file)
                cookies_file = None
            else:
                logger.info("Cookie config loaded cookies_file=%s", cookies_file)
        cfg = cls(cookies_file=cookies_file)
        return cfg


auth_config = AuthConfig.from_env()
cookie_config = CookieConfig.from_env()
api_key_header = APIKeyHeader(name=auth_config.header_name, auto_error=False)

# default_retry_config will be initialized after RetryConfig is defined


async def require_api_key(api_key: str | None = Security(api_key_header)) -> None:
    """Global API key dependency."""
    if not auth_config.enabled:
        return

    if not auth_config.master_key:
        logger.error(
            "API key auth enabled but master key env var missing env=%s", DEFAULT_MASTER_API_KEY_ENV
        )
        raise HTTPException(
            status_code=500,
            detail=f"API key auth is enabled but {DEFAULT_MASTER_API_KEY_ENV} is not set.",
        )

    if not api_key or api_key != auth_config.master_key:
        logger.warning("Authentication failed (invalid/missing API key)")
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


# ----------------------------
# Output path hardening
# ----------------------------

SERVER_OUTPUT_ROOT_ENV = "SERVER_OUTPUT_ROOT"
DEFAULT_SERVER_OUTPUT_ROOT = "./downloads"
SERVER_OUTPUT_ROOT = Path(os.getenv(SERVER_OUTPUT_ROOT_ENV, DEFAULT_SERVER_OUTPUT_ROOT))

# Cookie upload directory
COOKIES_DIR_ENV = "COOKIES_DIR"
DEFAULT_COOKIES_DIR = "./cookies"
COOKIES_DIR = Path(os.getenv(COOKIES_DIR_ENV, DEFAULT_COOKIES_DIR))
COOKIES_DIR.mkdir(parents=True, exist_ok=True)


def _is_safe_subdir_name(value: str, *, max_length: int = 80) -> bool:
    """Validate an API-provided folder label (single subdirectory)."""
    if not value:
        return False
    if len(value) > max_length:
        return False
    if "/" in value or "\\" in value:
        return False
    if value in {".", ".."}:
        return False
    if ".." in value:
        return False

    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    return all(ch in allowed for ch in value)


def resolve_task_base_dir(client_output_path: str) -> Path:
    """Convert client 'output_path' into a server-controlled base directory."""
    label = client_output_path.strip()
    if label in {"", ".", "./"}:
        label = "default"

    if not _is_safe_subdir_name(label):
        logger.warning("Rejected unsafe output_path label=%r", label)
        raise HTTPException(
            status_code=400,
            detail="Invalid output_path. Provide a simple folder name (no slashes or '..').",
        )

    root = SERVER_OUTPUT_ROOT.resolve(strict=False)
    base = (root / label).resolve(strict=False)

    if not base.is_relative_to(root):
        logger.warning(
            "Rejected output_path outside root label=%r base=%s root=%s", label, base, root
        )
        raise HTTPException(status_code=400, detail="Invalid output_path (outside server root).")

    base.mkdir(parents=True, exist_ok=True)
    logger.debug("Resolved base output dir label=%r base=%s", label, base)
    return base


# ----------------------------
# Utilities
# ----------------------------


def resolve_cookie_file(request_cookie_file: str | None) -> str | None:
    """
    Resolve the cookie file path from request and environment configuration.

    Priority:
    1. Request-specific cookie_file parameter (relative to COOKIES_DIR)
    2. Global COOKIES_FILE environment variable (absolute or relative to COOKIES_DIR)

    All paths are validated to ensure they remain within the COOKIES_DIR to prevent
    path traversal attacks.

    Returns the absolute path to the cookie file, or None if no cookies are configured.
    """
    cookie_file = request_cookie_file or cookie_config.cookies_file

    if not cookie_file:
        return None

    cookie_path = Path(cookie_file)

    # If it's a relative path, treat it as relative to COOKIES_DIR
    if not cookie_path.is_absolute():
        cookie_path = (COOKIES_DIR / cookie_file).resolve(strict=False)
    else:
        # For absolute paths, just resolve it (we'll validate containment next)
        cookie_path = cookie_path.resolve(strict=False)

    # Validate the path doesn't escape COOKIES_DIR
    if not cookie_path.is_relative_to(COOKIES_DIR.resolve(strict=False)):
        logger.warning("Rejected cookie path outside COOKIES_DIR path=%s", cookie_path)
        raise HTTPException(
            status_code=400,
            detail="Cookie file path must be within the cookies directory",
        )

    # Verify the file exists
    if not cookie_path.is_file():
        logger.warning("Cookie file not found path=%s", cookie_path)
        return None

    return str(cookie_path)


def normalize_string(value: str, max_length: int = 200) -> str:
    """Trim whitespace, replace unsafe filename characters with underscores, and cap length."""
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
    base_output_path: str
    task_output_path: str
    format: str
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None


class DownloadRequest(BaseModel):
    url: str
    output_path: str = "default"
    format: str = "bestvideo+bestaudio/best"
    quiet: bool = False
    cookie_file: str | None = Field(
        default=None,
        description="Path to cookies.txt file for authentication (overrides COOKIES_FILE env var)",
    )


class SubtitlesRequest(BaseModel):
    url: str
    output_path: str = "default"
    languages: list[str] = Field(default_factory=lambda: ["en", "en.*"])
    write_automatic: bool = True
    write_manual: bool = True
    convert_to: str | None = "srt"
    quiet: bool = False
    cookie_file: str | None = Field(
        default=None,
        description="Path to cookies.txt file for authentication (overrides COOKIES_FILE env var)",
    )
    max_retries: int | None = Field(
        default=None,
        ge=0,
        description="Maximum number of retry attempts (overrides DEFAULT_MAX_RETRIES env var)",
    )
    retry_backoff: float | None = Field(
        default=None,
        ge=0,
        description="Initial backoff delay in seconds (overrides DEFAULT_RETRY_BACKOFF env var)",
    )


class AudioRequest(BaseModel):
    url: str
    output_path: str = "default"
    audio_format: str = "mp3"
    audio_quality: str | None = None
    quiet: bool = False
    cookie_file: str | None = Field(
        default=None,
        description="Path to cookies.txt file for authentication (overrides COOKIES_FILE env var)",
    )
    max_retries: int | None = Field(
        default=None,
        ge=0,
        description="Maximum number of retry attempts (overrides DEFAULT_MAX_RETRIES env var)",
    )
    retry_backoff: float | None = Field(
        default=None,
        ge=0,
        description="Initial backoff delay in seconds (overrides DEFAULT_RETRY_BACKOFF env var)",
    )


class RetryConfig(BaseModel):
    """Configuration for retry behavior."""

    max_retries: int = Field(
        default_factory=lambda: _env_int(os.getenv(DEFAULT_MAX_RETRIES_ENV), default=3),
        ge=0,
        description="Maximum number of retry attempts",
    )
    backoff_base: float = Field(
        default_factory=lambda: _env_float(os.getenv(DEFAULT_RETRY_BACKOFF_ENV), default=5.0),
        ge=0,
        description="Base backoff delay in seconds",
    )
    backoff_multiplier: float = Field(
        default_factory=lambda: _env_float(
            os.getenv(DEFAULT_RETRY_BACKOFF_MULTIPLIER_ENV), default=2.0
        ),
        ge=1.0,
        description="Exponential backoff multiplier",
    )
    jitter: bool = Field(
        default_factory=lambda: _env_truthy(os.getenv(DEFAULT_RETRY_JITTER_ENV), default=True),
        description="Add random jitter to backoff to avoid thundering herd",
    )
    retryable_http_codes: list[int] = Field(
        default_factory=lambda: [429, 500, 502, 503, 504],
        description="HTTP status codes that trigger retry",
    )

    @classmethod
    def from_env(cls) -> "RetryConfig":
        """Create RetryConfig from environment variables."""
        cfg = cls()
        logger.info(
            "Retry config loaded from env max_retries=%s backoff_base=%s backoff_multiplier=%s jitter=%s",
            cfg.max_retries,
            cfg.backoff_base,
            cfg.backoff_multiplier,
            cfg.jitter,
        )
        return cfg


# Initialize global retry config from environment
default_retry_config = RetryConfig.from_env()


T = TypeVar("T")


# ----------------------------
# Retry utilities
# ----------------------------


def is_retryable_error(error: Exception, retry_config: RetryConfig) -> bool:
    """Check if an error is retryable based on configuration."""
    error_str = str(error).lower()

    # Check for HTTP error codes
    for code in retry_config.retryable_http_codes:
        if f"http error {code}" in error_str or f"httperror: {code}" in error_str:
            return True

    # Check for common retryable error patterns
    retryable_patterns = [
        "too many requests",
        "rate limit",
        "temporary failure",
        "connection reset",
        "connection refused",
        "timed out",
        "timeout",
        "server error",
    ]

    return any(pattern in error_str for pattern in retryable_patterns)


def calculate_backoff(attempt: int, retry_config: RetryConfig) -> float:
    """Calculate exponential backoff delay with optional jitter."""
    base_delay = retry_config.backoff_base
    multiplier = retry_config.backoff_multiplier

    # Exponential backoff: base * (multiplier ^ attempt)
    delay = base_delay * (multiplier**attempt)

    # Add jitter if enabled (±25% random variation)
    if retry_config.jitter:
        jitter_range = delay * 0.25
        delay = delay + random.uniform(-jitter_range, jitter_range)

    return max(0, delay)


def retry_with_backoff(
    func: Callable[..., T],
    retry_config: RetryConfig,
    *args: Any,
    **kwargs: Any,
) -> T:
    """Execute a function with retry logic and exponential backoff."""
    last_error: Exception | None = None

    for attempt in range(retry_config.max_retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_error = e
            is_last_attempt = attempt >= retry_config.max_retries

            if is_last_attempt or not is_retryable_error(e, retry_config):
                logger.warning(
                    "Non-retryable error or max retries exceeded attempt=%d/%d error=%s",
                    attempt + 1,
                    retry_config.max_retries + 1,
                    str(e)[:200],
                )
                raise

            # Calculate backoff and wait
            backoff = calculate_backoff(attempt, retry_config)
            logger.info(
                "Retryable error encountered, retrying after backoff attempt=%d/%d backoff_seconds=%.1f error=%s",
                attempt + 1,
                retry_config.max_retries + 1,
                backoff,
                str(e)[:200],
            )
            time.sleep(backoff)

    # This should never be reached, but mypy needs it
    if last_error:
        raise last_error
    raise RuntimeError("Retry logic failed without raising an exception")


# ----------------------------
# Persistence (SQLite)
# ----------------------------


class State:
    def __init__(self, db_file: str = "tasks.db"):
        self.tasks: dict[str, Task] = {}
        self.db_file = db_file
        self._init_db()
        self._load_tasks()

    def _init_db(self) -> None:
        logger.info("Initializing database db_file=%s", self.db_file)
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
        start = time.monotonic()
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
            rows = cur.fetchall()
            for row in rows:
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
            logger.info(
                "Loaded tasks from database count=%d elapsed_ms=%d",
                len(rows),
                int((time.monotonic() - start) * 1000),
            )
        except Exception:
            logger.exception("Error loading tasks from database db_file=%s", self.db_file)

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
            logger.debug(
                "Saved task task_id=%s status=%s job_type=%s",
                task.id,
                task.status,
                task.job_type.value,
            )
        except Exception:
            logger.exception("Error saving task to database task_id=%s", task.id)

    def add_task(self, job_type: JobType, url: str, base_output_path: str, fmt: str) -> str:
        task_id = str(uuid.uuid4())
        base = resolve_task_base_dir(base_output_path)
        task_dir = (base / task_id).resolve(strict=False)

        if not task_dir.is_relative_to(base.resolve(strict=False)):
            logger.error(
                "Task dir containment check failed task_id=%s base=%s task_dir=%s",
                task_id,
                base,
                task_dir,
            )
            raise HTTPException(status_code=400, detail="Invalid task directory resolution.")

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
        logger.info(
            "Created task task_id=%s job_type=%s base=%s fmt=%s url=%s",
            task_id,
            job_type.value,
            base,
            fmt,
            url,
        )
        return task_id

    def get_task(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    def update_task(
        self,
        task_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        task = self.tasks.get(task_id)
        if not task:
            logger.warning("Attempted to update missing task task_id=%s status=%s", task_id, status)
            return

        task.status = status
        if result is not None:
            task.result = result
        if error is not None:
            task.error = error

        self._save_task(task)
        logger.info("Updated task task_id=%s status=%s", task_id, status)

    def list_tasks(self) -> list[Task]:
        return list(self.tasks.values())


state = State()


# ----------------------------
# yt-dlp service
# ----------------------------


class YtDlpService:
    @staticmethod
    def get_info(url: str, quiet: bool = False) -> dict[str, Any]:
        opts = {"quiet": quiet, "no_warnings": quiet, "skip_download": True}
        logger.debug("yt-dlp get_info url=%s quiet=%s", url, quiet)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return cast("dict[str, Any]", ydl.sanitize_info(info))

    @staticmethod
    def list_formats(url: str) -> list[dict[str, Any]]:
        info = YtDlpService.get_info(url=url, quiet=True)
        return info.get("formats", []) if info else []

    @staticmethod
    def download_video(
        url: str,
        output_path: str,
        fmt: str,
        quiet: bool,
        cookie_file: str | None = None,
    ) -> dict[str, Any]:
        ensure_dir(output_path)
        outtmpl = str(Path(output_path) / "%(title).180s.%(ext)s")
        ydl_opts = {
            "outtmpl": outtmpl,
            "quiet": quiet,
            "no_warnings": quiet,
            "format": fmt,
            "no_abort_on_error": True,
            "sleep_interval": 10,
            "sleep_subtitles": 10,
        }

        # Add cookies if provided (already validated by resolve_cookie_file)
        if cookie_file:
            ydl_opts["cookiefile"] = cookie_file
            logger.info("Using cookies file path=%s", cookie_file)

        logger.info(
            "yt-dlp download_video start url=%s output_path=%s fmt=%s quiet=%s cookie_file=%s",
            url,
            output_path,
            fmt,
            quiet,
            cookie_file,
        )
        start = time.monotonic()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.info("yt-dlp download_video done url=%s elapsed_ms=%d", url, elapsed_ms)
            return cast("dict[str, Any]", ydl.sanitize_info(info))

    @staticmethod
    def download_audio(
        url: str,
        output_path: str,
        audio_format: str,
        audio_quality: str | None,
        quiet: bool,
        cookie_file: str | None = None,
    ) -> dict[str, Any]:
        ensure_dir(output_path)
        outtmpl = str(Path(output_path) / "%(title).180s.%(ext)s")
        ydl_opts: dict[str, Any] = {
            "outtmpl": outtmpl,
            "quiet": quiet,
            "no_warnings": quiet,
            "format": "bestaudio/best",
            "extractaudio": True,
            "audioformat": audio_format,
            "no_abort_on_error": True,
            "sleep_interval": 10,
            "sleep_subtitles": 10,
        }
        if audio_quality is not None:
            ydl_opts["audioquality"] = audio_quality

        # Add cookies if provided (already validated by resolve_cookie_file)
        if cookie_file:
            ydl_opts["cookiefile"] = cookie_file
            logger.info("Using cookies file path=%s", cookie_file)

        logger.info(
            "yt-dlp download_audio start url=%s output_path=%s audio_format=%s audio_quality=%s quiet=%s cookie_file=%s",
            url,
            output_path,
            audio_format,
            audio_quality,
            quiet,
            cookie_file,
        )
        start = time.monotonic()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.info("yt-dlp download_audio done url=%s elapsed_ms=%d", url, elapsed_ms)
            return cast("dict[str, Any]", ydl.sanitize_info(info))

    @staticmethod
    def download_subtitles(
        url: str,
        output_path: str,
        languages: Sequence[str],
        write_manual: bool,
        write_automatic: bool,
        convert_to: str | None,
        quiet: bool,
        cookie_file: str | None = None,
    ) -> dict[str, Any]:
        """
        Download subtitles with support for partial success tracking.

        Returns a result dictionary with:
        - 'success': True if all requested subtitles were downloaded
        - 'downloaded': List of successfully downloaded subtitle files
        - 'failed': List of subtitle downloads that failed (empty if full success)
        - 'info': yt-dlp info dict (may be partial if download failed)
        - 'error': Error message if download failed
        """
        ensure_dir(output_path)
        outtmpl = str(Path(output_path) / "%(title).180s.%(ext)s")
        ydl_opts: dict[str, Any] = {
            "outtmpl": outtmpl,
            "quiet": quiet,
            "no_warnings": quiet,
            "skip_download": True,
            "subtitleslangs": list(languages),
            "no_abort_on_error": True,
            "sleep_interval": 10,
            "sleep_subtitles": 10,
            # Workaround: avoid WEB player client for extraction
            "extractor_args": {
                "youtube": {
                    "player_client": ["default", "-web"],
                }
            },
        }
        if write_manual:
            ydl_opts["writesubtitles"] = True
        if write_automatic:
            ydl_opts["writeautomaticsub"] = True
        if convert_to:
            ydl_opts["convertsubtitles"] = convert_to

        # Add cookies if provided (already validated by resolve_cookie_file)
        if cookie_file:
            ydl_opts["cookiefile"] = cookie_file
            logger.info("Using cookies file path=%s", cookie_file)

        logger.info(
            "yt-dlp download_subtitles start url=%s output_path=%s languages=%s manual=%s auto=%s convert_to=%s quiet=%s cookie_file=%s",
            url,
            output_path,
            list(languages),
            write_manual,
            write_automatic,
            convert_to,
            quiet,
            cookie_file,
        )
        start = time.monotonic()

        # Track files before download
        output_dir = Path(output_path)
        files_before = set(output_dir.glob("*")) if output_dir.exists() else set()

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                elapsed_ms = int((time.monotonic() - start) * 1000)
                sanitized_info = ydl.sanitize_info(info)

                # Check what files were actually created
                files_after = set(output_dir.glob("*")) if output_dir.exists() else set()
                new_files = files_after - files_before

                # Extract subtitle information from the result
                subtitles_data = {}
                if info and "subtitles" in info:
                    subtitles_data = info["subtitles"]
                if info and "automatic_captions" in info:
                    if write_automatic:
                        subtitles_data.update(info["automatic_captions"])

                # Determine which subtitles were actually downloaded
                downloaded_files = []
                for f in new_files:
                    if f.is_file():
                        downloaded_files.append(
                            {
                                "name": f.name,
                                "size_bytes": f.stat().st_size,
                                "path": str(f),
                            }
                        )

                # Check if we got the expected subtitles
                # Extract available subtitle languages from info
                requested_count = len(languages)
                successful_downloads = len(downloaded_files)

                logger.info(
                    "yt-dlp download_subtitles done url=%s elapsed_ms=%d downloaded=%d requested=%d",
                    url,
                    elapsed_ms,
                    successful_downloads,
                    requested_count,
                )

                return {
                    "success": successful_downloads > 0,
                    "downloaded": downloaded_files,
                    "failed": [] if successful_downloads > 0 else ["All subtitle downloads failed"],
                    "info": sanitized_info,
                    "partial": successful_downloads > 0 and successful_downloads < requested_count,
                }

        except Exception as e:
            # Partial success: some files may have been created before error
            files_after = set(output_dir.glob("*")) if output_dir.exists() else set()
            new_files = files_after - files_before

            downloaded_files = []
            for f in new_files:
                if f.is_file():
                    downloaded_files.append(
                        {
                            "name": f.name,
                            "size_bytes": f.stat().st_size,
                            "path": str(f),
                        }
                    )

            elapsed_ms = int((time.monotonic() - start) * 1000)
            error_msg = str(e)

            # Check if it's a retryable error
            is_429 = "429" in error_msg or "too many requests" in error_msg.lower()

            logger.warning(
                "yt-dlp download_subtitles failed url=%s elapsed_ms=%d downloaded_before_error=%d error=%s",
                url,
                elapsed_ms,
                len(downloaded_files),
                error_msg[:200],
            )

            return {
                "success": False,
                "downloaded": downloaded_files,
                "failed": [error_msg],
                "info": None,
                "error": error_msg,
                "partial": len(downloaded_files) > 0,
                "is_retryable": is_429,
            }


service = YtDlpService()


# ----------------------------
# Async execution
# ----------------------------

# Reuse one executor rather than creating a new pool per call. [web:2]
_EXECUTOR = ThreadPoolExecutor(
    max_workers=int(os.getenv("MAX_WORKERS", "4")), thread_name_prefix="yt-dlp-worker"
)


async def run_in_threadpool(func, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_EXECUTOR, lambda: func(*args, **kwargs))


async def process_task(task_id: str, job_type: JobType, payload: dict[str, Any]) -> None:
    logger.info("Process task start task_id=%s job_type=%s", task_id, job_type.value)
    start = time.monotonic()
    try:
        state.update_task(task_id, "running")

        # Build retry config: start with global defaults, override with request-specific values
        retry_config = RetryConfig(
            max_retries=default_retry_config.max_retries,
            backoff_base=default_retry_config.backoff_base,
            backoff_multiplier=default_retry_config.backoff_multiplier,
            jitter=default_retry_config.jitter,
        )

        # Override with request-specific values if provided
        if "max_retries" in payload:
            retry_config.max_retries = payload.pop("max_retries")
        if "retry_backoff" in payload:
            retry_config.backoff_base = payload.pop("retry_backoff")

        if job_type == JobType.video:
            # Apply retry wrapper for video downloads
            result = await run_in_threadpool(
                retry_with_backoff,
                service.download_video,
                retry_config,
                **payload,
            )
        elif job_type == JobType.audio:
            # Apply retry wrapper for audio downloads
            result = await run_in_threadpool(
                retry_with_backoff,
                service.download_audio,
                retry_config,
                **payload,
            )
        elif job_type == JobType.subtitles:
            # For subtitles, handle partial success and retry separately
            result = await run_in_threadpool(
                retry_with_backoff,
                service.download_subtitles,
                retry_config,
                **payload,
            )

            # Check if we got partial success
            if isinstance(result, dict) and result.get("partial"):
                logger.info(
                    "Partial subtitle download success task_id=%s downloaded=%d failed=%d",
                    task_id,
                    len(result.get("downloaded", [])),
                    len(result.get("failed", [])),
                )
                state.update_task(task_id, "partial", result=result)
                return

            # Check if completely failed but retryable
            if (
                isinstance(result, dict)
                and not result.get("success")
                and result.get("is_retryable")
            ):
                # The retry logic should have handled this, but if we still failed:
                logger.warning("Subtitle download failed after retries task_id=%s", task_id)
                state.update_task(task_id, "failed", error=result.get("error", "Unknown error"))
                return
        else:
            raise ValueError(f"Unsupported job type: {job_type}")

        state.update_task(task_id, "completed", result=result)
        logger.info(
            "Process task completed task_id=%s elapsed_ms=%d",
            task_id,
            int((time.monotonic() - start) * 1000),
        )
    except Exception as exc:
        logger.exception("Process task failed task_id=%s error=%s", task_id, exc)
        state.update_task(task_id, "failed", error=str(exc))


# ----------------------------
# File endpoints (generic)
# ----------------------------


def _require_completed_task(task_id: str) -> Task:
    """Get a task that has completed (either fully or partially)."""
    task = state.get_task(task_id)
    if not task:
        logger.info("Task not found task_id=%s", task_id)
        raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
    if task.status not in ("completed", "partial"):
        logger.info("Task not completed task_id=%s status=%s", task_id, task.status)
        raise HTTPException(
            status_code=400,
            detail=f"Task is not completed yet. Current status: {task.status}",
        )
    return task


def list_task_files(task: Task) -> list[Path]:
    task_dir = Path(task.task_output_path)
    if not task_dir.exists():
        logger.warning("Task output directory missing task_id=%s dir=%s", task.id, task_dir)
        return []
    files = [p for p in task_dir.iterdir() if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    logger.debug("Listed task files task_id=%s count=%d", task.id, len(files))
    return files


# ----------------------------
# FastAPI
# ----------------------------

app = FastAPI(
    title="yt-dlp API",
    description="API for downloading videos, audio, and subtitles using yt-dlp",
    dependencies=[Depends(require_api_key)],
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    token = _request_id_ctx.set(request_id)
    start = time.monotonic()
    try:
        logger.info("Request start method=%s path=%s", request.method, request.url.path)
        response = await call_next(request)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "Request end method=%s path=%s status=%d elapsed_ms=%d",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        _request_id_ctx.reset(token)


@app.post("/download", response_class=JSONResponse)
async def api_download_video(request: DownloadRequest):
    base_dir = resolve_task_base_dir(request.output_path)
    cookie_file = resolve_cookie_file(request.cookie_file)

    existing = next(
        (
            t
            for t in state.tasks.values()
            if t.job_type == JobType.video
            and t.url == request.url
            and t.base_output_path == str(base_dir)
            and t.format == request.format
        ),
        None,
    )
    if existing:
        logger.info(
            "Deduped video task existing_task_id=%s url=%s base=%s fmt=%s",
            existing.id,
            request.url,
            base_dir,
            request.format,
        )
        return {"status": "success", "task_id": existing.id}

    task_id = state.add_task(JobType.video, request.url, request.output_path, request.format)
    task = state.get_task(task_id)
    assert task is not None

    logger.info("Queue video task task_id=%s cookie_file=%s", task_id, cookie_file)
    asyncio.create_task(
        process_task(
            task_id=task_id,
            job_type=JobType.video,
            payload={
                "url": request.url,
                "output_path": task.task_output_path,
                "fmt": request.format,
                "quiet": request.quiet,
                "cookie_file": cookie_file,
            },
        )
    )
    return {"status": "success", "task_id": task_id}


@app.post("/audio", response_class=JSONResponse)
async def api_download_audio(request: AudioRequest):
    fmt_key = f"audio:{request.audio_format}:q={request.audio_quality}"
    base_dir = resolve_task_base_dir(request.output_path)
    cookie_file = resolve_cookie_file(request.cookie_file)

    existing = next(
        (
            t
            for t in state.tasks.values()
            if t.job_type == JobType.audio
            and t.url == request.url
            and t.base_output_path == str(base_dir)
            and t.format == fmt_key
        ),
        None,
    )
    if existing:
        logger.info(
            "Deduped audio task existing_task_id=%s url=%s base=%s fmt=%s",
            existing.id,
            request.url,
            base_dir,
            fmt_key,
        )
        return {"status": "success", "task_id": existing.id}

    task_id = state.add_task(JobType.audio, request.url, request.output_path, fmt_key)
    task = state.get_task(task_id)
    assert task is not None

    logger.info("Queue audio task task_id=%s cookie_file=%s", task_id, cookie_file)
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
                "cookie_file": cookie_file,
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
    base_dir = resolve_task_base_dir(request.output_path)
    cookie_file = resolve_cookie_file(request.cookie_file)

    existing = next(
        (
            t
            for t in state.tasks.values()
            if t.job_type == JobType.subtitles
            and t.url == request.url
            and t.base_output_path == str(base_dir)
            and t.format == fmt_key
        ),
        None,
    )
    if existing:
        logger.info(
            "Deduped subtitles task existing_task_id=%s url=%s base=%s fmt=%s",
            existing.id,
            request.url,
            base_dir,
            fmt_key,
        )
        return {"status": "success", "task_id": existing.id}

    task_id = state.add_task(JobType.subtitles, request.url, request.output_path, fmt_key)
    task = state.get_task(task_id)
    assert task is not None

    logger.info("Queue subtitles task task_id=%s cookie_file=%s", task_id, cookie_file)
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
                "cookie_file": cookie_file,
            },
        )
    )
    return {"status": "success", "task_id": task_id}


@app.get("/task/{task_id}", response_class=JSONResponse)
async def get_task_status(task_id: str):
    task = state.get_task(task_id)
    if not task:
        logger.info("Task not found task_id=%s", task_id)
        raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")

    data: dict[str, Any] = {
        "id": task.id,
        "job_type": task.job_type,
        "url": task.url,
        "status": task.status,
        "base_output_path": task.base_output_path,
        "task_output_path": task.task_output_path,
    }
    # Include result for both completed and partial tasks
    if task.status in ("completed", "partial") and task.result:
        data["result"] = task.result
    if task.status == "failed" and task.error:
        data["error"] = task.error

    return {"status": "success", "data": data}


@app.get("/tasks", response_class=JSONResponse)
async def list_all_tasks():
    logger.debug("List tasks count=%d", len(state.tasks))
    return {"status": "success", "data": state.list_tasks()}


@app.get("/info", response_class=JSONResponse)
async def api_get_video_info(url: str = Query(..., description="Video URL")):
    try:
        logger.info("Info request url=%s", url)
        return {"status": "success", "data": service.get_info(url=url, quiet=True)}
    except Exception as exc:
        logger.exception("Info request failed url=%s error=%s", url, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/formats", response_class=JSONResponse)
async def api_list_formats(url: str = Query(..., description="Video URL")):
    try:
        logger.info("Formats request url=%s", url)
        return {"status": "success", "data": service.list_formats(url)}
    except Exception as exc:
        logger.exception("Formats request failed url=%s error=%s", url, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/cookies/upload", response_class=JSONResponse)
async def upload_cookies_file(
    file: UploadFile = File(..., description="cookies.txt file"),
):
    """
    Upload a cookies.txt file for use in downloads.

    The file is stored in the cookies directory and can be referenced by
    the returned filename in download requests via the cookie_file parameter.
    """
    if not file.filename:
        logger.warning("Cookies upload attempt without filename")
        raise HTTPException(status_code=400, detail="No filename provided")

    # Generate a safe filename
    safe_filename = f"{uuid.uuid4()}_{normalize_string(file.filename, max_length=50)}"
    cookie_path = COOKIES_DIR / safe_filename

    try:
        logger.info("Saving cookies file filename=%s path=%s", file.filename, cookie_path)
        content = await file.read()

        # Validate it's a text file
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as err:
            logger.warning("Cookies file is not valid UTF-8 filename=%s", file.filename)
            raise HTTPException(
                status_code=400, detail="cookies.txt must be a valid text file"
            ) from err

        # Write the file
        with open(cookie_path, "wb") as f:
            f.write(content)

        logger.info(
            "Cookies file saved successfully path=%s size_bytes=%d", cookie_path, len(content)
        )
        return {
            "status": "success",
            "data": {
                "cookie_file": safe_filename,
                "path": str(cookie_path),
                "size_bytes": len(content),
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to save cookies file filename=%s error=%s", file.filename, exc)
        raise HTTPException(status_code=500, detail=f"Failed to save cookies file: {exc}") from exc


@app.get("/task/{task_id}/files", response_class=JSONResponse)
async def api_task_files(task_id: str):
    task = _require_completed_task(task_id)
    files = list_task_files(task)
    return {
        "status": "success",
        "data": [{"name": f.name, "size_bytes": f.stat().st_size} for f in files],
    }


@app.get("/task/{task_id}/file", response_class=FileResponse)
async def api_task_file(
    task_id: str,
    name: str = Query(..., description="Exact filename from /task/{task_id}/files"),
):
    task = _require_completed_task(task_id)
    allow = {p.name: p for p in list_task_files(task)}
    if name not in allow:
        logger.info("File not found task_id=%s name=%s", task_id, name)
        raise HTTPException(status_code=404, detail="File not found for this task")

    p = allow[name]
    logger.info("Serving file task_id=%s name=%s path=%s", task_id, name, p)
    return FileResponse(path=str(p), filename=p.name, media_type="application/octet-stream")


@app.get("/task/{task_id}/zip", response_class=FileResponse)
async def api_task_zip(task_id: str):
    task = _require_completed_task(task_id)
    files = list_task_files(task)
    if not files:
        logger.info("No files to zip task_id=%s", task_id)
        raise HTTPException(status_code=404, detail="No files found to zip")

    tmp = NamedTemporaryFile(delete=False, suffix=".zip")
    tmp_path = Path(tmp.name)
    tmp.close()

    def cleanup() -> None:
        try:
            tmp_path.unlink(missing_ok=True)
            logger.debug("Cleaned up temp zip path=%s", tmp_path)
        except Exception:
            logger.exception("Failed to cleanup temp zip path=%s", tmp_path)

    try:
        logger.info(
            "Creating zip task_id=%s tmp_path=%s file_count=%d", task_id, tmp_path, len(files)
        )
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
        logger.exception("Failed to create zip task_id=%s error=%s", task_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to create zip: {exc}") from exc


def start_api() -> None:
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    logger.info("Starting uvicorn host=%s port=%s", host, port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    logger.info("Starting yt-dlp API server...")
    start_api()
