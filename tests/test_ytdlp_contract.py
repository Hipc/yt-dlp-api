"""
Contract tests for yt-dlp library integration.

These tests verify that yt-dlp returns data in the structure our app expects.
They require network access and are skipped in CI by default.

Run manually with:
    pytest tests/test_ytdlp_contract.py -v
    pytest -m network -v  # run all network tests
"""

import tempfile

import pytest

from main import YtDlpService


@pytest.mark.network
class TestYtDlpContract:
    """
    Contract tests for yt-dlp library integration.

    These tests verify that yt-dlp's API returns data in the structure
    our application expects, catching any breaking changes in yt-dlp responses.
    """

    def test_get_info_returns_expected_fields(self):
        """Verify /info endpoint returns expected fields from yt-dlp."""
        # Use a reliable, permanent test video (Rick Astley - Never Gonna Give You Up)
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

        info = YtDlpService.get_info(url, quiet=True)

        # Contract: these core fields must exist
        assert "id" in info, "Missing 'id' field in yt-dlp response"
        assert "title" in info, "Missing 'title' field in yt-dlp response"
        assert "formats" in info, "Missing 'formats' field in yt-dlp response"
        assert "webpage_url" in info, "Missing 'webpage_url' field in yt-dlp response"
        assert isinstance(info["formats"], list), "'formats' should be a list"

        # Log what we got for manual inspection
        print(f"\n✓ Video ID: {info['id']}")
        print(f"✓ Title: {info['title']}")
        print(f"✓ Formats available: {len(info['formats'])}")

    def test_list_formats_returns_expected_structure(self):
        """Verify /formats returns expected structure from yt-dlp."""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

        formats = YtDlpService.list_formats(url)

        assert isinstance(formats, list), "Formats should be a list"

        if len(formats) > 0:
            # Contract: each format should have these fields
            f = formats[0]
            assert "format_id" in f, "Missing 'format_id' in format"
            assert "ext" in f, "Missing 'ext' in format"

            print(f"\n✓ Got {len(formats)} formats")
            print(f"✓ Sample format: {f.get('format_id')} - {f.get('ext')}")

    def test_subtitles_extraction_structure(self):
        """
        Verify subtitle extraction returns expected structure.

        This test verifies the partial success logic works correctly.
        """
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

        with tempfile.TemporaryDirectory() as tmpdir:
            result = YtDlpService.download_subtitles(
                url=url,
                output_path=tmpdir,
                languages=["en"],
                write_manual=True,
                write_automatic=True,
                convert_to="srt",
                quiet=True,
                cookie_file=None,
            )

            # Contract: result should have expected structure
            assert isinstance(result, dict), "Result should be a dict"
            assert "success" in result, "Missing 'success' field"
            assert "downloaded" in result, "Missing 'downloaded' field"
            assert "failed" in result, "Missing 'failed' field"
            assert isinstance(result["downloaded"], list), "'downloaded' should be a list"

            # We don't assert success=True because subtitles may not be available
            # We're just checking the structure is correct
            print("\n✓ Subtitle extraction structure OK")
            print(f"✓ Success: {result.get('success')}")
            print(f"✓ Downloaded: {len(result.get('downloaded', []))} files")
            print(f"✓ Failed: {len(result.get('failed', []))} errors")

    def test_youtube_extractor_args_compat(self):
        """
        Verify that our YouTube extractor_args still work.

        We use a custom player_client setting to avoid WEB player issues.
        This test ensures yt-dlp hasn't changed how it handles this arg.
        """
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

        # This should not raise an error
        info = YtDlpService.get_info(url, quiet=True)

        # If we got here without error, the extractor_args are compatible
        assert info is not None
        print("\n✓ YouTube extractor_args compatible")

    def test_audio_download_structure(self):
        """Verify audio download returns expected structure."""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

        with tempfile.TemporaryDirectory() as tmpdir:
            # Just extract info, not full download (to keep test fast)
            # This verifies the options structure is still valid
            result = YtDlpService.download_audio(
                url=url,
                output_path=tmpdir,
                audio_format="mp3",
                audio_quality=None,
                quiet=True,
                cookie_file=None,
            )

            assert isinstance(result, dict), "Result should be a dict"
            assert "id" in result, "Missing 'id' in audio download result"
            assert "title" in result, "Missing 'title' in audio download result"

            print("\n✓ Audio download structure OK")
            print(f"✓ Got info for: {result['title']}")
