"""
Unit tests for configuration classes.
"""

import os
from unittest.mock import patch

import pytest

from main import (
    AuthConfig,
    CookieConfig,
    RetryConfig,
)


class TestAuthConfig:
    """Tests for AuthConfig class."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        config = AuthConfig()
        assert config.enabled is False
        assert config.master_key is None
        assert config.header_name == "X-API-Key"

    def test_from_env_disabled(self) -> None:
        """Test loading auth config from environment when disabled."""
        with patch.dict(os.environ, {}, clear=True):
            config = AuthConfig.from_env()
            assert config.enabled is False
            assert config.master_key is None
            assert config.header_name == "X-API-Key"

    def test_from_env_enabled(self) -> None:
        """Test loading enabled auth config from environment."""
        env_vars = {
            "API_KEY_AUTH_ENABLED": "true",
            "API_MASTER_KEY": "test-secret-key",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            config = AuthConfig.from_env()
            assert config.enabled is True
            assert config.master_key == "test-secret-key"

    def test_from_env_custom_header(self) -> None:
        """Test custom header name from environment."""
        env_vars = {
            "API_KEY_AUTH_ENABLED": "1",
            "API_MASTER_KEY": "my-key",
            "API_KEY_HEADER_NAME": "X-Custom-Auth",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            config = AuthConfig.from_env()
            assert config.header_name == "X-Custom-Auth"

    def test_from_env_header_whitespace_trimming(self) -> None:
        """Test that header name whitespace is trimmed."""
        env_vars = {
            "API_KEY_HEADER_NAME": "  X-Auth  ",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            config = AuthConfig.from_env()
            assert config.header_name == "X-Auth"


class TestCookieConfig:
    """Tests for CookieConfig class."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        config = CookieConfig()
        assert config.cookies_file is None

    def test_from_env_no_cookie_file(self) -> None:
        """Test loading config when no cookie file is set."""
        with patch.dict(os.environ, {}, clear=True):
            config = CookieConfig.from_env()
            assert config.cookies_file is None

    def test_from_env_with_existing_file(self, temp_dir) -> None:
        """Test loading config with an existing cookie file."""
        cookie_file = temp_dir / "cookies.txt"
        cookie_file.write_text("# Netscape HTTP Cookie File\n")

        env_vars = {
            "COOKIES_FILE": str(cookie_file),
        }
        with patch.dict(os.environ, env_vars, clear=False):
            config = CookieConfig.from_env()
            assert config.cookies_file == str(cookie_file)

    def test_from_env_with_nonexistent_file(self) -> None:
        """Test loading config with a non-existent cookie file."""
        env_vars = {
            "COOKIES_FILE": "/nonexistent/path/cookies.txt",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            config = CookieConfig.from_env()
            assert config.cookies_file is None

    def test_from_env_whitespace_trimming(self, temp_dir) -> None:
        """Test that cookie file path whitespace is trimmed."""
        cookie_file = temp_dir / "cookies.txt"
        cookie_file.write_text("# Netscape HTTP Cookie File\n")

        env_vars = {
            "COOKIES_FILE": f"  {cookie_file}  ",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            config = CookieConfig.from_env()
            assert config.cookies_file == str(cookie_file)


class TestRetryConfig:
    """Tests for RetryConfig class."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        config = RetryConfig()
        assert config.max_retries >= 0
        assert config.backoff_base >= 0
        assert config.backoff_multiplier >= 1.0
        assert isinstance(config.jitter, bool)
        assert len(config.retryable_http_codes) > 0

    def test_custom_values(self) -> None:
        """Test custom configuration values."""
        config = RetryConfig(
            max_retries=5,
            backoff_base=10.0,
            backoff_multiplier=3.0,
            jitter=False,
            retryable_http_codes=[500, 502],
        )
        assert config.max_retries == 5
        assert config.backoff_base == 10.0
        assert config.backoff_multiplier == 3.0
        assert config.jitter is False
        assert config.retryable_http_codes == [500, 502]

    def test_from_env_defaults(self) -> None:
        """Test loading config from environment with defaults."""
        with patch.dict(os.environ, {}, clear=True):
            config = RetryConfig.from_env()
            assert config.max_retries >= 0
            assert isinstance(config.max_retries, int)

    def test_from_env_custom_values(self) -> None:
        """Test loading custom values from environment."""
        env_vars = {
            "DEFAULT_MAX_RETRIES": "10",
            "DEFAULT_RETRY_BACKOFF": "5.5",
            "DEFAULT_RETRY_BACKOFF_MULTIPLIER": "3.0",
            "DEFAULT_RETRY_JITTER": "false",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            config = RetryConfig.from_env()
            assert config.max_retries == 10
            assert config.backoff_base == 5.5
            assert config.backoff_multiplier == 3.0
            assert config.jitter is False

    def test_validation_max_retries_negative(self) -> None:
        """Test that negative max_retries raises validation error."""
        with pytest.raises(ValueError):
            RetryConfig(max_retries=-1)

    def test_validation_backoff_base_negative(self) -> None:
        """Test that negative backoff_base raises validation error."""
        with pytest.raises(ValueError):
            RetryConfig(backoff_base=-1.0)

    def test_validation_backoff_multiplier_less_than_one(self) -> None:
        """Test that backoff_multiplier < 1.0 raises validation error."""
        with pytest.raises(ValueError):
            RetryConfig(backoff_multiplier=0.5)

    def test_from_env_invalid_values_use_defaults(self) -> None:
        """Test that invalid environment values fall back to defaults."""
        env_vars = {
            "DEFAULT_MAX_RETRIES": "invalid",
            "DEFAULT_RETRY_BACKOFF": "not-a-number",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            config = RetryConfig.from_env()
            # Should use defaults instead of crashing
            assert isinstance(config.max_retries, int)
            assert isinstance(config.backoff_base, float)
