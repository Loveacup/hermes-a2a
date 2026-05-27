"""hermes-a2a plugin — loads on profile startup, starts A2A HTTP server."""
import hashlib, logging, os, sys, subprocess
from pathlib import Path

logger = logging.getLogger("hermes-a2a")
PLUGIN_NAME, PLUGIN_VERSION = "hermes-a2a", "0.1.0"
PORT_BASE, PORT_RANGE = 8650, 50
_server_proc = None

def _stable_port(profile: str) -> int:
    # PYTHONHASHSEED randomization breaks port stability across gateway restarts;
    # sha256 keeps the same `hash(profile) % 50 + 8650` shape but deterministically.
    return PORT_BASE + int(hashlib.sha256(profile.encode()).hexdigest(), 16) % PORT_RANGE

def on_load(ctx):
    cfg = ctx.config or {}
    port = int(cfg.get("port", _stable_port(os.environ.get("HERMES_PROFILE", "default"))))
    host = cfg.get("host", "127.0.0.1")
    global _server_proc
    env = os.environ.copy()
    env.update(A2A_HOST=host, A2A_PORT=str(port), HERMES_HOME=ctx.hermes_home)
    _server_proc = subprocess.Popen([sys.executable, str(Path(__file__).parent/"server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    logger.info(f"[hermes-a2a] v{PLUGIN_VERSION} on http://{host}:{port}")
    return True

def on_unload(ctx):
    global _server_proc
    if _server_proc: _server_proc.terminate()

def on_tool_call(ctx, tool_name, tool_args):
    return None
