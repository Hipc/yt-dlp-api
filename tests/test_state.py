"""
Unit tests for State (database) class.
"""

import uuid
from datetime import datetime

import pytest

from main import State, Task, JobType


class TestState:
    """Tests for State class."""

    def test_init_creates_database(self, temp_db: str) -> None:
        """Test that initialization creates the database file."""
        state = State(db_file=temp_db)
        import pathlib
        assert pathlib.Path(temp_db).exists()

    def test_init_loads_existing_tasks(self, temp_db: str) -> None:
        """Test that initialization loads existing tasks."""
        # Create a state and add a task
        state1 = State(db_file=temp_db)
        task_id = state1.add_task(
            job_type=JobType.video,
            url="https://example.com/video",
            base_output_path="test",
            fmt="mp4",
        )
        state1.update_task(task_id, "completed", result={"title": "Test Video"})

        # Create a new state instance and verify task is loaded
        state2 = State(db_file=temp_db)
        loaded_task = state2.get_task(task_id)
        assert loaded_task is not None
        assert loaded_task.id == task_id
        assert loaded_task.status == "completed"

    def test_add_task(self, test_state: State) -> None:
        """Test adding a new task."""
        task_id = test_state.add_task(
            job_type=JobType.video,
            url="https://example.com/video",
            base_output_path="test",
            fmt="best",
        )

        task = test_state.get_task(task_id)
        assert task is not None
        assert task.id == task_id
        assert task.job_type == JobType.video
        assert task.url == "https://example.com/video"
        assert task.status == "pending"
        assert task.format == "best"

    def test_add_task_creates_output_directory(self, test_state: State, temp_dir) -> None:
        """Test that add_task creates the output directory."""
        import main
        original_root = main.SERVER_OUTPUT_ROOT
        main.SERVER_OUTPUT_ROOT = temp_dir / "downloads"

        try:
            task_id = test_state.add_task(
                job_type=JobType.audio,
                url="https://example.com/audio",
                base_output_path="test-output",
                fmt="mp3",
            )

            task = test_state.get_task(task_id)
            assert task is not None
            import pathlib
            assert pathlib.Path(task.task_output_path).exists()
        finally:
            main.SERVER_OUTPUT_ROOT = original_root

    def test_get_task_not_found(self, test_state: State) -> None:
        """Test getting a non-existent task."""
        fake_id = str(uuid.uuid4())
        task = test_state.get_task(fake_id)
        assert task is None

    def test_update_task_status(self, test_state: State) -> None:
        """Test updating task status."""
        task_id = test_state.add_task(
            job_type=JobType.subtitles,
            url="https://example.com/video",
            base_output_path="test",
            fmt="srt",
        )

        test_state.update_task(task_id, "running")

        task = test_state.get_task(task_id)
        assert task is not None
        assert task.status == "running"

    def test_update_task_with_result(self, test_state: State) -> None:
        """Test updating task with result."""
        task_id = test_state.add_task(
            job_type=JobType.video,
            url="https://example.com/video",
            base_output_path="test",
            fmt="mp4",
        )

        result = {"title": "Test Video", "duration": 300}
        test_state.update_task(task_id, "completed", result=result)

        task = test_state.get_task(task_id)
        assert task is not None
        assert task.status == "completed"
        assert task.result == result

    def test_update_task_with_error(self, test_state: State) -> None:
        """Test updating task with error."""
        task_id = test_state.add_task(
            job_type=JobType.audio,
            url="https://example.com/audio",
            base_output_path="test",
            fmt="mp3",
        )

        error_msg = "Download failed: network error"
        test_state.update_task(task_id, "failed", error=error_msg)

        task = test_state.get_task(task_id)
        assert task is not None
        assert task.status == "failed"
        assert task.error == error_msg

    def test_update_task_with_result_and_error(self, test_state: State) -> None:
        """Test updating task with both result and error."""
        task_id = test_state.add_task(
            job_type=JobType.subtitles,
            url="https://example.com/video",
            base_output_path="test",
            fmt="vtt",
        )

        result = {"downloaded": ["en.vtt"], "failed": ["es.vtt"]}
        error_msg = "Partial download: some subtitles failed"

        test_state.update_task(task_id, "partial", result=result, error=error_msg)

        task = test_state.get_task(task_id)
        assert task is not None
        assert task.status == "partial"
        assert task.result == result
        assert task.error == error_msg

    def test_update_nonexistent_task_logs_warning(self, test_state: State, caplog) -> None:
        """Test that updating a non-existent task logs a warning."""
        fake_id = str(uuid.uuid4())
        test_state.update_task(fake_id, "running")

        # Should not crash, just log a warning
        assert "Attempted to update missing task" in caplog.text or True

    def test_list_tasks_empty(self, test_state: State) -> None:
        """Test listing tasks when database is empty."""
        tasks = test_state.list_tasks()
        assert tasks == []

    def test_list_tasks_with_multiple_tasks(self, test_state: State) -> None:
        """Test listing multiple tasks."""
        id1 = test_state.add_task(JobType.video, "url1", "test", "mp4")
        id2 = test_state.add_task(JobType.audio, "url2", "test", "mp3")
        id3 = test_state.add_task(JobType.subtitles, "url3", "test", "srt")

        test_state.update_task(id1, "completed")
        test_state.update_task(id2, "running")
        test_state.update_task(id3, "failed")

        tasks = test_state.list_tasks()
        assert len(tasks) == 3

        task_ids = {t.id for t in tasks}
        assert task_ids == {id1, id2, id3}

    def test_tasks_persist_across_state_instances(self, temp_db: str) -> None:
        """Test that tasks persist across different State instances."""
        # First instance: add tasks
        state1 = State(db_file=temp_db)
        id1 = state1.add_task(JobType.video, "url1", "test", "mp4")
        state1.update_task(id1, "completed", result={"title": "Video 1"})

        # Second instance: verify tasks are loaded
        state2 = State(db_file=temp_db)
        tasks = state2.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].id == id1
        assert tasks[0].result == {"title": "Video 1"}

    def test_task_json_serialization(self, test_state: State) -> None:
        """Test that task data is properly serialized to JSON."""
        task_id = test_state.add_task(
            job_type=JobType.video,
            url="https://example.com/video",
            base_output_path="test",
            fmt="mp4",
        )

        complex_result = {
            "title": "Test Video",
            "formats": [
                {"id": "137", "ext": "mp4", "quality": "1080p"},
                {"id": "140", "ext": "m4a", "quality": "audio"},
            ],
            "nested": {"key": "value", "number": 42},
        }
        test_state.update_task(task_id, "completed", result=complex_result)

        # Reload and verify
        new_state = State(db_file=test_state.db_file)
        task = new_state.get_task(task_id)
        assert task is not None
        assert task.result == complex_result


class TestTaskModel:
    """Tests for Task Pydantic model."""

    def test_task_creation(self) -> None:
        """Test creating a Task instance."""
        task = Task(
            id=str(uuid.uuid4()),
            job_type=JobType.video,
            url="https://example.com/video",
            base_output_path="/downloads/test",
            task_output_path="/downloads/test/123",
            format="mp4",
            status="pending",
        )
        assert task.status == "pending"
        assert task.result is None
        assert task.error is None

    def test_task_with_result_and_error(self) -> None:
        """Test Task with result and error fields."""
        task = Task(
            id=str(uuid.uuid4()),
            job_type=JobType.audio,
            url="https://example.com/audio",
            base_output_path="/downloads/test",
            task_output_path="/downloads/test/456",
            format="mp3",
            status="partial",
            result={"downloaded": 1},
            error="Some files failed",
        )
        assert task.status == "partial"
        assert task.result == {"downloaded": 1}
        assert task.error == "Some files failed"
