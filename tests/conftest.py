"""
Shared fixtures and test utilities.
"""

import os
import tempfile
import uuid
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

# Set test environment variables before importing main
os.environ.update({
    "API_KEY_AUTH_ENABLED": "false",
    "SERVER_OUTPUT_ROOT": tempfile.mkdtemp(),
    "COOKIES_DIR": tempfile.mkdtemp(),
    "LOG_LEVEL": "DEBUG",
})

import main
from main import AuthConfig, CookieConfig, RetryConfig, State


@pytest.fixture
def temp_dir() -> Generator[Path]:
    """Provide a temporary directory that is cleaned up after the test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def temp_db(temp_dir: Path) -> Generator[str]:
    """Provide a temporary database file."""
    db_path = temp_dir / "test_tasks.db"
    yield str(db_path)
    # Cleanup is automatic with temp_dir


@pytest.fixture
def test_state(temp_db: str) -> State:
    """Provide a State instance with a temporary database."""
    return State(db_file=temp_db)


@pytest.fixture
def clean_state() -> Generator[State]:
    """Provide a clean State instance for each test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        state = State(db_file=str(db_path))
        yield state


@pytest.fixture
def mock_yt_dlp_service() -> MagicMock:
    """Provide a mocked YtDlpService."""
    mock = MagicMock()
    return mock


@pytest.fixture
def sample_task_id() -> str:
    """Provide a sample task ID."""
    return str(uuid.uuid4())


@pytest.fixture
def auth_config_enabled() -> AuthConfig:
    """Provide an enabled AuthConfig for testing."""
    return AuthConfig(enabled=True, master_key="test-master-key", header_name="X-API-Key")


@pytest.fixture
def auth_config_disabled() -> AuthConfig:
    """Provide a disabled AuthConfig for testing."""
    return AuthConfig(enabled=False, master_key=None, header_name="X-API-Key")


@pytest.fixture
def cookie_config_with_file(temp_dir: Path) -> CookieConfig:
    """Provide a CookieConfig with a test cookies file."""
    cookie_file = temp_dir / "test_cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n")
    return CookieConfig(cookies_file=str(cookie_file))


@pytest.fixture
def retry_config() -> RetryConfig:
    """Provide a RetryConfig with test values (shorter delays for testing)."""
    return RetryConfig(
        max_retries=2,
        backoff_base=0.1,  # Short delay for tests
        backoff_multiplier=2.0,
        jitter=False,  # Disable for predictable tests
        retryable_http_codes=[429, 500, 502, 503, 504],
    )


@pytest.fixture
async def async_client() -> AsyncGenerator[AsyncClient]:
    """Provide an async HTTP client for testing the FastAPI app."""
    # Create a fresh app instance for each test
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def reset_state() -> Generator[None]:
    """Reset the global state between tests."""
    # Save original state
    original_state = main.state

    # Create new temporary state
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        main.state = State(db_file=str(db_path))
        yield

        # Restore original state
        main.state = original_state


@pytest.fixture
def mock_output_root(temp_dir: Path) -> Generator[Path]:
    """Provide and set a temporary SERVER_OUTPUT_ROOT."""
    original_root = main.SERVER_OUTPUT_ROOT
    test_root = temp_dir / "downloads"
    test_root.mkdir(parents=True, exist_ok=True)

    # Monkey-patch the SERVER_OUTPUT_ROOT
    main.SERVER_OUTPUT_ROOT = test_root

    yield test_root

    # Restore original
    main.SERVER_OUTPUT_ROOT = original_root


@pytest.fixture
def sample_video_url() -> str:
    """Provide a sample video URL for testing."""
    return "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture
def sample_video_info() -> dict:
    """Provide sample video info response."""
    return {
        "id": "dQw4w9WgXcQ",
        "title": "Sample Video",
        "uploader": "Test Channel",
        "duration": 213,
        "view_count": 1000000,
        "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "formats": [
            {
                "format_id": "137",
                "ext": "mp4",
                "height": 1080,
                "width": 1920,
                "format_note": "1080p",
            },
            {
                "format_id": "140",
                "ext": "m4a",
                "acodec": "mp4a.40.2",
                "format_note": "medium",
            },
        ],
    }


@pytest.fixture
def sample_formats() -> list:
    """Provide sample format list response."""
    return [
        {
            "format_id": "137",
            "ext": "mp4",
            "height": 1080,
            "width": 1920,
            "format_note": "1080p",
            "preference": 100,
        },
        {
            "format_id": "140",
            "ext": "m4a",
            "acodec": "mp4a.40.2",
            "format_note": "medium",
            "preference": 50,
        },
    ]
