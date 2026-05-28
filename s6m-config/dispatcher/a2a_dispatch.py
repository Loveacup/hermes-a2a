"""Read-only A2A task dispatcher. Score profiles by skill coverage + keyword match."""
import json, os, re, sys, time, urllib.request, urllib.error
from pathlib import Path

PORT_MAP = os.environ.get("PORT_MAP_PATH", "/Users/alexcai/code/hermes-a2a/s6m-config/port-map.md")
CACHE_PATH = Path(os.path.expanduser("~/.hermes/.dispatcher-discovery-cache.json"))
CACHE_TTL = 300
_PORT_RE = re.compile(r"^- \*\*([a-z_]+)\*\*.*端口 `(\d+)`")


def load_port_map(path: str = PORT_MAP) -> dict[str, int]:
    routes = {}
    with open(path) as f:
        for line in f:
            m = _PORT_RE.match(line)
            if m:
                routes[m.group(1)] = int(m.group(2))
    return routes


def fetch_card(profile: str, port: int, timeout: float = 2.0) -> dict | None:
    url = f"http://127.0.0.1:{port}/a2a/.well-known/agent-card.json"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


def discover_all(force: bool = False) -> dict[str, dict]:
    if not force and CACHE_PATH.exists():
        try:
            data = json.loads(CACHE_PATH.read_text())
            if time.time() - data.get("ts", 0) < CACHE_TTL:
                return data.get("cards", {})
        except (OSError, json.JSONDecodeError):
            pass
    cards = {}
    for profile, port in load_port_map().items():
        c = fetch_card(profile, port)
        if c:
            cards[profile] = c
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps({"ts": time.time(), "cards": cards}, ensure_ascii=False))
    return cards


_CN_EN_MAP = {
    "部署": ["deploy", "deployment", "release", "launch", "ship"],
    "代码": ["code", "coding", "programming", "developer"],
    "安全": ["security", "audit", "secure", "vulnerability"],
    "审查": ["review", "audit", "inspect", "check"],
    "财务": ["finance", "financial", "budget", "accounting"],
    "数据": ["data", "analysis", "analytics", "statistics"],
    "服务": ["service", "server", "api", "backend"],
    "测试": ["test", "testing", "qa", "quality"],
    "监控": ["monitor", "monitoring", "watch", "observe"],
    "日志": ["log", "logging", "trace"],
    "优化": ["optimize", "optimization", "performance"],
    "修复": ["fix", "repair", "bugfix", "patch"],
    "配置": ["config", "configuration", "setup", "settings"],
    "数据库": ["database", "sql", "storage", "postgres"],
    "网络": ["network", "http", "dns", "proxy"],
    "前端": ["frontend", "ui", "react", "vue", "web"],
    "后端": ["backend", "api", "server", "service"],
    "运维": ["devops", "infra", "operations", "deploy"],
    "文档": ["docs", "documentation", "write", "readme"],
    "设计": ["design", "architecture", "plan", "blueprint"],
}

def _expand_cn_tokens(tokens: set[str]) -> set[str]:
    """Expand Chinese tokens to English synonyms for cross-language matching."""
    expanded = set(tokens)
    for t in tokens:
        if t in _CN_EN_MAP:
            expanded.update(_CN_EN_MAP[t])
    return expanded


def score(task_desc: str, card: dict) -> tuple[float, str]:
    desc_lower = task_desc.lower()
    name = (card.get("name") or "").lower()
    descr = (card.get("description") or "").lower()
    raw_tokens = set(re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}", desc_lower))
    tokens = _expand_cn_tokens(raw_tokens) if raw_tokens else set()
    if not tokens:
        kw_score = 0.0
        kw_hits: list[str] = []
    else:
        hay = name + " " + descr
        hits = [t for t in tokens if t in hay]
        kw_score = min(1.0, len(hits) / max(3, len(tokens)))
        kw_hits = hits[:5]
    skills = card.get("skills", [])
    # Build extended hay from skill fields for keyword matching
    skill_hay = " ".join(
        s.get("id", "") + " " + s.get("name", "") + " " + s.get("description", "")
        for s in skills
    ).lower()
    if skill_hay:
        skill_hits = [t for t in tokens if t in skill_hay]
        kw_score = max(kw_score, min(1.0, len(skill_hits) / max(3, len(tokens))))
        kw_hits = list(dict.fromkeys(kw_hits + skill_hits))[:5]
    matched_skills = []
    for s in skills:
        skill_blob = " ".join([
            s.get("id", ""), s.get("name", ""), s.get("description", ""),
            " ".join(s.get("tags", [])), " ".join(s.get("examples", []))
        ]).lower()
        if any(t in skill_blob for t in tokens if len(t) >= 2):
            matched_skills.append(s.get("id") or s.get("name") or "?")
    sk_score = min(1.0, len(matched_skills) / max(2, len(skills) or 1))
    total = round(0.6 * kw_score + 0.4 * sk_score, 3)
    parts = []
    if matched_skills:
        parts.append("matched skills: " + ", ".join(matched_skills[:4]))
    if kw_hits:
        parts.append("keywords: " + ", ".join(kw_hits[:3]))
    return total, "; ".join(parts) or "no signal"


def recommend(task_desc: str, top_n: int = 3) -> dict:
    cards = discover_all()
    ranked = []
    for profile, card in cards.items():
        s, reason = score(task_desc, card)
        ranked.append({"profile": profile, "score": s, "reason": reason})
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return {"task_desc": task_desc, "discovered": len(cards), "top3": ranked[:top_n]}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: a2a_dispatch.py '<task description>'", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(recommend(sys.argv[1]), ensure_ascii=False, indent=2))
