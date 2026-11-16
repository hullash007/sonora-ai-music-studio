"""Local configuration. Keys never enter browser responses or CLI arguments."""
from __future__ import annotations
import ipaddress
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

class ConfigError(ValueError):
    pass


def data_home() -> Path:
    if os.environ.get('SONORA_HOME'):
        return Path(os.environ['SONORA_HOME']).expanduser().resolve()
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'Sonora'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'sonora'


def private_dir(root: Path) -> Path:
    if root.is_symlink():
        raise ConfigError('Sonora data directory must not be a symbolic link.')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != 'nt':
        root.chmod(0o700)
    return root


def atomic_json(path: Path, value: dict) -> None:
    private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(prefix='.sonora-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def endpoint_url(value: str) -> str:
    try:
        u = urlsplit(value)
        port = u.port
    except ValueError:
        raise ConfigError('Invalid endpoint URL.') from None
    if (u.scheme not in {'http', 'https'} or not u.hostname or u.username or u.password
            or u.query or u.fragment or u.path not in {'', '/'} or any(c.isspace() for c in value)):
        raise ConfigError('Use the server origin only, without a path, key, query or user information.')
    try:
        local = ipaddress.ip_address(u.hostname).is_loopback
    except ValueError:
        local = u.hostname == 'localhost'
    if u.scheme != 'https' and not local:
        raise ConfigError('Remote servers require HTTPS. Only loopback HTTP is allowed.')
    if port is not None and not 1 <= port <= 65535:
        raise ConfigError('Invalid endpoint port.')
    return value.rstrip('/')


def validate_key(value: str) -> str:
    if len(value) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ConfigError('API key must contain printable ASCII with no spaces or newlines.')
    return value


@dataclass(frozen=True)
class Config:
    endpoint: str
    model: str
    credential: str | None = None

    def __post_init__(self):
        object.__setattr__(self, 'endpoint', endpoint_url(self.endpoint))
        if not isinstance(self.model, str) or not self.model or len(self.model) > 200:
            raise ConfigError('Choose an available model.')
        if self.credential is not None and (len(self.credential) != 32 or not all(c in '0123456789abcdef' for c in self.credential)):
            raise ConfigError('Invalid credential reference.')

    def public(self):
        return {'provider': 'ACE-Step REST', 'endpoint': self.endpoint, 'model': self.model}


def load_config(root: Path) -> Config:
    try:
        obj = json.loads((root / 'config.json').read_text(encoding='utf-8'))
        if obj.get('version') != 1 or obj.get('provider') != 'acestep-rest':
            raise ConfigError('Unsupported configuration version/provider.')
        return Config(obj['endpoint'], obj['model'], obj.get('credential'))
    except (OSError, ValueError, KeyError, TypeError):
        raise ConfigError('No valid configuration. Run sonora setup.') from None


def read_key(root: Path, config: Config) -> str:
    if 'SONORA_API_KEY' in os.environ:
        return validate_key(os.environ['SONORA_API_KEY'])
    if not config.credential:
        return ''
    try:
        return validate_key(json.loads((root / f'credential-{config.credential}.json').read_text())['api_key'])
    except (OSError, ValueError, KeyError, TypeError):
        raise ConfigError('Saved credential is unavailable. Run sonora setup or set SONORA_API_KEY locally.') from None


def save_config(root: Path, config: Config, key: str | None) -> None:
    """Only call after endpoint/model validation. Active config is the atomic commit."""
    import uuid
    reference = uuid.uuid4().hex if key else None
    credential_path = root / f'credential-{reference}.json'
    if key:
        atomic_json(credential_path, {'api_key': validate_key(key)})
    try:
        atomic_json(root / 'config.json', {'version': 1, 'provider': 'acestep-rest',
                    'endpoint': config.endpoint, 'model': config.model, 'credential': reference})
    except BaseException:
        if key:
            credential_path.unlink(missing_ok=True)
        raise
    # Orphan credentials are removed only after the new config is durably active.
    for old in root.glob('credential-*.json'):
        if old != credential_path:
            old.unlink(missing_ok=True)
