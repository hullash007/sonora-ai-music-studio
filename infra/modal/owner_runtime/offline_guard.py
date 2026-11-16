"""Fail closed for Python model-download routes in the isolated child only."""
import ipaddress
import sys

ATTEMPTS=[]

def assert_no_download_attempts():
    if ATTEMPTS:raise OfflineViolation('Offline downloader/network route was entered')

class OfflineViolation(RuntimeError):
    pass

def reject_download(*args, **kwargs):
    ATTEMPTS.append('download')
    raise OfflineViolation('Download requested during offline generation')

def audit_network(event, args):
    if event not in ('socket.connect', 'socket.getaddrinfo', 'socket.sendto'):
        return
    if event == 'socket.getaddrinfo':
        host = args[0]
    else:
        address = args[1] if len(args) > 1 else None
        if isinstance(address, str):
            return  # Unix-domain sockets for local runtime IPC.
        if not isinstance(address, tuple) or not address:
            raise OfflineViolation('Unknown network destination in offline generation')
        host = address[0]
    if host in ('localhost', b'localhost', None):
        return
    if isinstance(host, bytes):
        host=host.decode('ascii')
    try:
        allowed = ipaddress.ip_address(host).is_loopback
    except ValueError:
        allowed = False
    if not allowed:
        ATTEMPTS.append('external-network')
        raise OfflineViolation('External network requested during offline generation')

class RejectModelScope:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'modelscope' or fullname.startswith('modelscope.'):
            ATTEMPTS.append('modelscope-import')
            raise OfflineViolation('ModelScope disabled in offline generation')
        return None

def install():
    import os
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_DATASETS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1')
    sys.addaudithook(audit_network)
    sys.meta_path.insert(0, RejectModelScope())
    import huggingface_hub
    huggingface_hub.snapshot_download=reject_download
    huggingface_hub.hf_hub_download=reject_download
    # Import before any ACE loader: all aliases imported later see the refusal.
    from huggingface_hub import file_download, _snapshot_download
    file_download.hf_hub_download=reject_download
    _snapshot_download.snapshot_download=reject_download
