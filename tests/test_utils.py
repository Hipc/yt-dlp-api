"""
Unit tests for utility functions.
"""

from pathlib import Path

import pytest

from main import (
    _env_truthy,
    _env_int,
    _env_float,
    _is_safe_subdir_name,
    resolve_task_base_dir,
    resolve_cookie_file,
    normalize_string,
    ensure_dir,
)
from main import SERVER_OUTPUT_ROOT_ENV, SERVER_OUTPUT_ROOT, COOKIES_DIR


class TestEnvTruthy:
    """Tests for _env_truthy function."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("1", True),
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("t", True),
            ("T", True),
            ("yes", True),
            ("Yes", True),
            ("y", True),
            ("on", True),
            ("ON", True),
            ("0", False),
            ("false", False),
            ("False", False),
            ("f", False),
            ("no", False),
            ("n", False),
            ("off", False),
            ("OFF", False),
            ("  true  ", True),  # Whitespace trimming
            ("invalid", False),  # Invalid returns default
        ],
    )
    def test_env_truthy_values(self, value: str, expected: bool) -> None:
        """Test various truthy/falsey string values."""
        assert _env_truthy(value) == expected

    def test_env_truthy_none_with_default(self) -> None:
        """Test None value with custom default."""
        assert _env_truthy(None, default=True) is True
        assert _env_truthy(None, default=False) is False


class TestEnvInt:
    """Tests for _env_int function."""

    @pytest.mark.parametrize(
        ("value", "default", "expected"),
        [
            ("42", 0, 42),
            ("-10", 0, -10),
            ("0", 100, 0),
            ("invalid", 10, 10),  # Invalid returns default
            (None, 5, 5),  # None returns default
            ("  25  ", 0, 25),  # Whitespace trimming
        ],
    )
    def test_env_int_values(self, value: str | None, default: int, expected: int) -> None:
        """Test integer parsing from environment variables."""
        assert _env_int(value, default=default) == expected


class TestEnvFloat:
    """Tests for _env_float function."""

    @pytest.mark.parametrize(
        ("value", "default", "expected"),
        [
            ("3.14", 0.0, 3.14),
            ("-2.5", 0.0, -2.5),
            ("0", 1.0, 0.0),
            ("invalid", 10.0, 10.0),  # Invalid returns default
            (None, 5.5, 5.5),  # None returns default
            ("  2.5  ", 0.0, 2.5),  # Whitespace trimming
        ],
    )
    def test_env_float_values(self, value: str | None, default: float, expected: float) -> None:
        """Test float parsing from environment variables."""
        assert _env_float(value, default=default) == expected


class TestIsSafeSubdirName:
    """Tests for _is_safe_subdir_name function."""

    @pytest.mark.parametrize(
        "name",
        [
            "simple",
            "with_underscore",
            "with-dash",
            "with.dot",
            "MixedCase123",
            "a",  # Single char
            "123",
            "CamelCase",
            "snake_case",
            "kebab-case",
        ],
    )
    def test_safe_names(self, name: str) -> None:
        """Test names that should be considered safe."""
        assert _is_safe_subdir_name(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "",  # Empty
            ".",  # Current dir
            "..",  # Parent dir
            "...",  # Contains double dot
            "with/../dot",  # Path traversal
            "with\\..\\dot",  # Windows path traversal
            "with/slash",  # Forward slash
            "with\\backslash",  # Backslash
            "with space",  # Space (not in allowed chars)
            "with*asterisk",  # Asterisk
            "with?question",  # Question mark
            "a" * 81,  # Too long (>80 chars)
        ],
    )
    def test_unsafe_names(self, name: str) -> None:
        """Test names that should be considered unsafe."""
        assert _is_safe_subdir_name(name) is False

    def test_custom_max_length(self) -> None:
        """Test custom max_length parameter."""
        long_name = "a" * 50
        assert _is_safe_subdir_name(long_name, max_length=50) is True
        assert _is_safe_subdir_name(long_name, max_length=49) is False


class TestResolveTaskBaseDir:
    """Tests for resolve_task_base_dir function."""

    def test_empty_path_uses_default(self, tmp_path) -> None:
        """Test empty path resolves to 'default' subdir."""
        import main
        original_root = main.SERVER_OUTPUT_ROOT
        main.SERVER_OUTPUT_ROOT = tmp_path

        try:
            result = resolve_task_base_dir("")
            expected = tmp_path / "default"
            assert result == expected
            assert result.exists()
        finally:
            main.SERVER_OUTPUT_ROOT = original_root

    def test_dot_slash_uses_default(self, tmp_path) -> None:
        """Test './' path resolves to 'default' subdir."""
        import main
        original_root = main.SERVER_OUTPUT_ROOT
        main.SERVER_OUTPUT_ROOT = tmp_path

        try:
            result = resolve_task_base_dir("./")
            expected = tmp_path / "default"
            assert result == expected
        finally:
            main.SERVER_OUTPUT_ROOT = original_root

    def test_safe_name_creates_subdir(self, tmp_path) -> None:
        """Test safe name creates appropriate subdirectory."""
        import main
        original_root = main.SERVER_OUTPUT_ROOT
        main.SERVER_OUTPUT_ROOT = tmp_path

        try:
            result = resolve_task_base_dir("my-downloads")
            expected = tmp_path / "my-downloads"
            assert result == expected
            assert result.exists()
        finally:
            main.SERVER_OUTPUT_ROOT = original_root

    def test_unsafe_name_raises_http_exception(self, tmp_path) -> None:
        """Test unsafe names raise HTTP 400."""
        import main
        original_root = main.SERVER_OUTPUT_ROOT
        main.SERVER_OUTPUT_ROOT = tmp_path

        try:
            with pytest.raises(Exception) as exc_info:  # HTTPException
                assert exc_info.value.status_code == 400
                resolve_task_base_dir("../escape")
        finally:
            main.SERVER_OUTPUT_ROOT = original_root


class TestResolveCookieFile:
    """Tests for resolve_cookie_file function."""

    def test_no_cookie_file_returns_none(self) -> None:
        """Test when no cookie file is configured."""
        import main
        original_config = main.cookie_config
        main.cookie_config = main.CookieConfig(cookies_file=None)

        try:
            assert resolve_cookie_file(None) is None
        finally:
            main.cookie_config = original_config

    def test_nonexistent_file_returns_none(self, temp_dir) -> None:
        """Test non-existent file returns None."""
        result = resolve_cookie_file("nonexistent.txt")
        assert result is None


class TestNormalizeString:
    """Tests for normalize_string function."""

    @pytest.mark.parametrize(
        ("input_str", "expected"),
        [
            ("  hello  ", "hello"),  # Trim whitespace
            ("file:name", "file_name"),  # Colon
            ("file/name", "file_name"),  # Forward slash
            ("file\\name", "file_name"),  # Backslash
            ("file*name", "file_name"),  # Asterisk
            ("file?name", "file_name"),  # Question mark
            ('file"name', "file_name"),  # Quote
            ("file<name>", "file_name_"),  # Angle brackets (each replaced separately)
            ("file|name", "file_name"),  # Pipe
            ("a" * 250, "a" * 197 + "..."),  # Length truncation
        ],
    )
    def test_normalize_string(self, input_str: str, expected: str) -> None:
        """Test string normalization."""
        assert normalize_string(input_str) == expected

    def test_custom_max_length(self) -> None:
        """Test custom max_length parameter."""
        long_str = "a" * 100
        result = normalize_string(long_str, max_length=50)
        assert len(result) == 50
        assert result.endswith("...")


class TestEnsureDir:
    """Tests for ensure_dir function."""

    def test_creates_directory(self, temp_dir) -> None:
        """Test directory creation."""
        new_dir = temp_dir / "new" / "nested" / "dir"
        result = ensure_dir(str(new_dir))
        assert Path(result).exists()
        assert Path(result).is_dir()

    def test_existing_directory(self, temp_dir) -> None:
        """Test existing directory doesn't raise error."""
        existing = temp_dir / "existing"
        existing.mkdir()
        result = ensure_dir(str(existing))
        assert Path(result).exists()
