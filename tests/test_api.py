"""
Integration tests for API endpoints (with mocked yt-dlp).
"""

from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

import main


@pytest.fixture
async def client_with_mock_state(reset_state) -> AsyncGenerator[AsyncClient]:
    """Provide an async client with fresh state for each test."""
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestHealthEndpoints:
    """Tests for basic health/check endpoints."""

    @pytest.mark.asyncio
    async def test_root_endpoint(self, client_with_mock_state: AsyncClient) -> None:
        """Test root endpoint is accessible (FastAPI default)."""
        response = await client_with_mock_state.get("/")
        # FastAPI returns 404 for non-existent root, but we're checking it's running
        assert response.status_code in (200, 404)


class TestDownloadEndpoints:
    """Tests for /download, /audio, /subtitles endpoints."""

    @pytest.mark.asyncio
    async def test_download_video_success(
        self, client_with_mock_state: AsyncClient, sample_video_url: str
    ) -> None:
        """Test successful video download request."""
        payload = {
            "url": sample_video_url,
            "output_path": "test-downloads",
            "format": "best",
            "quiet": True,
        }

        with patch("main.process_task") as mock_process:
            mock_process.return_value = None

            response = await client_with_mock_state.post("/download", json=payload)

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert "task_id" in data
            assert isinstance(data["task_id"], str)

    @pytest.mark.asyncio
    async def test_download_audio_success(
        self, client_with_mock_state: AsyncClient, sample_video_url: str
    ) -> None:
        """Test successful audio download request."""
        payload = {
            "url": sample_video_url,
            "output_path": "audio-downloads",
            "audio_format": "mp3",
            "audio_quality": "192",
            "quiet": True,
        }

        with patch("main.process_task") as mock_process:
            mock_process.return_value = None

            response = await client_with_mock_state.post("/audio", json=payload)

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert "task_id" in data

    @pytest.mark.asyncio
    async def test_download_subtitles_success(
        self, client_with_mock_state: AsyncClient, sample_video_url: str
    ) -> None:
        """Test successful subtitles download request."""
        payload = {
            "url": sample_video_url,
            "output_path": "subs",
            "languages": ["en", "es"],
            "write_automatic": True,
            "write_manual": True,
            "convert_to": "srt",
            "quiet": True,
        }

        with patch("main.process_task") as mock_process:
            mock_process.return_value = None

            response = await client_with_mock_state.post("/subtitles", json=payload)

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert "task_id" in data

    @pytest.mark.asyncio
    async def test_download_with_custom_retry_config(
        self, client_with_mock_state: AsyncClient, sample_video_url: str
    ) -> None:
        """Test download with custom retry configuration."""
        payload = {
            "url": sample_video_url,
            "output_path": "test",
            "max_retries": 5,
            "retry_backoff": 10.0,
            "quiet": True,
        }

        with patch("main.process_task") as mock_process:
            mock_process.return_value = None

            response = await client_with_mock_state.post("/subtitles", json=payload)

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"

    @pytest.mark.asyncio
    async def test_download_deduplication(
        self, client_with_mock_state: AsyncClient, sample_video_url: str
    ) -> None:
        """Test that duplicate requests return existing task ID."""
        payload = {
            "url": sample_video_url,
            "output_path": "dedup-test",
            "format": "best",
            "quiet": True,
        }

        # First request
        with patch("main.process_task"):
            response1 = await client_with_mock_state.post("/download", json=payload)
            task_id_1 = response1.json()["task_id"]

        # Second identical request
        with patch("main.process_task") as mock_process:
            response2 = await client_with_mock_state.post("/download", json=payload)
            task_id_2 = response2.json()["task_id"]

            # Should return the same task ID without calling process_task
            assert task_id_1 == task_id_2
            mock_process.assert_not_called()

    @pytest.mark.asyncio
    async def test_download_invalid_output_path(
        self, client_with_mock_state: AsyncClient, sample_video_url: str
    ) -> None:
        """Test that invalid output path is rejected."""
        payload = {
            "url": sample_video_url,
            "output_path": "../../../etc",  # Path traversal attempt
            "format": "best",
        }

        response = await client_with_mock_state.post("/download", json=payload)

        assert response.status_code == 400


class TestTaskEndpoints:
    """Tests for /task/{task_id}, /tasks endpoints."""

    @pytest.mark.asyncio
    async def test_get_task_status(self, client_with_mock_state: AsyncClient) -> None:
        """Test getting task status."""
        # Create a task directly using the app's global state
        task_id = main.state.add_task(
            job_type=main.JobType.video,
            url="https://example.com/video",
            base_output_path="test",
            fmt="mp4",
        )
        main.state.update_task(task_id, "running")

        response = await client_with_mock_state.get(f"/task/{task_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["data"]["id"] == task_id
        assert data["data"]["status"] == "running"
        assert data["data"]["job_type"] == "video"

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, client_with_mock_state: AsyncClient) -> None:
        """Test getting non-existent task."""
        import uuid

        fake_id = str(uuid.uuid4())

        response = await client_with_mock_state.get(f"/task/{fake_id}")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_all_tasks(self, client_with_mock_state: AsyncClient) -> None:
        """Test listing all tasks."""
        id1 = main.state.add_task(main.JobType.video, "url1", "test", "mp4")
        id2 = main.state.add_task(main.JobType.audio, "url2", "test", "mp3")

        response = await client_with_mock_state.get("/tasks")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert len(data["data"]) == 2

        task_ids = {t["id"] for t in data["data"]}
        assert task_ids == {id1, id2}

    @pytest.mark.asyncio
    async def test_get_task_with_result(self, client_with_mock_state: AsyncClient) -> None:
        """Test getting completed task includes result."""
        task_id = main.state.add_task(
            job_type=main.JobType.video,
            url="https://example.com/video",
            base_output_path="test",
            fmt="mp4",
        )
        result = {"title": "Test Video", "duration": 300}
        main.state.update_task(task_id, "completed", result=result)

        response = await client_with_mock_state.get(f"/task/{task_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["result"] == result

    @pytest.mark.asyncio
    async def test_get_task_with_error(self, client_with_mock_state: AsyncClient) -> None:
        """Test getting failed task includes error."""
        task_id = main.state.add_task(
            job_type=main.JobType.video,
            url="https://example.com/video",
            base_output_path="test",
            fmt="mp4",
        )
        error = "Download failed: network error"
        main.state.update_task(task_id, "failed", error=error)

        response = await client_with_mock_state.get(f"/task/{task_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["error"] == error

    @pytest.mark.asyncio
    async def test_get_partial_task_includes_result(
        self, client_with_mock_state: AsyncClient
    ) -> None:
        """Test that partial status includes result data."""
        task_id = main.state.add_task(
            job_type=main.JobType.subtitles,
            url="https://example.com/video",
            base_output_path="test",
            fmt="srt",
        )
        result = {"downloaded": ["en.srt"], "failed": []}
        main.state.update_task(task_id, "partial", result=result)

        response = await client_with_mock_state.get(f"/task/{task_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["status"] == "partial"
        assert data["data"]["result"] == result


class TestInfoEndpoints:
    """Tests for /info and /formats endpoints."""

    @pytest.mark.asyncio
    async def test_get_video_info(
        self, client_with_mock_state: AsyncClient, sample_video_url: str, sample_video_info: dict
    ) -> None:
        """Test getting video information."""
        with patch("main.service.get_info", return_value=sample_video_info):
            response = await client_with_mock_state.get(f"/info?url={sample_video_url}")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert data["data"]["id"] == "dQw4w9WgXcQ"
            assert data["data"]["title"] == "Sample Video"

    @pytest.mark.asyncio
    async def test_get_video_info_error(self, client_with_mock_state: AsyncClient) -> None:
        """Test video info endpoint with error."""
        with patch("main.service.get_info", side_effect=Exception("Video not found")):
            response = await client_with_mock_state.get("/info?url=https://example.com/invalid")

            assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_list_formats(
        self, client_with_mock_state: AsyncClient, sample_video_url: str, sample_formats: list
    ) -> None:
        """Test listing available formats."""
        with patch("main.service.list_formats", return_value=sample_formats):
            response = await client_with_mock_state.get(f"/formats?url={sample_video_url}")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert len(data["data"]) == 2
            assert data["data"][0]["format_id"] == "137"

    @pytest.mark.asyncio
    async def test_list_formats_error(self, client_with_mock_state: AsyncClient) -> None:
        """Test formats endpoint with error."""
        with patch("main.service.list_formats", side_effect=Exception("Failed to extract")):
            response = await client_with_mock_state.get("/formats?url=https://example.com/invalid")

            assert response.status_code == 500


class TestCookieEndpoints:
    """Tests for /cookies/upload endpoint."""

    @pytest.mark.asyncio
    async def test_upload_cookies_file(self, client_with_mock_state: AsyncClient) -> None:
        """Test uploading a cookies file."""
        cookies_content = "# Netscape HTTP Cookie File\n"
        files = {"file": ("cookies.txt", cookies_content, "text/plain")}

        response = await client_with_mock_state.post("/cookies/upload", files=files)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "cookie_file" in data["data"]
        assert "size_bytes" in data["data"]

    @pytest.mark.asyncio
    async def test_upload_cookies_no_filename(self, client_with_mock_state: AsyncClient) -> None:
        """Test uploading cookies without filename."""
        # When filename is None, FastAPI doesn't match the UploadFile type
        # and returns 422 (validation error)
        files = {"file": (None, "content", "text/plain")}

        response = await client_with_mock_state.post("/cookies/upload", files=files)

        # FastAPI returns 422 for validation errors on file upload parameters
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_upload_cookies_invalid_utf8(self, client_with_mock_state: AsyncClient) -> None:
        """Test uploading non-UTF-8 cookies file."""
        files = {"file": ("cookies.txt", b"\x80\x81\x82\x83", "application/octet-stream")}

        response = await client_with_mock_state.post("/cookies/upload", files=files)

        assert response.status_code == 400


class TestTaskFileEndpoints:
    """Tests for /task/{task_id}/files, /task/{task_id}/file, /task/{task_id}/zip endpoints."""

    @pytest.mark.asyncio
    async def test_list_task_files(
        self, client_with_mock_state: AsyncClient, mock_output_root, temp_dir
    ) -> None:
        """Test listing files for a completed task."""
        # Create task and output directory
        import main

        original_root = main.SERVER_OUTPUT_ROOT
        main.SERVER_OUTPUT_ROOT = temp_dir / "downloads"

        try:
            task_id = main.state.add_task(
                job_type=main.JobType.video,
                url="https://example.com/video",
                base_output_path="test",
                fmt="mp4",
            )

            task = main.state.get_task(task_id)
            assert task is not None

            # Create some test files
            output_path = Path(task.task_output_path)
            output_path.mkdir(parents=True, exist_ok=True)
            (output_path / "video.mp4").write_text("video content")
            (output_path / "info.json").write_text('{"info": "data"}')

            main.state.update_task(task_id, "completed")

            response = await client_with_mock_state.get(f"/task/{task_id}/files")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert len(data["data"]) == 2
            file_names = {f["name"] for f in data["data"]}
            assert "video.mp4" in file_names
            assert "info.json" in file_names
        finally:
            main.SERVER_OUTPUT_ROOT = original_root

    @pytest.mark.asyncio
    async def test_list_task_files_not_completed(self, client_with_mock_state: AsyncClient) -> None:
        """Test listing files for a non-completed task."""
        task_id = main.state.add_task(
            job_type=main.JobType.video,
            url="https://example.com/video",
            base_output_path="test",
            fmt="mp4",
        )

        response = await client_with_mock_state.get(f"/task/{task_id}/files")

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_download_task_file(
        self, client_with_mock_state: AsyncClient, mock_output_root, temp_dir
    ) -> None:
        """Test downloading a specific task file."""
        import main

        original_root = main.SERVER_OUTPUT_ROOT
        main.SERVER_OUTPUT_ROOT = temp_dir / "downloads"

        try:
            task_id = main.state.add_task(
                job_type=main.JobType.video,
                url="https://example.com/video",
                base_output_path="test",
                fmt="mp4",
            )

            task = main.state.get_task(task_id)
            assert task is not None

            # Create a test file
            output_path = Path(task.task_output_path)
            output_path.mkdir(parents=True, exist_ok=True)
            (output_path / "video.mp4").write_text("video content")

            main.state.update_task(task_id, "completed")

            response = await client_with_mock_state.get(f"/task/{task_id}/file?name=video.mp4")

            assert response.status_code == 200
            assert response.content == b"video content"
        finally:
            main.SERVER_OUTPUT_ROOT = original_root

    @pytest.mark.asyncio
    async def test_download_task_zip(
        self, client_with_mock_state: AsyncClient, mock_output_root, temp_dir
    ) -> None:
        """Test downloading task files as ZIP."""
        import zipfile
        from io import BytesIO

        import main

        original_root = main.SERVER_OUTPUT_ROOT
        main.SERVER_OUTPUT_ROOT = temp_dir / "downloads"

        try:
            task_id = main.state.add_task(
                job_type=main.JobType.video,
                url="https://example.com/video",
                base_output_path="test",
                fmt="mp4",
            )

            task = main.state.get_task(task_id)
            assert task is not None

            # Create test files
            output_path = Path(task.task_output_path)
            output_path.mkdir(parents=True, exist_ok=True)
            (output_path / "video.mp4").write_text("video content")
            (output_path / "info.json").write_text('{"info": "data"}')

            main.state.update_task(task_id, "completed")

            response = await client_with_mock_state.get(f"/task/{task_id}/zip")

            assert response.status_code == 200
            assert response.headers["content-type"] == "application/zip"

            # Verify ZIP contents
            zip_content = BytesIO(response.content)
            with zipfile.ZipFile(zip_content, "r") as zf:
                assert len(zf.namelist()) == 2
                assert "video.mp4" in zf.namelist()
                assert "info.json" in zf.namelist()
        finally:
            main.SERVER_OUTPUT_ROOT = original_root


class TestSubtitlesV2Endpoints:
    """Tests for /v2/subtitles endpoint with policy-based selection."""

    @pytest.mark.asyncio
    async def test_subtitles_v2_best_one_default(
        self, client_with_mock_state: AsyncClient, sample_video_url: str
    ) -> None:
        """Test v2 subtitles with default best_one mode."""
        payload = {
            "url": sample_video_url,
            "output_path": "subs-v2",
            # All fields use defaults
        }

        with patch("main.process_task") as mock_process:
            mock_process.return_value = None

            response = await client_with_mock_state.post("/v2/subtitles", json=payload)

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert "task_id" in data

            # Verify the payload was constructed correctly
            call_args = mock_process.call_args
            assert call_args is not None
            assert call_args.kwargs["job_type"] == main.JobType.subtitles_v2
            payload_dict = call_args.kwargs["payload"]
            assert payload_dict["english_mode"] == main.EnglishMode.best_one
            assert payload_dict["prefer"] == main.SubtitlePreference.manual_then_auto
            assert payload_dict["formats"] == main.SubtitleFormat.srt

    @pytest.mark.asyncio
    async def test_subtitles_v2_all_english(
        self, client_with_mock_state: AsyncClient, sample_video_url: str
    ) -> None:
        """Test v2 subtitles with all_english mode."""
        payload = {
            "url": sample_video_url,
            "english_mode": "all_english",
            "formats": "both",
        }

        with patch("main.process_task") as mock_process:
            mock_process.return_value = None

            response = await client_with_mock_state.post("/v2/subtitles", json=payload)

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"

            call_args = mock_process.call_args
            assert call_args.kwargs["payload"]["english_mode"] == main.EnglishMode.all_english
            assert call_args.kwargs["payload"]["formats"] == main.SubtitleFormat.both

    @pytest.mark.asyncio
    async def test_subtitles_v2_explicit_mode(
        self, client_with_mock_state: AsyncClient, sample_video_url: str
    ) -> None:
        """Test v2 subtitles with explicit mode and custom language list."""
        payload = {
            "url": sample_video_url,
            "english_mode": "explicit",
            "languages": ["es", "fr", "de"],
            "formats": "vtt",
        }

        with patch("main.process_task") as mock_process:
            mock_process.return_value = None

            response = await client_with_mock_state.post("/v2/subtitles", json=payload)

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"

            call_args = mock_process.call_args
            payload_dict = call_args.kwargs["payload"]
            assert payload_dict["english_mode"] == main.EnglishMode.explicit
            assert payload_dict["languages"] == ["es", "fr", "de"]
            assert payload_dict["formats"] == main.SubtitleFormat.vtt

    @pytest.mark.asyncio
    async def test_subtitles_v2_auto_only(
        self, client_with_mock_state: AsyncClient, sample_video_url: str
    ) -> None:
        """Test v2 subtitles preferring automatic captions only."""
        payload = {
            "url": sample_video_url,
            "prefer": "auto_only",
            "formats": "srt",
        }

        with patch("main.process_task") as mock_process:
            mock_process.return_value = None

            response = await client_with_mock_state.post("/v2/subtitles", json=payload)

            assert response.status_code == 200

            call_args = mock_process.call_args
            assert call_args.kwargs["payload"]["prefer"] == main.SubtitlePreference.auto_only

    @pytest.mark.asyncio
    async def test_subtitles_v2_custom_ranking(
        self, client_with_mock_state: AsyncClient, sample_video_url: str
    ) -> None:
        """Test v2 subtitles with custom language ranking."""
        payload = {
            "url": sample_video_url,
            "english_rank": ["en-GB", "en", "en-US"],
        }

        with patch("main.process_task") as mock_process:
            mock_process.return_value = None

            response = await client_with_mock_state.post("/v2/subtitles", json=payload)

            assert response.status_code == 200

            call_args = mock_process.call_args
            assert call_args.kwargs["payload"]["english_rank"] == ["en-GB", "en", "en-US"]

    @pytest.mark.asyncio
    async def test_subtitles_v2_deduplication(
        self, client_with_mock_state: AsyncClient, sample_video_url: str
    ) -> None:
        """Test that identical v2 requests are deduplicated."""
        payload = {
            "url": sample_video_url,
            "english_mode": "best_one",
            "formats": "srt",
        }

        with patch("main.process_task") as mock_process:
            mock_process.return_value = None

            # First request
            response1 = await client_with_mock_state.post("/v2/subtitles", json=payload)
            task_id_1 = response1.json()["task_id"]

            # Second identical request
            response2 = await client_with_mock_state.post("/v2/subtitles", json=payload)
            task_id_2 = response2.json()["task_id"]

            # Should return the same task_id
            assert task_id_1 == task_id_2

            # process_task should only be called once (for first request)
            assert mock_process.call_count == 1

    @pytest.mark.asyncio
    async def test_subtitles_v2_different_params_create_new_task(
        self, client_with_mock_state: AsyncClient, sample_video_url: str
    ) -> None:
        """Test that different parameters create different tasks."""
        with patch("main.process_task") as mock_process:
            mock_process.return_value = None

            # Different formats should create different tasks
            payload1 = {"url": sample_video_url, "formats": "srt"}
            response1 = await client_with_mock_state.post("/v2/subtitles", json=payload1)
            task_id_1 = response1.json()["task_id"]

            payload2 = {"url": sample_video_url, "formats": "vtt"}
            response2 = await client_with_mock_state.post("/v2/subtitles", json=payload2)
            task_id_2 = response2.json()["task_id"]

            assert task_id_1 != task_id_2
            assert mock_process.call_count == 2

    @pytest.mark.asyncio
    async def test_subtitles_v2_explicit_requires_languages(
        self, client_with_mock_state: AsyncClient, sample_video_url: str
    ) -> None:
        """Test that explicit mode requires languages to be set (service layer)."""
        # This test verifies the API accepts the request
        # The actual validation happens in the service layer during download
        payload = {
            "url": sample_video_url,
            "english_mode": "explicit",
            # languages is empty by default
        }

        with patch("main.process_task") as mock_process:
            mock_process.return_value = None

            # API should accept the request (validation happens during processing)
            response = await client_with_mock_state.post("/v2/subtitles", json=payload)
            assert response.status_code == 200


class TestAuthentication:
    """Tests for API key authentication."""

    @pytest.mark.asyncio
    async def test_auth_enabled_requires_key(self) -> None:
        """Test that enabled auth requires valid API key."""
        import main

        original_config = main.auth_config

        try:
            # Enable auth
            main.auth_config = main.AuthConfig(
                enabled=True, master_key="test-key", header_name="X-API-Key"
            )

            transport = ASGITransport(app=main.app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/tasks")

                # Should fail without API key
                assert response.status_code == 401
        finally:
            main.auth_config = original_config

    @pytest.mark.asyncio
    async def test_auth_disabled_allows_access(self) -> None:
        """Test that disabled auth allows access."""
        import main

        original_config = main.auth_config

        try:
            # Disable auth
            main.auth_config = main.AuthConfig(enabled=False)

            with patch("main.process_task"):
                transport = ASGITransport(app=main.app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/download",
                        json={
                            "url": "https://example.com/video",
                            "format": "best",
                        },
                    )

                    # Should succeed without API key
                    assert response.status_code in (200, 400)  # May fail on other validation
        finally:
            main.auth_config = original_config
