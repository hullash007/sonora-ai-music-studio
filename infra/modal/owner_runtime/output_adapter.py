"""Private callback adapter. Never used to grant a retry or change frozen code."""
from pathlib import Path
import uuid

def relative_job_output(audio_root, job_id, output):
    root = Path(audio_root).resolve(strict=True)
    job_dir = root / 'jobs' / str(uuid.UUID(str(job_id)))
    candidate = Path(output).resolve(strict=True)
    if not candidate.is_file():
        raise ValueError('Output must be an existing file')
    try:
        candidate.relative_to(job_dir)
    except ValueError as exc:
        raise ValueError('Output must be inside the exact job') from exc
    return candidate.relative_to(root).as_posix()
