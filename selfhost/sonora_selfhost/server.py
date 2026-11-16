"""Loopback-only studio. Provider credentials remain on the Python side."""
from __future__ import annotations
from . import __version__
import hmac
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .provider import ProviderError
from .store import BusyError, Worker

STATIC = Path(__file__).parent / 'static'

class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_key: str = Field(min_length=32, max_length=36)
    prompt: str = Field(min_length=1, max_length=4000)
    lyrics: str = Field(default='', max_length=10000)
    duration: StrictInt = Field(default=30, ge=10, le=120)
    model: str = Field(min_length=1, max_length=200)
    confirm_cost: StrictBool = False


def create_app(store, provider, *, token=None, run_worker=True):
    token = token or secrets.token_urlsafe(32)
    worker = Worker(store, provider)

    @asynccontextmanager
    async def lifespan(app):
        if run_worker:
            worker.start()
        yield
        if run_worker:
            worker.close()
        provider.close()

    app = FastAPI(title='Sonora - Your studio', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.session_token = token
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]'])

    @app.middleware('http')
    async def protect(request: Request, call_next):
        if request.method not in {'GET', 'HEAD'}:
            origin = request.headers.get('origin')
            if origin and origin != str(request.base_url).rstrip('/'):
                return JSONResponse({'detail': 'Cross-origin requests are not allowed.'}, status_code=403)
            if request.headers.get('sec-fetch-site') in {'cross-site'}:
                return JSONResponse({'detail': 'Cross-site requests are not allowed.'}, status_code=403)
        if request.url.path.startswith('/api/') and request.url.path != '/api/session':
            session = request.cookies.get('sonora_session', '')
            if not hmac.compare_digest(session, token):
                return JSONResponse({'detail': 'Open the private studio URL printed by sonora start.'}, status_code=401)
        response = await call_next(request)
        response.headers.update({
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        })
        return response

    @app.post('/api/session')
    def session(request: Request):
        if not hmac.compare_digest(request.headers.get('x-sonora-session', ''), token):
            raise HTTPException(401, 'Invalid local session.')
        response = JSONResponse({'ok': True})
        response.set_cookie('sonora_session', token, httponly=True, samesite='strict', secure=False)
        return response

    @app.get('/')
    def index():
        return FileResponse(STATIC / 'index.html', media_type='text/html')

    @app.get('/style.css')
    def style():
        return FileResponse(STATIC / 'style.css', media_type='text/css')

    @app.get('/app.js')
    def script():
        return FileResponse(STATIC / 'app.js', media_type='application/javascript')

    @app.get('/api/status')
    def status():
        return {**provider.config.public(), 'version': __version__,
                'format': 'WAV', 'duration_min': 10, 'duration_max': 120,
                'cancellation_supported': False, 'gpu_verified': False}

    @app.get('/api/models')
    def models():
        try:
            return {'models': provider.models(), 'selected': provider.config.model}
        except ProviderError as exc:
            raise HTTPException(503, str(exc)) from None

    @app.get('/api/jobs')
    def jobs():
        # Deliberate allowlist: never expose future provider credentials/headers/paths.
        fields = {'id','status','created','updated','prompt','lyrics','duration','model',
                  'remote_id','error','actual_duration'}
        return {'jobs': [{k: v for k, v in j.items() if k in fields} for j in store.all()]}

    @app.post('/api/generate', status_code=202)
    def generate(body: GenerateRequest):
        if not body.confirm_cost:
            raise HTTPException(400, 'Confirm that this uses your own hardware or provider balance.')
        try:
            uuid.UUID(body.request_key)
        except ValueError:
            raise HTTPException(422, 'Invalid request key.') from None
        try:
            job_id = store.create(body.request_key, body.prompt, body.lyrics, body.duration,
                                  body.model, provider.config.endpoint)
        except BusyError as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {'job_id': job_id}

    @app.get('/api/audio/{job_id}')
    def audio(job_id: str):
        job = store.get(job_id)
        if not job or job['status'] != 'complete':
            raise HTTPException(404, 'Track not ready.')
        try:
            path = store.audio_path(job_id)
        except ValueError:
            raise HTTPException(404, 'Track not found.') from None
        if not path.is_file():
            raise HTTPException(404, 'Saved audio is missing. Check your data directory.')
        return FileResponse(path, media_type='audio/wav', filename=f'sonora-{job_id[:8]}.wav', content_disposition_type='inline')

    return app
