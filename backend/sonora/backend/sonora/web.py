"""SONORA HTTP application factory.

This module is deliberately independent of Modal. Production staging and local
browser tests provide their own job store, generation callback and audio
storage dependencies.
"""

from __future__ import annotations

import hmac
import mimetypes
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from sonora.library import (
    list_track_records,
    read_track_record,
    resolve_audio_file,
)


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
async function loadLibrary(){if(!password()){libraryStatus.textContent='Enter your staging password first.';return}libraryStatus.textContent='Loading…';tracksEl.replaceChildren();try{const response=await api('/api/library');const data=await response.json();libraryStatus.textContent=data.count+' saved track(s)';if(!data.tracks.length)libraryStatus.textContent='No tracks yet. Generate a test track to populate this staging library.';for(const track of data.tracks){const card=document.createElement('article');card.className='track';const title=document.createElement('div');title.className='track-title';title.textContent=track.title||'Untitled track';const meta=document.createElement('div');meta.className='meta';meta.textContent=(track.duration_seconds??'—')+' sec · '+(track.created_at?new Date(track.created_at*1000).toLocaleString():'Date unavailable')+' · '+(track.engine||'Music engine');const prompt=document.createElement('div');prompt.className='hint';prompt.textContent=track.prompt||'';const actions=document.createElement('div');actions.className='actions';const play=document.createElement('button');play.type='button';play.textContent='Play';play.addEventListener('click',()=>playTrack(track.job_id));const download=document.createElement('button');download.type='button';download.className='secondary';download.textContent='Download audio';download.addEventListener('click',()=>downloadTrack(track.job_id,track.filename));actions.append(play,download);card.append(title,meta,prompt,actions);tracksEl.append(card)}}catch(error){libraryStatus.textContent=error.message}}
async function fetchAudio(id){const response=await api('/api/library/'+encodeURIComponent(id)+'/audio');return response.blob()}
async function playTrack(id){try{const blob=await fetchAudio(id);if(playerUrl)URL.revokeObjectURL(playerUrl);playerUrl=URL.createObjectURL(blob);player.src=playerUrl;player.classList.remove('hidden');await player.play()}catch(error){libraryStatus.textContent=error.message}}
async function downloadTrack(id,filename){try{const blob=await fetchAudio(id),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=filename||'sonora-track.mp3';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(error){libraryStatus.textContent=error.message}}
</script></body></html>"""


def create_app(
    *,
    job_store: Any,
    enqueue_generation: Callable[[str, str, str, int], Any],
    audio_root: str | Path,
    reload_audio: Callable[[], Any],
    password: str,
    model_name: str = "ACE-Step 1.5",
) -> FastAPI:
    """Create the SONORA API using injected storage and generation adapters.

    Args:
        job_store: Mapping-like object supporting get(), assignment and keys.
        enqueue_generation: Called as (job_id, prompt, lyrics, duration).
        audio_root: Root directory for generated audio and library metadata.
        reload_audio: Storage refresh callback; use a no-op for local files.
        password: Required password for authenticated API routes.
        model_name: Engine label shown in the library.

    This factory does not import or initialize Modal.
    """

    root = Path(audio_root)
    app = FastAPI(title="SONORA Music Studio")

    def verify_password(supplied: str | None) -> None:
        if not password:
            raise HTTPException(
                status_code=503,
                detail="Staging portal password is not configured",
            )

        if not supplied or not hmac.compare_digest(supplied, password):
            raise HTTPException(
                status_code=401,
                detail="Incorrect staging studio password",
            )

    def find_job(job_id: str) -> tuple[str, dict[str, Any]] | None:
        """Find a job by canonical UUID, tolerating legacy UUID-hex keys."""
        try:
            canonical_id = str(uuid.UUID(job_id))
        except (ValueError, TypeError, AttributeError):
            return None

        record = job_store.get(canonical_id)
        if record is None:
            record = job_store.get(canonical_id.replace("-", ""))

        if not isinstance(record, dict):
            return None

        return canonical_id, record

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return HTML_PAGE

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "SONORA HTTP API",
        }

    @app.post("/api/generate")
    def submit_generation(
        payload: dict[str, Any],
        x_portal_password: str | None = Header(default=None),
    ) -> dict[str, str]:
        verify_password(x_portal_password)

        prompt_value = payload.get("prompt", "")
        lyrics_value = payload.get("lyrics", "")
        raw_duration = payload.get("duration", 30)

        if not isinstance(prompt_value, str):
            raise HTTPException(status_code=400, detail="Prompt must be text")
        if not isinstance(lyrics_value, str):
            raise HTTPException(status_code=400, detail="Lyrics must be text")

        prompt = prompt_value.strip()
        lyrics = lyrics_value

        if not prompt:
            raise HTTPException(
                status_code=400,
                detail="A music prompt is required",
            )
        if len(prompt) > 512:
            raise HTTPException(
                status_code=400,
                detail="Prompt must be 512 characters or fewer",
            )
        if len(lyrics) > 4096:
            raise HTTPException(
                status_code=400,
                detail="Lyrics must be 4096 characters or fewer",
            )

        # Accept JSON integers only. This rejects booleans, floats and strings.
        if type(raw_duration) is not int:
            raise HTTPException(
                status_code=400,
                detail="Duration must be a whole-number integer",
            )

        duration = raw_duration
        if not 10 <= duration <= 480:
            raise HTTPException(
                status_code=400,
                detail="Duration must be between 10 and 480 seconds",
            )

        job_id = str(uuid.uuid4())
        created_at = time.time()

        job_store[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "stage": "Waiting for generation worker",
            "prompt": prompt,
            "lyrics": lyrics,
            "duration_seconds": duration,
            "created_at": created_at,
        }

        try:
            enqueue_generation(job_id, prompt, lyrics, duration)
        except Exception as exc:
            record = dict(job_store.get(job_id, {}))
            record.update(
                status="failed",
                stage="Could not schedule generation worker",
                error=str(exc),
            )
            job_store[job_id] = record

            raise HTTPException(
                status_code=503,
                detail=f"Could not schedule generation worker: {exc}",
            ) from exc

        return {
            "job_id": job_id,
            "status": "queued",
        }

    @app.get("/api/status/{job_id}")
    def get_status(
        job_id: str,
        x_portal_password: str | None = Header(default=None),
    ) -> dict[str, Any]:
        verify_password(x_portal_password)

        found = find_job(job_id)
        if found is None:
            raise HTTPException(
                status_code=404,
                detail="Generation job not found",
            )

        canonical_id, record = found
        return {
            "job_id": canonical_id,
            "status": record.get("status", "queued"),
            "stage": record.get("stage", "Waiting for generation worker"),
            "error": record.get("error"),
        }

    @app.get("/api/library")
    def get_library(
        x_portal_password: str | None = Header(default=None),
    ) -> dict[str, Any]:
        verify_password(x_portal_password)
        reload_audio()

        try:
            records = list_track_records(root, limit=200)
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=500,
                detail="Could not read the music library",
            ) from exc

        tracks = [
            {
                "job_id": item["job_id"],
                "title": item.get("title", "Untitled track"),
                "prompt": item.get("prompt", ""),
                "duration_seconds": item.get("duration_seconds"),
                "created_at": item.get("created_at"),
                "filename": item.get("filename") or Path(item["file"]).name,
                "engine": item.get("engine", model_name),
                "model_revision": item.get("model_revision"),
            }
            for item in records
        ]

        return {
            "tracks": tracks,
            "count": len(tracks),
        }

    def make_audio_response(
        job_id: str,
        supplied_password: str | None,
    ) -> FileResponse:
        verify_password(supplied_password)
        reload_audio()

        record = read_track_record(root, job_id)
        if record is None:
            raise HTTPException(
                status_code=404,
                detail="Track not found in the music library",
            )

        try:
            file_path = resolve_audio_file(root, record["file"])
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(
                status_code=404,
                detail="Track audio file is unavailable",
            ) from exc

        requested_filename = record.get("filename")
        filename = (
            Path(requested_filename).name
            if isinstance(requested_filename, str) and requested_filename.strip()
            else file_path.name
        )

        media_type, _ = mimetypes.guess_type(filename)
        if not media_type:
            media_type = mimetypes.guess_type(file_path.name)[0] or "audio/mpeg"

        return FileResponse(
            path=file_path,
            media_type=media_type,
            filename=filename,
        )

    # The supplied HTML uses this route. The additional aliases preserve
    # compatibility with the earlier staging page and clients.
    @app.get("/api/library/{job_id}/audio")
    def library_audio(
        job_id: str,
        x_portal_password: str | None = Header(default=None),
    ) -> FileResponse:
        return make_audio_response(job_id, x_portal_password)

    @app.get("/api/audio/{job_id}")
    def stream_audio(
        job_id: str,
        x_portal_password: str | None = Header(default=None),
    ) -> FileResponse:
        return make_audio_response(job_id, x_portal_password)

    @app.get("/api/download/{job_id}")
    def download_audio(
        job_id: str,
        x_portal_password: str | None = Header(default=None),
    ) -> FileResponse:
        return make_audio_response(job_id, x_portal_password)

    return app
