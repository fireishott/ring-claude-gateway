# ring-claude-gateway

Intelligent routing layer for [Hermes Agent](https://github.com/nousresearch/hermes-agent) built around two tools:

1. **Ring-2.6-1T** — silent pre-flight classifier that routes each message to either a conversational model or a dev agent
2. **Claude Code** — Anthropic Pro-powered dev agent with superpowers plugins (TDD, subagent-driven development, systematic debugging, and more)

The result: a single bot that handles casual conversation through your preferred LLM and complex coding/scripting tasks through claude-code — automatically, with no user-visible handoff.

---

## How It Works

```
Incoming message
      │
      ▼
Ring-2.6-1T (silent classifier, free on OpenRouter)
      │
      ├── conversation ──► your conversational LLM (fast, stateless)
      │
      └── coding/files ──► claude-code subprocess
                               (superpowers plugins, Pro sub,
                                file ops, 50-turn autonomy)
```

Ring is invisible — users only ever see responses from the conversational model or claude-code.

---

## Repository Contents

| File | Purpose |
|------|---------|
| `ring_router.py` | `_ring_classify()` — drop into run_agent.py at module level |
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
4. Paste `routing_block.py` into the `run_conversation()` loop
5. `systemctl --user restart hermes-gateway`
6. Verify: `journalctl --user -u hermes-gateway -n 50 --no-pager | grep RING`

---

## Requirements

- [Hermes Agent](https://github.com/nousresearch/hermes-agent) running as a systemd user service
- [Claude Code](https://claude.ai/code) CLI with an Anthropic Pro subscription
- [OpenRouter](https://openrouter.ai) API key (Ring-2.6-1T is free tier — no credits needed)
- Any OpenAI-compatible conversational LLM configured in Hermes (MiniMax, OpenAI, Anthropic, etc.)

---

## Superpowers Plugins

Once claude-code is installed with the superpowers plugin, every routed dev task has access to the full skill suite:

- **brainstorming** — design → spec → implementation plan workflow
- **subagent-driven-development** — fresh subagent per task with spec and quality review gates
- **systematic-debugging** — root cause first, no guessing
- **writing-plans** — TDD-first implementation plans with exact code
- **test-driven-development** — failing test before implementation

---

## License

[MIT](LICENSE)
