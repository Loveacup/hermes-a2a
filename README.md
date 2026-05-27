# hermes-a2a

A2A (Agent-to-Agent) Protocol plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

Enables cross-profile communication in the 三省六部 (Three Provinces Six Ministries) governance system — ministries discover each other's capabilities, delegate tasks synchronously, and stream progress in real time.

## Architecture

```
         Agent Card
    ┌─────────────────┐
    │  name, skills    │
    │  model, tools    │   ← Auto-generated from profile config
    │  endpoint URL    │
    └─────────────────┘
           │
    ┌──────▼──────┐     ┌──────────────┐
    │ A2A Server  │────▶│ Hermes Agent │
    │ (HTTP/JSON) │◀────│    Loop      │
    └─────────────┘     └──────────────┘
           │
    ┌──────▼──────┐
    │ Task Store  │
    │ (in-memory) │
    └─────────────┘
```

## Quick Start

```bash
# 1. Enable plugin for a profile
hermes plugins enable hermes-a2a --profile shangshu

# 2. Start gateway (plugin auto-starts A2A server)
hermes gateway restart --profile shangshu

# 3. Discover Agent Card
curl http://localhost:8650/a2a/.well-known/agent-card.json

# 4. Send a task
curl -X POST http://localhost:8650/a2a/tasks \
  -H "Content-Type: application/json" \
  -d '{"message": {"text": "Check infrastructure health"}}'
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/a2a/.well-known/agent-card.json` | Agent Card (capability manifest) |
| POST | `/a2a/tasks` | Create and queue a task |
| POST | `/a2a/tasks/send` | Fire-and-forget task |
| GET | `/a2a/tasks/{id}` | Task status |
| GET | `/a2a/tasks/{id}/stream` | SSE streaming progress |

## 三省六部 Deployment

```
Profile     Port    Role
─────────────────────────────
shangshu    8650    API Hub / dispatcher
engineer    8651    兵部 / code implementation
gongbu      8652    工部 / infrastructure
budget      8653    户部 / data & cost
tester      8654    刑部 / testing & audit
protocol    8655    礼部 / documentation
registry    8656    吏部 / agent registry
```

## License

MIT
