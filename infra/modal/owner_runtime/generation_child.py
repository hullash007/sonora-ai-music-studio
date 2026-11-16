import os,sys,json
from pathlib import Path
from offline_guard import install, reject_download, assert_no_download_attempts
install()
PROJECT_DIR='/opt/ACE-Step-1.5'
CHECKPOINT_DIR=str(Path(PROJECT_DIR+'/checkpoints').resolve())
AUDIO_DIR='/mnt/sonora-private-audio'
VALIDATION_DURATION_SECONDS=30
job_id=sys.argv[1]
prompt=sys.argv[2]
lyrics=sys.argv[3]
if not 1<=len(prompt)<=512 or len(lyrics)>4096:raise ValueError('Invalid bounded generation request')
def update_progress(stage):print('STAGE',stage,flush=True)
from prepare import verify_snapshot,verify_source,read_json
verify_source(Path(PROJECT_DIR),read_json('/opt/sonora-validation-prep/source-pins.json'))
manifest=read_json('/opt/sonora-validation-prep/model-manifest.json')
verify_snapshot(Path(CHECKPOINT_DIR),manifest,effective=True)
"""Load the pinned model and synchronously generate the audio file."""
update_progress("Checking staging generation state")

site_packages = (
    Path(PROJECT_DIR)
    / ".venv"
    / "lib"
    / "python3.12"
    / "site-packages"
)
sys.path.insert(0, str(site_packages))

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("PYTHONUNBUFFERED", "1")

update_progress("Preparing isolated staging checkpoint directory")
os.makedirs(CHECKPOINT_DIR, exist_ok=True)

# Check before checkpoint/model initialization. If cancellation won
# while the function was starting, do not begin expensive model work.

update_progress("Downloading or loading ACE-Step checkpoint")
import acestep.model_downloader as _downloader
for _name in dir(_downloader):
    if _name.startswith('download_') or _name == '_smart_download':setattr(_downloader,_name,reject_download)
from acestep.api import model_download as _api_download
_api_download.download_from_huggingface=reject_download
_api_download.download_from_modelscope=reject_download
_api_download.can_access_google=reject_download
from acestep.api.model_download import ensure_model_downloaded
from acestep.handler import AceStepHandler
from acestep.inference import (
    GenerationConfig,
    GenerationParams,
    generate_music,
)
from acestep.llm_inference import LLMHandler

ensure_model_downloaded(
    "acestep-5Hz-lm-1.7B",
    CHECKPOINT_DIR,
)


update_progress("Initializing ACE-Step DiT model")
dit_handler = AceStepHandler()
dit_message, dit_ok = dit_handler.initialize_service(
    project_root=PROJECT_DIR,
    config_path="acestep-v15-turbo",
    device="cuda",
    use_flash_attention=False,
    compile_model=False,
    offload_to_cpu=False,
    offload_dit_to_cpu=False,
)
if not dit_ok:
    raise RuntimeError(
        f"ACE-Step DiT initialization failed: {dit_message}"
    )


actual_attention=dit_handler.model.config._attn_implementation
print('ACTUAL_DIT_ATTENTION',actual_attention,flush=True)
if actual_attention != 'sdpa' or next(dit_handler.model.parameters()).device.type!='cuda':raise RuntimeError('Unexpected DiT attention backend/device')
update_progress("Initializing ACE-Step language model")
llm_handler = LLMHandler()
llm_message, llm_ok = llm_handler.initialize(
    checkpoint_dir=CHECKPOINT_DIR,
    lm_model_path="acestep-5Hz-lm-1.7B",
    backend="pt",
    device="cuda",
    offload_to_cpu=False,
    dtype=None,
)
if not llm_ok:
    raise RuntimeError(
        f"ACE-Step language-model initialization failed: {llm_message}"
    )

print('ACTUAL_LM_BACKEND',llm_handler.llm_backend,flush=True)
if llm_handler.llm_backend!='pt' or llm_handler.device!='cuda':raise RuntimeError('Unexpected LM backend/device')
assert_no_download_attempts()
# Final check directly before starting the actual generation call.

job_dir = os.path.join(AUDIO_DIR, "jobs", job_id)
os.makedirs(job_dir, exist_ok=True)

update_progress("Generating 30-second validation track")

params = GenerationParams(
    task_type="text2music",
    caption=prompt,
    lyrics=lyrics if lyrics.strip() else "[Instrumental]",
    duration=float(VALIDATION_DURATION_SECONDS),
    vocal_language="unknown",
    thinking=True,
    use_cot_metas=True,
    use_cot_caption=True,
    use_cot_language=True,
    inference_steps=8,
    shift=3.0,
)

generation_config = GenerationConfig(
    batch_size=1,
    allow_lm_batch=False,
    use_random_seed=True,
    audio_format="mp3",
    mp3_bitrate="128k",
    mp3_sample_rate=48000,
)

result = generate_music(
    dit_handler,
    llm_handler,
    params,
    generation_config,
    save_dir=job_dir,
)

if not result.success:
    raise RuntimeError(
        result.error
        or result.status_message
        or "ACE-Step generation failed"
    )

if not result.audios:
    raise RuntimeError(
        "ACE-Step completed without returning an audio file"
    )

output_path = result.audios[0].get("path")
if not output_path:
    raise RuntimeError("ACE-Step returned an empty audio path")

resolved_output = os.path.abspath(output_path)
resolved_job_dir = os.path.abspath(job_dir)

if os.path.commonpath(
    [resolved_output, resolved_job_dir]
) != resolved_job_dir:
    raise RuntimeError(
        "Generated audio path is outside this job's directory"
    )

if not os.path.isfile(resolved_output):
    raise RuntimeError(
        f"Generated audio file was not found: {resolved_output}"
    )

verify_snapshot(Path(CHECKPOINT_DIR),manifest,effective=True)
assert_no_download_attempts()
print('OUTPUT_PATH',resolved_output,flush=True)
