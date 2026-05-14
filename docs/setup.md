# Setup Guide

## Prerequisites

| Component | What it does | Where to get it |
|-----------|-------------|-----------------|
| [Hermes Agent](https://github.com/nousresearch/hermes-agent) | AI gateway — handles Discord/messaging | Hermes install |
| [Claude Code](https://claude.ai/code) | Dev agent with tool access, superpowers plugins | Anthropic Pro sub |
| OpenRouter account | Hosts Ring-2.6-1T (free tier) | openrouter.ai |
| MiniMax API | Conversational LLM (or swap for any provider) | api.minimax.io |

---

## 1. Install Claude Code

```bash
npm install -g @anthropic-ai/claude-code
```

Then authenticate with your Anthropic Pro subscription:

```bash
claude-code /login
```

Install the superpowers plugin:

```bash
claude-code /plugins install superpowers
```

This gives you `brainstorming`, `subagent-driven-development`, `systematic-debugging`, and the full superpowers skill suite.

---

## 2. Get an OpenRouter API Key

1. Sign up at [openrouter.ai](https://openrouter.ai)
2. Create an API key (Ring-2.6-1T is free — no credits needed)
3. Add it to your Hermes env file:

```bash
echo "OPENROUTER_API_KEY=sk-or-v1-YOUR_KEY_HERE" >> ~/.hermes/.env
```

**Important:** Put it in `~/.hermes/.env`, NOT `~/.bashrc`. The Hermes systemd service doesn't source `.bashrc`.

---

## 3. Integrate the Ring Classifier into run_agent.py

### Step 1: Add the module-level function

Open `~/.hermes/hermes-agent/run_agent.py` and find `class AIAgent:`. Insert the contents of `ring_router.py` **before** that class definition.

### Step 2: Add the routing block

Inside `run_conversation()`, find the `while retry_count < max_retries:` loop. Paste the contents of `routing_block.py` at the **top of that loop**, before the primary LLM call.

Adjust two paths in `routing_block.py` to match your environment:
- `/home/user/.local/bin/claude-code` → your actual claude-code path (`which claude-code`)
- `/path/to/your/output/file.py` → where you want claude-code to save generated files

### Step 3: Restart the gateway

```bash
systemctl --user restart hermes-gateway
```

---

## 4. Verify Routing is Working

```bash
journalctl --user -u hermes-gateway -n 50 --no-pager | grep RING
```

You should see:
- `RING_ROUTER_FIRED len=<n>` — classifier invoked
- `RING_ROUTER_RESULT route=minimax` — conversational message routed to MiniMax
- `RING_ROUTER_RESULT route=claude-code task=...` — dev task routed to claude-code
- `ROUTER_CC_RC=0` — claude-code completed successfully

---

## 5. Run the Tests

```bash
cd ~/.hermes/hermes-agent
pip install pytest
pytest tests/run_agent/test_ring_router.py -v
```

All 11 tests should pass.
