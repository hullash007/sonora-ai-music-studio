from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from sonora.library import (
    list_track_records,
    read_track_record,
    resolve_audio_file,
    write_track_record,
)


class MusicLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.audio_root = Path(self.temp_dir.name)
        (self.audio_root / "jobs" / "sample").mkdir(parents=True)
        (self.audio_root / "jobs" / "sample" / "track.mp3").write_bytes(b"fake-mp3-data")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def make_record(self, *, created_at: float = 100.0) -> dict:
        job_id = str(uuid.uuid4())
        own_audio = self.audio_root / "jobs" / job_id / "track.mp3"
        own_audio.parent.mkdir(parents=True)
        own_audio.write_bytes(b"fake-mp3-data")
        return {
            "job_id": job_id,
            "title": "Test track",
            "status": "complete",
            "prompt": "Warm ambient piano",
            "lyrics": "",
            "filename": "track.mp3",
            "file": f"jobs/{job_id}/track.mp3",
            "duration_seconds": 30,
            "created_at": created_at,
            "completed_at": created_at + 5,
            "engine": "ACE-Step 1.5",
            "model_revision": "test-revision",
        }

    def test_write_and_read_record(self) -> None:
        record = self.make_record()
        path = write_track_record(self.audio_root, record)
        self.assertTrue(path.is_file())
        actual = read_track_record(self.audio_root, record["job_id"])
        self.assertEqual(actual["title"], "Test track")
        self.assertEqual(actual["job_id"], record["job_id"])

    def test_list_is_newest_first(self) -> None:
        older = self.make_record(created_at=10)
        newer = self.make_record(created_at=20)
        write_track_record(self.audio_root, older)
        write_track_record(self.audio_root, newer)
        records = list_track_records(self.audio_root)
        self.assertEqual([r["job_id"] for r in records], [newer["job_id"], older["job_id"]])

    def test_invalid_job_id_returns_none(self) -> None:
        self.assertIsNone(read_track_record(self.audio_root, "../../etc/passwd"))

    def test_path_traversal_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            resolve_audio_file(self.audio_root, "../../outside.mp3", require_exists=False)

    def test_absolute_path_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            resolve_audio_file(self.audio_root, str(self.audio_root / "secret.mp3"), require_exists=False)

    def test_only_completed_records_can_be_written(self) -> None:
        record = self.make_record()
        record["status"] = "running"
        with self.assertRaises(ValueError):
            write_track_record(self.audio_root, record)

    def test_missing_audio_path_is_rejected(self) -> None:
        record = self.make_record()
        record["file"] = "../../escape.mp3"
        with self.assertRaises(ValueError):
            write_track_record(self.audio_root, record)

    def test_corrupt_json_is_ignored(self) -> None:
        library_dir = self.audio_root / "library"
        library_dir.mkdir()
        bad_id = str(uuid.uuid4())
        (library_dir / f"{bad_id}.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(list_track_records(self.audio_root), [])

    def test_record_identity_must_match_filename(self) -> None:
        library_dir = self.audio_root / "library"
        library_dir.mkdir()
        file_id = str(uuid.uuid4())
        different_id = str(uuid.uuid4())
        (library_dir / f"{file_id}.json").write_text(
            json.dumps({"job_id": different_id, "status": "complete", "file": "jobs/sample/track.mp3"}),
            encoding="utf-8",
        )
        self.assertIsNone(read_track_record(self.audio_root, file_id))

    def test_cross_job_audio_is_rejected_on_write_and_read(self) -> None:
        first, second = self.make_record(), self.make_record()
        first["file"] = second["file"]
        with self.assertRaises(ValueError):
            write_track_record(self.audio_root, first)
        directory = self.audio_root / "library"
        directory.mkdir(exist_ok=True)
        (directory / f"{first['job_id']}.json").write_text(json.dumps(first))
        self.assertIsNone(read_track_record(self.audio_root, first["job_id"]))
        self.assertEqual(list_track_records(self.audio_root), [])

    def test_symlink_to_other_job_and_missing_audio_are_not_visible(self) -> None:
        first, second = self.make_record(), self.make_record()
        first_path = self.audio_root / first["file"]
        first_path.unlink()
        first_path.symlink_to(self.audio_root / second["file"])
        with self.assertRaises(ValueError):
            write_track_record(self.audio_root, first)
        first_path.unlink()
        with self.assertRaises(FileNotFoundError):
            write_track_record(self.audio_root, first)

    def test_limit_is_validated(self) -> None:
        with self.assertRaises(ValueError):
            list_track_records(self.audio_root, limit=0)


if __name__ == "__main__":
    unittest.main()
