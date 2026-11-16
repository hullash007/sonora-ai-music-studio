"""Verified Google identity at the HTTP edge."""
from contextvars import ContextVar
principal = ContextVar("sonora_principal", default=None)
current_identity = ContextVar("sonora_identity", default=None)
import base64
import hashlib
import hmac
import json
import secrets
import time
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse


def install_google_auth(app, *, client_id, origin, owner_email, password, store, verifier=None, clock=time.time, on_identity=None):
    if not client_id or not origin.startswith('https://') or not owner_email.endswith('@gmail.com') or not password:
        raise ValueError('Google auth must be fully configured')
    cookie_name = '__Host-sonora_session'
    nonce_cookie = '__Host-sonora_login'
    signing_key = hmac.digest(password.encode(), b'sonora-google-auth-v1', 'sha256')
    if verifier is None:
        from google.oauth2 import id_token
        from google.auth.transport.requests import Request as GoogleRequest
        transport = GoogleRequest()
        def verifier(token):
            return id_token.verify_oauth2_token(token, transport, client_id)

    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def signed(value):
        raw = base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).decode()
        return raw + '.' + hmac.new(signing_key, raw.encode(), hashlib.sha256).hexdigest()

    def unsigned(value):
        try:
            raw, signature = value.split('.')
            if not hmac.compare_digest(signature, hmac.new(signing_key, raw.encode(), hashlib.sha256).hexdigest()):
                return None
            obj = json.loads(base64.urlsafe_b64decode(raw))
            return obj if obj['exp'] > clock() else None
        except (ValueError, KeyError, TypeError):
            return None

    def session(request):
        token = request.cookies.get(cookie_name, '')
        if not token or len(token) > 100:
            return None
        record = store.get('session:' + digest(token))
        return record if record and record.get('expires_at', 0) > clock() else None

    def same_origin(request):
        if request.headers.get('origin') != origin:
            raise HTTPException(403, 'Request origin not allowed')

    @app.middleware('http')
    async def authorize(request, call_next):
        path = request.url.path
        # Never accept the legacy shared credential from clients in Google mode.
        request.scope['headers'] = [(k, v) for k, v in request.scope['headers'] if k.lower() != b'x-portal-password']
        if path.startswith('/api/') and path not in {'/api/health', '/api/auth/config', '/api/auth/google', '/api/auth/me', '/api/auth/logout'}:
            identity = session(request)
            if not identity:
                return JSONResponse({'detail': 'Sign in with Google'}, status_code=401)
            principal.set(identity['sub'])
            current_identity.set(identity)
            if request.method not in {'GET', 'HEAD'} and request.headers.get('origin') != origin:
                return JSONResponse({'detail': 'Request origin not allowed'}, status_code=403)
            request.scope['headers'].append((b'x-portal-password', password.encode()))
        response = await call_next(request)
        if path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Cross-Origin-Opener-Policy'] = 'same-origin-allow-popups'
        return response

    @app.get('/api/auth/config')
    def config():
        challenge = {'nonce': secrets.token_urlsafe(32), 'exp': clock() + 300}
        response = JSONResponse({'mode': 'google', 'client_id': client_id, 'nonce': challenge['nonce']})
        response.set_cookie(nonce_cookie, signed(challenge), secure=True, httponly=True, samesite='strict', max_age=300, path='/')
        return response

    @app.post('/api/auth/google')
    async def login(request: Request):
        same_origin(request)
        if int(request.headers.get('content-length', '0')) > 18000:
            raise HTTPException(400, 'Invalid sign-in request')
        challenge = unsigned(request.cookies.get(nonce_cookie, ''))
        if not challenge:
            raise HTTPException(401, 'Sign-in expired. Reload and try again.')
        try:
            body = await request.json()
            token = body.get('credential')
            if not isinstance(token, str) or not 1 <= len(token) <= 16000:
                raise ValueError('Invalid token')
            claims = verifier(token)
            # Library verifies signature/expiry; repeat security-relevant claims for injected verifier tests.
            if (claims.get('aud') != client_id or claims.get('iss') not in {'accounts.google.com', 'https://accounts.google.com'}
                or claims.get('exp', 0) <= clock() or not claims.get('sub') or claims.get('email_verified') is not True
                or not hmac.compare_digest(str(claims.get('nonce', '')), challenge['nonce'])):
                raise ValueError('Invalid claims')
        except Exception:
            raise HTTPException(401, 'Google sign-in could not be verified')
        if not store.put('login-used:' + digest(challenge['nonce']), {'expires_at': challenge['exp']}, skip_if_exists=True):
            raise HTTPException(401, 'This sign-in was already used')
        role = 'visitor'
        email = claims.get('email', '')
        if email.casefold() == owner_email.casefold():
            store.put('owner-sub', {'sub': claims['sub']}, skip_if_exists=True)
            binding = store.get('owner-sub')
            if binding and binding.get('sub') == claims['sub']:
                role = 'owner'
        token = secrets.token_urlsafe(32)
        identity = {'sub': claims['sub'], 'email': email, 'role': role, 'expires_at': clock() + 28800}
        if on_identity:on_identity(identity)
        store.put('session:' + digest(token), identity)
        response = JSONResponse({'authenticated': True, 'workspace': digest(claims['sub']), 'email': email, 'role': role})
        response.set_cookie(cookie_name, token, secure=True, httponly=True, samesite='lax', max_age=28800, path='/')
        response.delete_cookie(nonce_cookie, path='/', secure=True, httponly=True, samesite='strict')
        return response

    @app.get('/api/auth/me')
    def me(request: Request):
        identity = session(request)
        return {'authenticated': bool(identity), 'workspace': digest(identity['sub']) if identity else None, 'email': identity.get('email') if identity else None, 'role': identity.get('role') if identity else None}

    @app.post('/api/auth/logout')
    def logout(request: Request):
        same_origin(request)
        token = request.cookies.get(cookie_name, '')
        if token:
            store.put('session:' + digest(token), {'expires_at': 0})
        response = JSONResponse({'authenticated': False})
        response.delete_cookie(cookie_name, path='/', secure=True, httponly=True, samesite='lax')
        return response
