"""Generate Obsidian capability map from A2A agent cards. Hash-gated to avoid sync churn."""
import hashlib, json, os, re, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path

PORT_MAP = os.environ.get("PORT_MAP_PATH", "/Users/alexcai/code/hermes-a2a/s6m-config/port-map.md")
OUTPUT = Path(os.environ.get(
    "DOCS_OUTPUT",
    os.path.expanduser("~/Documents/Obsidian/AlexCai/20-Areas/10_AI实践/三省六部_Hermes/三省六部能力图谱.md"),
))
STATE = Path(os.path.expanduser("~/.hermes/.docs-gen-state.json"))
_PORT_RE = re.compile(r"^- \*\*([a-z_]+)\*\*.*端口 `(\d+)`")


def load_port_map() -> dict[str, int]:
    routes = {}
    with open(PORT_MAP) as f:
        for line in f:
            m = _PORT_RE.match(line)
            if m:
                routes[m.group(1)] = int(m.group(2))
    return routes


def fetch_card(profile: str, port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/a2a/.well-known/agent-card.json", timeout=2) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


def render(cards: dict[str, dict], offline: list[str]) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    total_skills = sum(len(c.get("skills", [])) for c in cards.values())
    lines = [
        "# 三省六部能力图谱",
        "",
        f"> Auto-generated at {now}. Online: {len(cards)} / Offline: {len(offline)}. Total skills: {total_skills}.",
        "",
        "## 概览",
        f"- 在线 profile：{len(cards)}",
        f"- 离线 profile：{len(offline)}",
        f"- 总 skill 数：{total_skills}",
        "",
        "## Profile 列表",
        "",
    ]
    for profile in sorted(cards.keys()):
        card = cards[profile]
        url = card.get("url", "")
        port = url.rsplit(":", 1)[-1].split("/", 1)[0] if url else "?"
        model = card.get("currentModel", {}) or {}
        skills = card.get("skills", [])
        skill_ids = ", ".join(s.get("id", "?") for s in skills)
        lines += [
            f"### {profile} — port {port}",
            f"- name: {card.get('name', '?')}",
            f"- description: {(card.get('description') or '')[:120]}",
            f"- model: {model.get('default', '?')} / {model.get('provider', '?')}",
            f"- skills ({len(skills)}): {skill_ids}",
            "",
        ]
    if offline:
        lines += ["## 离线 Profile", ""]
        for p in sorted(offline):
            lines.append(f"- {p} — 未响应 agent-card 端点")
    return "\n".join(lines) + "\n"


def main() -> int:
    cards = {}
    offline = []
    for profile, port in load_port_map().items():
        c = fetch_card(profile, port)
        if c:
            cards[profile] = c
        else:
            offline.append(profile)
    body = render(cards, offline)
    h = hashlib.sha256(body.encode()).hexdigest()[:12]
    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text())
        except (OSError, json.JSONDecodeError):
            state = {}
    if state.get("hash") == h and OUTPUT.exists():
        print(f"docs_generator: hash {h} unchanged; skip write")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(body)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"hash": h, "ts": time.time(), "online": len(cards), "offline": offline}, ensure_ascii=False))
    print(f"docs_generator: wrote {OUTPUT} (hash {h}, online {len(cards)}, offline {len(offline)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
