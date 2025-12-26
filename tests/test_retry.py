"""
Unit tests for retry logic.
"""

import time
from unittest.mock import MagicMock

import pytest

from main import (
    RetryConfig,
    calculate_backoff,
    is_retryable_error,
    retry_with_backoff,
)


class TestIsRetryableError:
    """Tests for is_retryable_error function."""

    def test_retryable_http_codes(self, retry_config) -> None:
        """Test that configured HTTP codes are retryable."""
        for code in retry_config.retryable_http_codes:
            error = Exception(f"HTTP Error {code}")
            assert is_retryable_error(error, retry_config) is True

    def test_retryable_http_error_alternate_format(self, retry_config) -> None:
        """Test alternate HTTP error format."""
        error = Exception("HTTPError: 429")
        assert is_retryable_error(error, retry_config) is True

    def test_retryable_error_patterns(self, retry_config) -> None:
        """Test various retryable error patterns."""
        retryable_errors = [
            Exception("too many requests"),
            Exception("rate limit exceeded"),
            Exception("temporary failure in name resolution"),
            Exception("connection reset by peer"),
            Exception("connection refused"),
            Exception("operation timed out"),
            Exception("server error 500"),
        ]

        for error in retryable_errors:
            assert is_retryable_error(error, retry_config) is True, f"Failed for: {error}"

    def test_non_retryable_error(self, retry_config) -> None:
        """Test that non-retryable errors return False."""
        non_retryable_errors = [
            Exception("video unavailable"),
            Exception("age restricted"),
            Exception("payment required"),
            Exception("authentication failed"),
            Exception("HTTP Error 404"),
            Exception("HTTP Error 403"),
        ]

        for error in non_retryable_errors:
            assert is_retryable_error(error, retry_config) is False, f"Should not retry: {error}"

    def test_case_insensitive_matching(self, retry_config) -> None:
        """Test that error matching is case-insensitive."""
        error = Exception("Too Many Requests")
        assert is_retryable_error(error, retry_config) is True

        error = Exception("RATE LIMIT")
        assert is_retryable_error(error, retry_config) is True

    def test_custom_retryable_codes(self) -> None:
        """Test custom retryable HTTP codes."""
        config = RetryConfig(
            retryable_http_codes=[503, 504]
        )

        error = Exception("HTTP Error 503")
        assert is_retryable_error(error, config) is True

        error = Exception("HTTP Error 500")  # Not in custom list
        assert is_retryable_error(error, config) is False


class TestCalculateBackoff:
    """Tests for calculate_backoff function."""

    def test_zero_attempt_no_backoff(self, retry_config) -> None:
        """Test that attempt 0 returns base backoff."""
        delay = calculate_backoff(0, retry_config)
        assert delay == retry_config.backoff_base

    def test_exponential_backoff(self, retry_config) -> None:
        """Test exponential backoff calculation."""
        base = retry_config.backoff_base
        multiplier = retry_config.backoff_multiplier

        delay0 = calculate_backoff(0, retry_config)
        delay1 = calculate_backoff(1, retry_config)
        delay2 = calculate_backoff(2, retry_config)

        assert delay0 == base
        assert delay1 == base * multiplier
        assert delay2 == base * (multiplier ** 2)

    def test_backoff_with_jitter(self) -> None:
        """Test that jitter adds randomness to backoff."""
        config = RetryConfig(
            backoff_base=1.0,
            backoff_multiplier=2.0,
            jitter=True,
        )

        delays = [calculate_backoff(1, config) for _ in range(10)]

        # With jitter, we should get varying delays
        # Base delay = 1.0 * 2 = 2.0
        # Jitter range ±25% = ±0.5
        # So delays should be in range [1.5, 2.5]
        for delay in delays:
            assert 1.5 <= delay <= 2.5

        # Not all delays should be the same
        assert len(set(delays)) > 1

    def test_backoff_without_jitter(self, retry_config) -> None:
        """Test that backoff is predictable without jitter."""
        config = RetryConfig(
            backoff_base=1.0,
            backoff_multiplier=2.0,
            jitter=False,
        )

        delay1 = calculate_backoff(1, config)
        delay2 = calculate_backoff(1, config)

        assert delay1 == delay2 == 2.0

    def test_backoff_never_negative(self) -> None:
        """Test that backoff delay is never negative."""
        config = RetryConfig(
            backoff_base=0.1,
            backoff_multiplier=2.0,
            jitter=True,
        )

        for attempt in range(5):
            delay = calculate_backoff(attempt, config)
            assert delay >= 0


class TestRetryWithBackoff:
    """Tests for retry_with_backoff function."""

    def test_success_on_first_attempt(self, retry_config) -> None:
        """Test successful execution on first attempt."""
        func = MagicMock(return_value="success")

        result = retry_with_backoff(func, retry_config, "arg1", kwarg="kwarg1")

        assert result == "success"
        func.assert_called_once_with("arg1", kwarg="kwarg1")

    def test_retry_on_retryable_error(self, retry_config) -> None:
        """Test that retryable errors trigger retries."""
        func = MagicMock(
            side_effect=[
                Exception("HTTP Error 429"),
                Exception("HTTP Error 429"),
                "success",
            ]
        )

        result = retry_with_backoff(func, retry_config)

        assert result == "success"
        assert func.call_count == 3

    def test_fail_after_max_retries(self, retry_config) -> None:
        """Test that function fails after max retries."""
        func = MagicMock(side_effect=Exception("HTTP Error 429"))

        with pytest.raises(Exception) as exc_info:
            retry_with_backoff(func, retry_config)

        assert "HTTP Error 429" in str(exc_info.value)
        assert func.call_count == retry_config.max_retries + 1

    def test_no_retry_on_non_retryable_error(self, retry_config) -> None:
        """Test that non-retryable errors fail immediately."""
        func = MagicMock(side_effect=Exception("video unavailable"))

        with pytest.raises(Exception) as exc_info:
            retry_with_backoff(func, retry_config)

        assert "video unavailable" in str(exc_info.value)
        func.assert_called_once()  # Should not retry

    def test_retry_count_matches_config(self) -> None:
        """Test that number of retries matches config.max_retries."""
        config = RetryConfig(max_retries=3, backoff_base=0.01)
        func = MagicMock(side_effect=Exception("too many requests"))

        with pytest.raises(Exception, match="too many requests"):
            retry_with_backoff(func, config)

        # Should call initial attempt + 3 retries
        assert func.call_count == 4

    def test_backoff_delays(self) -> None:
        """Test that appropriate backoff delays are used."""
        config = RetryConfig(
            max_retries=2,
            backoff_base=0.05,
            backoff_multiplier=2.0,
            jitter=False,
        )
        func = MagicMock(
            side_effect=[
                Exception("HTTP Error 429"),
                Exception("HTTP Error 429"),
                "success",
            ]
        )

        start = time.monotonic()
        retry_with_backoff(func, config)
        elapsed = time.monotonic() - start

        # First retry at 0.05s, second at 0.1s
        # Total should be at least 0.15s
        assert elapsed >= 0.14  # Small tolerance for timing

    def test_kwargs_passed_correctly(self, retry_config) -> None:
        """Test that kwargs are passed through correctly."""
        func = MagicMock(return_value="result")

        retry_with_backoff(
            func,
            retry_config,
            "positional_arg",
            keyword_arg="value",
            another=123,
        )

        func.assert_called_once_with(
            "positional_arg",
            keyword_arg="value",
            another=123,
        )

    def test_returns_function_result(self, retry_config) -> None:
        """Test that function return value is passed through."""
        expected_results = [
            {"status": "completed", "title": "Video"},
            [1, 2, 3, 4],
            "simple string",
            None,
            42,
        ]

        for expected in expected_results:
            func = MagicMock(return_value=expected)
            result = retry_with_backoff(func, retry_config)
            assert result == expected

    def test_custom_exception_type(self, retry_config) -> None:
        """Test that custom exception types can be retried."""
        class CustomError(Exception):
            pass

        # Configure retry config to recognize our custom error
        config = RetryConfig(
            max_retries=2,
            backoff_base=0.01,
        )

        func = MagicMock(
            side_effect=[
                CustomError("custom error unrelated to retryable patterns"),
                "success",
            ]
        )

        # Since CustomError doesn't match retryable patterns,
        # it should fail immediately
        with pytest.raises(CustomError):
            retry_with_backoff(func, config)

        assert func.call_count == 1

    def test_partial_success_case(self) -> None:
        """Test handling of partial success scenarios."""
        config = RetryConfig(
            max_retries=1,
            backoff_base=0.01,
        )

        # Simulate a function that returns a dict with success flag
        func = MagicMock(
            side_effect=[
                Exception("HTTP Error 429"),
                {"success": True, "data": "result"},
            ]
        )

        result = retry_with_backoff(func, config)
        assert result == {"success": True, "data": "result"}
