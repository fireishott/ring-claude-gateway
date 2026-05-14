# ring-claude-gateway

Intelligent routing layer for [Hermes Agent](https://github.com/nousresearch/hermes-agent) that combines two tools:

1. **Ring-2.6-1T** — silent pre-flight classifier that routes each message to either a conversational model (MiniMax) or a dev agent (claude-code)
2. **Claude Code** — Anthropic Pro-powered dev agent with superpowers plugins (TDD, subagent-driven development, systematic debugging, and more)

The result: a single Discord bot that handles casual conversation through MiniMax and complex coding/scripting tasks through claude-code — automatically, with no user-visible handoff.

---

## What It Does

```
Discord message
      │
      ▼
Ring-2.6-1T (silent classifier, free on OpenRouter)
      │
      ├── conversation ──► MiniMax-M2.7 (fast, conversational)
      │
      └── coding/files ──► claude-code subprocess
                               (superpowers plugins, Pro sub,
                                file ops, 50-turn autonomy)
```

Ring is invisible — users only ever see MiniMax or claude-code responses.

---

## Repository Contents

| File | Purpose |
|------|---------|
| `ring_router.py` | `_ring_classify()` function — drop into run_agent.py at module level |
| `routing_block.py` | Routing block — paste into `run_conversation()` loop |
| `tests/test_ring_router.py` | 11-test suite covering happy paths and all fallback scenarios |
| `config/config.example.yaml` | Sanitized Hermes config.yaml snippets |
| `docs/setup.md` | Step-by-step installation guide |
| `docs/architecture.md` | How the pieces fit together, key implementation details |

---

## Quick Start

See [docs/setup.md](docs/setup.md) for full installation instructions.

**TL;DR:**
1. Install Claude Code + superpowers plugin (`claude-code /plugins install superpowers`)
2. Get a free OpenRouter API key, add `OPENROUTER_API_KEY` to `~/.hermes/.env`
3. Add `ring_router.py` contents to run_agent.py (module level)
4. Add `routing_block.py` contents to `run_conversation()` loop
5. `systemctl --user restart hermes-gateway`
6. Verify: `journalctl --user -u hermes-gateway -n 50 --no-pager | grep RING`

---

## Requirements

- Hermes Agent (running as systemd user service)
- Claude Code CLI with Anthropic Pro subscription
- OpenRouter API key (Ring-2.6-1T is free tier)
- Any OpenAI-compatible conversational model (MiniMax, OpenAI, etc.)

---

## Superpowers Plugins via Claude Code

Once claude-code is installed with the superpowers plugin, it brings the full skill suite into every routed dev task:

- **brainstorming** — design → spec → plan workflow
- **subagent-driven-development** — fresh subagent per task with spec + quality review gates
- **systematic-debugging** — root cause first, no guessing
- **writing-plans** — TDD-first implementation plans with exact code
- **test-driven-development** — failing test before implementation
- And more at [superpowers docs](https://github.com/anthropics/claude-code)

---

## License

MIT
