
import sys
from pathlib import Path

import modal

# --------------------------------------------------
# Configuration
# --------------------------------------------------

APP_NAME = "ace-step-15-api"
PROJECT_DIR = "/opt/ACE-Step-1.5"
AUDIO_DIR = "/mnt/ace-step-audio"

app = modal.App(APP_NAME)

# Persistent storage for downloaded model weights
checkpoints_volume = modal.Volume.from_name(
    "ace-step-checkpoints",
    create_if_missing=True,
)

# Persistent storage for generated audio and caches
audio_volume = modal.Volume.from_name(
    "ace-step-audio",
    create_if_missing=True,
)

# --------------------------------------------------
# Build the container
# --------------------------------------------------

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install(
        "git",
        "ffmpeg",
        "libsndfile1",
        "libgomp1",
    )
    .pip_install("uv")
    .run_commands(
        f"git clone --depth 1 "
        f"https://github.com/ACE-Step/ACE-Step-1.5.git "
        f"{PROJECT_DIR}",
    )
    .run_commands(
        f"cd {PROJECT_DIR} && uv sync --no-dev --python 3.12"
    )
)

# --------------------------------------------------
# Deploy ACE-Step API
# --------------------------------------------------

@app.function(
    image=image,
    gpu=["A10", "L4"],
    cpu=8,
    memory=32768,
    timeout=3600,
    startup_timeout=1800,
    max_containers=1,

    volumes={
        f"{PROJECT_DIR}/checkpoints": checkpoints_volume,
        AUDIO_DIR: audio_volume,
    },

    secrets=[
        modal.Secret.from_name("ace-step-api"),
    ],

    env={
        "ACESTEP_API_HOST": "0.0.0.0",
        "ACESTEP_API_PORT": "8001",
        "ACESTEP_API_WORKERS": "1",

        "ACESTEP_CONFIG_PATH": "acestep-v15-turbo",
        "ACESTEP_LM_MODEL_PATH": "acestep-5Hz-lm-1.7B",
        "ACESTEP_LM_BACKEND": "vllm",

        "ACESTEP_DEVICE": "auto",
        "ACESTEP_INIT_LLM": "auto",
        "ACESTEP_NO_INIT": "true",

        "ACESTEP_TMPDIR": f"{AUDIO_DIR}/tmp",

        "TRITON_CACHE_DIR": f"{AUDIO_DIR}/triton",
        "TORCHINDUCTOR_CACHE_DIR": f"{AUDIO_DIR}/torchinductor",

        "PYTHONUNBUFFERED": "1",
    },
)
@modal.asgi_app()
def ace_step_api():

    # Make the repository and its uv environment visible
    # to the Modal Python runtime.
    sys.path.insert(0, PROJECT_DIR)

    site_packages = (
        Path(PROJECT_DIR)
        / ".venv"
        / "lib"
        / "python3.12"
        / "site-packages"
    )

    sys.path.insert(0, str(site_packages))

    # Import the official ACE-Step API application.
    from acestep.api_server import create_app

    return create_app()
