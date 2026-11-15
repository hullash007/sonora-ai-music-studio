import hmac
import os
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any

import modal

# ---------------------------------------------------------------------------
# App and persistent resources
# ---------------------------------------------------------------------------

APP_NAME = "ace-step-instinct-portal"
PROJECT_DIR = "/opt/ACE-Step-1.5"
CHECKPOINT_DIR = f"{PROJECT_DIR}/checkpoints"
AUDIO_DIR = "/mnt/ace-step-audio"

app = modal.App(APP_NAME)

# Reuse the volumes already created by the first deployment.
checkpoints_volume = modal.Volume.from_name("ace-step-checkpoints")
audio_volume = modal.Volume.from_name("ace-step-audio")

# Durable job state shared by the CPU web service and GPU worker.
job_store = modal.Dict.from_name("ace-step-jobs", create_if_missing=True)

# Pin the source revision used for this deployment so future upstream changes
# cannot unexpectedly alter the running environment.
ACE_STEP_COMMIT = "ca1e85fe9430179831e6bc6be790c332190a3866"

# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

portal_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi")
)

music_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "ffmpeg", "libsndfile1", "libgomp1")
    .pip_install("uv")
    .run_commands(
        f"git clone https://github.com/ACE-Step/ACE-Step-1.5.git {PROJECT_DIR} "
        f"&& cd {PROJECT_DIR} && git checkout {ACE_STEP_COMMIT}"
    )
    .run_commands(f"cd {PROJECT_DIR} && uv sync --no-dev --python 3.12")
)

# ---------------------------------------------------------------------------
# GPU worker: a separate, on-demand function. It has no minimum containers,
# so Modal can scale it to zero when no music is being generated.
# ---------------------------------------------------------------------------

@app.function(
    image=music_image,
    gpu=["A10", "L4"],
    cpu=8,
    memory=32768,
    timeout=1800,
    startup_timeout=1800,
    min_containers=0,
    max_containers=1,
    scaledown_window=30,
    volumes={
        f"{PROJECT_DIR}/checkpoints": checkpoints_volume,
        AUDIO_DIR: audio_volume,
    },
)
def generate_track(job_id: str, prompt: str, lyrics: str, duration: int) -> None:
    """Load ACE-Step, generate one track, commit it, and update durable state."""

    def update_job(**updates: Any) -> None:
        current = job_store.get(job_id, {"job_id": job_id})
        current.update(updates)
        job_store[job_id] = current

    try:
        update_job(status="running", stage="Starting GPU worker", error=None)

        # Use the repository's own uv environment from the image build.
        sys.path.insert(0, PROJECT_DIR)
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

        update_job(stage="Loading ACE-Step music model")

        from acestep.handler import AceStepHandler
        from acestep.llm_inference import LLMHandler
        from acestep.inference import (
            GenerationConfig,
            GenerationParams,
            generate_music,
        )
        from acestep.api.model_download import ensure_model_downloaded

        # The checkpoints Volume retains these weights between jobs. This call
        # is a no-op when the files are already present.
        ensure_model_downloaded("acestep-5Hz-lm-1.7B", CHECKPOINT_DIR)

        dit_handler = AceStepHandler()
        status_message, dit_ok = dit_handler.initialize_service(
            project_root=PROJECT_DIR,
            config_path="acestep-v15-turbo",
            device="cuda",
            use_flash_attention=True,
            compile_model=False,
            offload_to_cpu=False,
            offload_dit_to_cpu=False,
        )
        if not dit_ok:
            raise RuntimeError(f"ACE-Step DiT initialization failed: {status_message}")

        update_job(stage="Loading ACE-Step language model")

        llm_handler = LLMHandler()
        llm_status, llm_ok = llm_handler.initialize(
            checkpoint_dir=CHECKPOINT_DIR,
            lm_model_path="acestep-5Hz-lm-1.7B",
            backend="vllm",
            device="cuda",
            offload_to_cpu=False,
            dtype=None,
        )
        if not llm_ok:
            raise RuntimeError(f"ACE-Step language-model initialization failed: {llm_status}")

        update_job(stage="Generating music")

        job_dir = os.path.join(AUDIO_DIR, "jobs", job_id)
        os.makedirs(job_dir, exist_ok=True)

        generation_params = GenerationParams(
            task_type="text2music",
            caption=prompt,
            lyrics=lyrics if lyrics.strip() else "[Instrumental]",
            duration=float(duration),
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
            generation_params,
            generation_config,
            save_dir=job_dir,
        )

        if not result.success:
            raise RuntimeError(result.error or result.status_message or "Music generation failed")
        if not result.audios:
            raise RuntimeError("ACE-Step completed without returning an audio file")

        audio_path = result.audios[0].get("path")
        if not audio_path or not os.path.isfile(audio_path):
            raise RuntimeError(f"Generated audio file was not found: {audio_path}")

        audio_abs = os.path.abspath(audio_path)
        audio_root = os.path.abspath(job_dir)
        if os.path.commonpath([audio_abs, audio_root]) != audio_root:
            raise RuntimeError("Generated audio path is outside the job output directory")

        update_job(stage="Saving generated MP3")
        audio_volume.commit()

        relative_audio_path = os.path.relpath(audio_abs, AUDIO_DIR).replace("\\", "/")
        download_token = uuid.uuid4().hex + uuid.uuid4().hex
        update_job(
            status="complete",
            stage="Music generation complete",
            file=relative_audio_path,
            filename=os.path.basename(audio_abs),
            download_token=download_token,
            duration_seconds=duration,
            status_message=result.status_message,
            error=None,
        )

    except Exception as exc:
        update_job(
            status="failed",
            stage="Generation failed",
            error=str(exc),
            traceback=traceback.format_exc()[-12000:],
        )
        raise

# ---------------------------------------------------------------------------
# CPU-only webpage and job API. Visiting or polling this service does not
# allocate the ACE-Step GPU. The GPU function is invoked only on Generate.
# ---------------------------------------------------------------------------

HTML_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ACE-Step Music Studio</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#10131a;color:#edf1f7;font:16px/1.5 system-ui,Segoe UI,sans-serif}
main{max-width:820px;margin:48px auto;padding:32px;background:#1a202b;border:1px solid #303b4d;border-radius:18px}
h1{margin:0 0 6px;font-size:30px}p.sub{margin:0 0 28px;color:#aeb9c9}label{display:block;margin:18px 0 7px;font-weight:650}
input,textarea{width:100%;padding:13px 14px;border:1px solid #465369;border-radius:9px;background:#111722;color:#f3f6fb;font:inherit}
textarea{min-height:140px;resize:vertical}textarea.small{min-height:100px}input[type=number]{max-width:170px}
button{margin-top:22px;padding:13px 22px;border:0;border-radius:9px;background:#7dd3fc;color:#082032;font-weight:750;font-size:16px;cursor:pointer}
button:disabled{opacity:.55;cursor:wait}.hint{font-size:13px;color:#aeb9c9;margin-top:5px}.panel{margin-top:24px;padding:16px;border:1px solid #3c485b;border-radius:10px;background:#131a25;white-space:pre-wrap}
a{color:#7dd3fc;font-weight:700}.hidden{display:none}.status{color:#dce7f5}.error{color:#fca5a5}
@media(max-width:640px){main{margin:12px;padding:20px}}
</style>
</head>
<body>
<main>
<h1>ACE-Step Music Studio</h1>
<p class="sub">Submit a music brief. The GPU starts only when you generate a track.</p>
<form id="musicForm">
<label for="access">Studio access password</label>
<input id="access" type="password" autocomplete="current-password" required>
<label for="prompt">Music prompt</label>
<textarea id="prompt" maxlength="512" required placeholder="Describe genre, mood, instruments, tempo, arrangement, vocals and production style."></textarea>
<div class="hint">Maximum 512 characters. Be specific about the sound you want.</div>
<label for="lyrics">Lyrics (optional)</label>
<textarea id="lyrics" class="small" maxlength="4096" placeholder="Leave blank for an instrumental track, or paste lyrics here."></textarea>
<label for="duration">Track duration (seconds)</label>
<input id="duration" type="number" min="10" max="480" value="180" required>
<div class="hint">Allowed range: 10–480 seconds. Longer tracks take longer and use more GPU compute.</div>
<button id="generateButton" type="submit">Generate music</button>
</form>
<section id="resultPanel" class="panel hidden" aria-live="polite">
<div id="status" class="status">Ready.</div>
<div id="jobLine" class="hint"></div>
<div id="downloadArea" class="hidden" style="margin-top:14px"><a id="downloadLink" href="#">Download MP3</a></div>
</section>
</main>
<script>
const form=document.getElementById('musicForm');
const button=document.getElementById('generateButton');
const panel=document.getElementById('resultPanel');
const statusEl=document.getElementById('status');
const jobLine=document.getElementById('jobLine');
const downloadArea=document.getElementById('downloadArea');
let pollTimer=null;
form.addEventListener('submit',async(e)=>{
  e.preventDefault();
  if(pollTimer) clearInterval(pollTimer);
  downloadArea.classList.add('hidden');
  panel.classList.remove('hidden');
  button.disabled=true;
  statusEl.textContent='Submitting job to the GPU worker…';
  statusEl.className='status';
  jobLine.textContent='';
  const password=document.getElementById('access').value;
  const payload={
    prompt:document.getElementById('prompt').value.trim(),
    lyrics:document.getElementById('lyrics').value,
    duration:Number(document.getElementById('duration').value)
  };
  try{
    const response=await fetch('/api/generate',{method:'POST',headers:{'Content-Type':'application/json','X-Portal-Password':password},body:JSON.stringify(payload)});
    const data=await response.json();
    if(!response.ok) throw new Error(data.detail||'Could not submit generation job');
    jobLine.textContent='Job ID: '+data.job_id;
    statusEl.textContent='Queued. Modal will start a GPU only for this generation.';
    const finished=await pollJob(data.job_id,password);
    if(!finished) pollTimer=setInterval(()=>pollJob(data.job_id,password),5000);
  }catch(err){
    statusEl.textContent=err.message;
    statusEl.className='error';
    button.disabled=false;
  }
});
async function pollJob(jobId,password){
  try{
    const response=await fetch('/api/status/'+encodeURIComponent(jobId),{headers:{'X-Portal-Password':password}});
    const data=await response.json();
    if(!response.ok) throw new Error(data.detail||'Could not read job status');
    statusEl.textContent=data.stage||data.status;
    if(data.status==='complete'){
      clearInterval(pollTimer);pollTimer=null;
      statusEl.textContent='Music generated successfully.';
      document.getElementById('downloadLink').href='/api/download/'+encodeURIComponent(jobId)+'?token='+encodeURIComponent(data.download_token);
      downloadArea.classList.remove('hidden');button.disabled=false;return true;
    }else if(data.status==='failed'){
      clearInterval(pollTimer);pollTimer=null;
      statusEl.textContent='Generation failed: '+(data.error||'Unknown error');
      statusEl.className='error';button.disabled=false;return true;
    }
  }catch(err){
    statusEl.textContent='Checking job status: '+err.message;
  }
  return false;
}
</script>
</body>
</html>"""

@app.function(
    image=portal_image,
    cpu=1,
    memory=1024,
    min_containers=0,
    max_containers=1,
    scaledown_window=60,
    volumes={AUDIO_DIR: audio_volume},
    secrets=[modal.Secret.from_name("ace-step-portal")],
)
@modal.asgi_app()
def music_portal():
    from fastapi import FastAPI, Header, HTTPException, Query
    from fastapi.responses import FileResponse, HTMLResponse

    web = FastAPI(title="ACE-Step Music Studio")

    def verify_password(supplied: str | None) -> None:
        expected = os.environ.get("PORTAL_PASSWORD", "")
        if not expected:
            raise HTTPException(status_code=503, detail="Portal password is not configured")
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="Incorrect studio password")

    @web.get("/", response_class=HTMLResponse)
    def home():
        return HTML_PAGE

    @web.get("/api/health")
    def portal_health():
        return {"status": "ok", "service": "ACE-Step CPU portal"}

    @web.post("/api/generate")
    def submit_generation(payload: dict, x_portal_password: str | None = Header(default=None)):
        verify_password(x_portal_password)

        prompt = str(payload.get("prompt", "")).strip()
        lyrics = str(payload.get("lyrics", ""))
        try:
            duration = int(payload.get("duration", 180))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Duration must be a whole number of seconds")

        if not prompt:
            raise HTTPException(status_code=400, detail="A music prompt is required")
        if len(prompt) > 512:
            raise HTTPException(status_code=400, detail="Prompt must be 512 characters or fewer")
        if len(lyrics) > 4096:
            raise HTTPException(status_code=400, detail="Lyrics must be 4096 characters or fewer")
        if not 10 <= duration <= 480:
            raise HTTPException(status_code=400, detail="Duration must be between 10 and 480 seconds")

        job_id = uuid.uuid4().hex
        job_store[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "stage": "Waiting for GPU worker",
            "prompt": prompt,
            "duration_seconds": duration,
            "created_at": __import__("time").time(),
        }

        try:
            generate_track.spawn(job_id, prompt, lyrics, duration)
        except Exception as exc:
            job_store[job_id] = {
                **job_store.get(job_id, {}),
                "status": "failed",
                "stage": "Could not schedule GPU worker",
                "error": str(exc),
            }
            raise HTTPException(status_code=503, detail=f"Could not schedule GPU worker: {exc}")

        return {"job_id": job_id, "status": "queued"}

    @web.get("/api/status/{job_id}")
    def get_status(job_id: str, x_portal_password: str | None = Header(default=None)):
        verify_password(x_portal_password)
        record = job_store.get(job_id)
        if not record:
            raise HTTPException(status_code=404, detail="Job not found or expired")

        response = {
            "job_id": job_id,
            "status": record.get("status", "queued"),
            "stage": record.get("stage", "Waiting for GPU worker"),
            "error": record.get("error"),
        }
        if record.get("status") == "complete":
            response["download_token"] = record.get("download_token")
            response["filename"] = record.get("filename")
        return response

    @web.get("/api/download/{job_id}")
    def download_audio(job_id: str, token: str = Query(...)):
        record = job_store.get(job_id)
        if not record or record.get("status") != "complete":
            raise HTTPException(status_code=404, detail="Completed audio not found")
        expected_token = str(record.get("download_token", ""))
        if not expected_token or not hmac.compare_digest(token, expected_token):
            raise HTTPException(status_code=403, detail="Invalid download link")

        relative_path = str(record.get("file", ""))
        root = os.path.abspath(AUDIO_DIR)
        file_path = os.path.abspath(os.path.join(root, relative_path))
        if os.path.commonpath([root, file_path]) != root:
            raise HTTPException(status_code=403, detail="Invalid audio path")

        # The GPU worker commits the file before marking the job complete.
        audio_volume.reload()
        if not os.path.isfile(file_path):
            raise HTTPException(status_code=404, detail="Audio file is not available yet")
        return FileResponse(
            file_path,
            media_type="audio/mpeg",
            filename=record.get("filename") or "ace-step-generated.mp3",
        )

    return web
