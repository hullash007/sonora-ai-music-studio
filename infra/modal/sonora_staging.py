"""SONORA isolated staging app: CPU portal plus on-demand ACE-Step GPU worker.

Do not deploy this file without explicit approval. All Modal resource names are
staging-specific; this app does not mount or read production storage or secrets.
"""

from __future__ import annotations

import hmac
import os
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

import modal

APP_NAME = "sonora-staging"
PROJECT_DIR = "/opt/ACE-Step-1.5"
CHECKPOINT_DIR = f"{PROJECT_DIR}/checkpoints"
AUDIO_DIR = "/mnt/sonora-staging-audio"
BACKEND_DIR = "/opt/sonora/backend"
ACE_STEP_COMMIT = "ca1e85fe9430179831e6bc6be790c332190a3866"

app = modal.App(APP_NAME)

# Isolated resources: these names are intentionally distinct from production.
checkpoints_volume = modal.Volume.from_name(
    "sonora-staging-checkpoints", create_if_missing=True
)
audio_volume = modal.Volume.from_name("sonora-staging-audio", create_if_missing=True)
job_store = modal.Dict.from_name("sonora-staging-jobs", create_if_missing=True)

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_BACKEND = str(REPO_ROOT / "backend")

portal_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi")
    .add_local_dir(LOCAL_BACKEND, remote_path=BACKEND_DIR)
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
    .add_local_dir(LOCAL_BACKEND, remote_path=BACKEND_DIR)
)


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
    """Generate one track and persist audio plus a durable library record."""
    sys.path.insert(0, BACKEND_DIR)
    from sonora.library import write_track_record

    def update_job(**updates: Any) -> None:
        current = job_store.get(job_id, {"job_id": job_id})
        current.update(updates)
        job_store[job_id] = current

    try:
        update_job(status="running", stage="Starting staging GPU worker", error=None)
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
        from acestep.inference import GenerationConfig, GenerationParams, generate_music
        from acestep.api.model_download import ensure_model_downloaded

        ensure_model_downloaded("acestep-5Hz-lm-1.7B", CHECKPOINT_DIR)
        dit_handler = AceStepHandler()
        dit_message, dit_ok = dit_handler.initialize_service(
            project_root=PROJECT_DIR,
            config_path="acestep-v15-turbo",
            device="cuda",
            use_flash_attention=True,
            compile_model=False,
            offload_to_cpu=False,
            offload_dit_to_cpu=False,
        )
        if not dit_ok:
            raise RuntimeError(f"ACE-Step DiT initialization failed: {dit_message}")

        update_job(stage="Loading ACE-Step language model")
        llm_handler = LLMHandler()
        llm_message, llm_ok = llm_handler.initialize(
            checkpoint_dir=CHECKPOINT_DIR,
            lm_model_path="acestep-5Hz-lm-1.7B",
            backend="vllm",
            device="cuda",
            offload_to_cpu=False,
            dtype=None,
        )
        if not llm_ok:
            raise RuntimeError(f"ACE-Step language-model initialization failed: {llm_message}")

        update_job(stage="Generating music")
        job_dir = os.path.join(AUDIO_DIR, "jobs", job_id)
        os.makedirs(job_dir, exist_ok=True)
        params = GenerationParams(
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
        config = GenerationConfig(
            batch_size=1,
            allow_lm_batch=False,
            use_random_seed=True,
            audio_format="mp3",
            mp3_bitrate="128k",
            mp3_sample_rate=48000,
        )
        result = generate_music(dit_handler, llm_handler, params, config, save_dir=job_dir)
        if not result.success:
            raise RuntimeError(result.error or result.status_message or "Music generation failed")
        if not result.audios:
            raise RuntimeError("ACE-Step completed without returning an audio file")

        audio_path = result.audios[0].get("path")
        if not audio_path or not os.path.isfile(audio_path):
            raise RuntimeError(f"Generated audio file was not found: {audio_path}")
        audio_abs = os.path.abspath(audio_path)
        job_root = os.path.abspath(job_dir)
        if os.path.commonpath([audio_abs, job_root]) != job_root:
            raise RuntimeError("Generated audio path is outside the job output directory")

        relative_audio_path = os.path.relpath(audio_abs, AUDIO_DIR).replace("\\", "/")
        initial = job_store.get(job_id, {})
        created_at = float(initial.get("created_at", time.time()))
        completed_at = time.time()
        title = " ".join(prompt.split())
        if len(title) > 72:
            title = title[:69].rstrip() + "..."

        library_record = {
            "job_id": job_id,
            "title": title or "Untitled track",
            "status": "complete",
            "prompt": prompt,
            "lyrics": lyrics,
            "filename": os.path.basename(audio_abs),
            "file": relative_audio_path,
            "duration_seconds": duration,
            "created_at": created_at,
            "completed_at": completed_at,
            "engine": "ACE-Step 1.5",
            "model_revision": ACE_STEP_COMMIT,
        }
        write_track_record(AUDIO_DIR, library_record)
        audio_volume.commit()
        update_job(
            status="complete",
            stage="Music generation complete",
            file=relative_audio_path,
            filename=os.path.basename(audio_abs),
            title=title or "Untitled track",
            duration_seconds=duration,
            completed_at=completed_at,
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


HTML_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SONORA — AI Music Studio</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#10131a;color:#edf1f7;font:16px/1.5 system-ui,Segoe UI,sans-serif}
main{max-width:900px;margin:36px auto;padding:28px;background:#1a202b;border:1px solid #303b4d;border-radius:18px}
h1{margin:0 0 6px;font-size:30px}.eyebrow{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:#7dd3fc;font-weight:800}
.sub,.hint,.meta{color:#aeb9c9}.sub{margin:0 0 24px}.hint,.meta{font-size:13px}.field{margin:16px 0}label{display:block;margin-bottom:6px;font-weight:650}
input,textarea{width:100%;padding:12px 14px;border:1px solid #465369;border-radius:9px;background:#111722;color:#f3f6fb;font:inherit}textarea{min-height:115px;resize:vertical}input[type=number]{max-width:160px}
button{padding:10px 16px;border:0;border-radius:9px;background:#7dd3fc;color:#082032;font-weight:750;cursor:pointer}button:disabled{opacity:.55;cursor:wait}
.secondary{background:#293548;color:#eaf2fb}.panel,.track{margin-top:14px;padding:16px;border:1px solid #3c485b;border-radius:10px;background:#131a25}
.library{margin-top:30px}.library-head,.actions{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}.track-title{font-weight:750}.actions{justify-content:flex-start;margin-top:10px}.actions button{font-size:13px}.audio{width:100%;margin-top:14px}.error{color:#fca5a5}.hidden{display:none}a{color:#7dd3fc}
@media(max-width:640px){main{margin:10px;padding:18px}}
</style>
</head><body><main>
<div class="eyebrow">SONORA · ISOLATED STAGING</div><h1>Create music from an idea.</h1>
<p class="sub">Phase 1 library test environment. Tracks created here are stored separately from production.</p>
<form id="musicForm">
<div class="field"><label for="access">Staging access password</label><input id="access" type="password" autocomplete="current-password" required></div>
<div class="field"><label for="prompt">Music prompt</label><textarea id="prompt" maxlength="512" required placeholder="Describe genre, mood, instruments, tempo, arrangement and production style."></textarea><div class="hint">Maximum 512 characters.</div></div>
<div class="field"><label for="lyrics">Lyrics (optional)</label><textarea id="lyrics" maxlength="4096" placeholder="Leave blank for an instrumental track."></textarea></div>
<div class="field"><label for="duration">Track duration (seconds)</label><input id="duration" type="number" min="10" max="480" value="30" required></div>
<button id="generate" type="submit">Generate test track</button>
</form>
<section id="result" class="panel hidden" aria-live="polite"><div id="status">Ready.</div><div id="job" class="hint"></div></section>
<section class="library"><div class="library-head"><div><h2>Your staging library</h2><div class="hint">Completed tracks saved in staging storage.</div></div><button id="refresh" class="secondary" type="button">Refresh library</button></div>
<div id="libraryStatus" class="hint" style="margin-top:12px">Enter your staging password and refresh.</div><div id="tracks"></div><audio id="player" class="audio hidden" controls></audio></section>
</main><script>
const form=document.getElementById('musicForm'),generate=document.getElementById('generate');
const result=document.getElementById('result'),statusEl=document.getElementById('status'),jobEl=document.getElementById('job');
const libraryStatus=document.getElementById('libraryStatus'),tracksEl=document.getElementById('tracks'),player=document.getElementById('player');
let pollHandle=null,playerUrl=null;
function password(){return document.getElementById('access').value}
async function api(url,options={}){const headers={...(options.headers||{}),'X-Portal-Password':password()};const response=await fetch(url,{...options,headers});if(!response.ok){let detail='Request failed';try{detail=(await response.json()).detail||detail}catch(_){}throw new Error(detail)}return response}
form.addEventListener('submit',async event=>{event.preventDefault();if(pollHandle)clearInterval(pollHandle);generate.disabled=true;result.classList.remove('hidden');statusEl.textContent='Submitting staging generation job…';jobEl.textContent='';try{
 const response=await api('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:document.getElementById('prompt').value.trim(),lyrics:document.getElementById('lyrics').value,duration:Number(document.getElementById('duration').value)})});
 const data=await response.json();jobEl.textContent='Job ID: '+data.job_id;statusEl.textContent='Queued. Staging GPU worker will start for this job.';
 pollHandle=setInterval(()=>poll(data.job_id),5000);await poll(data.job_id);
 }catch(error){statusEl.textContent=error.message;statusEl.className='error';generate.disabled=false}
});
async function poll(id){try{const response=await api('/api/status/'+encodeURIComponent(id));const data=await response.json();statusEl.textContent=data.stage||data.status;if(data.status==='complete'){clearInterval(pollHandle);pollHandle=null;statusEl.textContent='Track saved to your staging library.';generate.disabled=false;await loadLibrary()}else if(data.status==='failed'){clearInterval(pollHandle);pollHandle=null;statusEl.textContent=data.error||'Generation failed';statusEl.className='error';generate.disabled=false}}catch(error){statusEl.textContent='Status check: '+error.message}}
document.getElementById('refresh').addEventListener('click',loadLibrary);
async function loadLibrary(){if(!password()){libraryStatus.textContent='Enter your staging password first.';return}libraryStatus.textContent='Loading…';tracksEl.replaceChildren();try{const response=await api('/api/library');const data=await response.json();libraryStatus.textContent=data.count+' saved track(s)';if(!data.tracks.length)libraryStatus.textContent='No tracks yet. Generate a test track to populate this staging library.';for(const track of data.tracks){const card=document.createElement('article');card.className='track';const title=document.createElement('div');title.className='track-title';title.textContent=track.title||'Untitled track';const meta=document.createElement('div');meta.className='meta';meta.textContent=(track.duration_seconds??'—')+' sec · '+(track.created_at?new Date(track.created_at*1000).toLocaleString():'Date unavailable')+' · '+(track.engine||'Music engine');const prompt=document.createElement('div');prompt.className='hint';prompt.textContent=track.prompt||'';const actions=document.createElement('div');actions.className='actions';const play=document.createElement('button');play.type='button';play.textContent='Play';play.addEventListener('click',()=>playTrack(track.job_id));const download=document.createElement('button');download.type='button';download.className='secondary';download.textContent='Download MP3';download.addEventListener('click',()=>downloadTrack(track.job_id,track.filename));actions.append(play,download);card.append(title,meta,prompt,actions);tracksEl.append(card)}}catch(error){libraryStatus.textContent=error.message}}
async function fetchAudio(id){const response=await api('/api/library/'+encodeURIComponent(id)+'/audio');return response.blob()}
async function playTrack(id){try{const blob=await fetchAudio(id);if(playerUrl)URL.revokeObjectURL(playerUrl);playerUrl=URL.createObjectURL(blob);player.src=playerUrl;player.classList.remove('hidden');await player.play()}catch(error){libraryStatus.textContent=error.message}}
async function downloadTrack(id,filename){try{const blob=await fetchAudio(id),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=filename||'sonora-track.mp3';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(error){libraryStatus.textContent=error.message}}
</script></body></html>"""


@app.function(
    image=portal_image,
    cpu=1,
    memory=1024,
    min_containers=0,
    max_containers=1,
    scaledown_window=60,
    volumes={AUDIO_DIR: audio_volume},
    secrets=[modal.Secret.from_name("sonora-staging-portal")],
)
@modal.asgi_app()
def music_portal():
    sys.path.insert(0, BACKEND_DIR)
    from fastapi import FastAPI, Header, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse
    from sonora.library import list_track_records, read_track_record, resolve_audio_file

    web = FastAPI(title="SONORA Staging")

    def verify_password(supplied: str | None) -> None:
        expected = os.environ.get("PORTAL_PASSWORD", "")
        if not expected:
            raise HTTPException(status_code=503, detail="Staging password is not configured")
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="Incorrect staging password")

    @web.get("/", response_class=HTMLResponse)
    def home():
        return HTML_PAGE

    @web.get("/api/health")
    def health():
        return {"status": "ok", "service": "SONORA isolated staging portal"}

    @web.post("/api/generate")
    def submit_generation(payload: dict, x_portal_password: str | None = Header(default=None)):
        verify_password(x_portal_password)
        prompt = str(payload.get("prompt", "")).strip()
        lyrics = str(payload.get("lyrics", ""))
        try:
            duration = int(payload.get("duration", 30))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Duration must be a whole number") from exc
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
            "stage": "Waiting for staging GPU worker",
            "prompt": prompt,
            "lyrics": lyrics,
            "duration_seconds": duration,
            "created_at": time.time(),
        }
        try:
            generate_track.spawn(job_id, prompt, lyrics, duration)
        except Exception as exc:
            current = job_store.get(job_id, {})
            current.update(status="failed", stage="Could not schedule worker", error=str(exc))
            job_store[job_id] = current
            raise HTTPException(status_code=503, detail="Could not schedule staging worker") from exc
        return {"job_id": job_id, "status": "queued"}

    @web.get("/api/status/{job_id}")
    def status(job_id: str, x_portal_password: str | None = Header(default=None)):
        verify_password(x_portal_password)
        try:
            canonical_id = str(uuid.UUID(job_id))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        record = job_store.get(canonical_id.replace("-", "")) or job_store.get(canonical_id)
        if not record:
            raise HTTPException(status_code=404, detail="Job not found")
        return {
            "job_id": record.get("job_id"),
            "status": record.get("status", "queued"),
            "stage": record.get("stage", "Waiting for worker"),
            "error": record.get("error"),
        }

    @web.get("/api/library")
    def library(x_portal_password: str | None = Header(default=None)):
        verify_password(x_portal_password)
        audio_volume.reload()
        records = list_track_records(AUDIO_DIR, limit=200)
        tracks = [
            {
                "job_id": record["job_id"],
                "title": record.get("title") or "Untitled track",
                "prompt": record.get("prompt", ""),
                "filename": record.get("filename") or "sonora-track.mp3",
                "duration_seconds": record.get("duration_seconds"),
                "created_at": record.get("created_at"),
                "engine": record.get("engine", "ACE-Step 1.5"),
            }
            for record in records
        ]
        return {"tracks": tracks, "count": len(tracks)}

    @web.get("/api/library/{job_id}/audio")
    def library_audio(job_id: str, x_portal_password: str | None = Header(default=None)):
        verify_password(x_portal_password)
        audio_volume.reload()
        record = read_track_record(AUDIO_DIR, job_id)
        if not record:
            raise HTTPException(status_code=404, detail="Track not found in staging library")
        try:
            audio_path = resolve_audio_file(AUDIO_DIR, record["file"])
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="Track audio is unavailable") from exc
        return FileResponse(
            str(audio_path),
            media_type="audio/mpeg",
            filename=record.get("filename") or "sonora-track.mp3",
        )

    return web
