# Architecture

## How It Works

```
Discord message
      │
      ▼
Ring-2.6-1T classifier (silent, ~1-2s, free on OpenRouter)
      │
      ├── route: "minimax" ──────────────► MiniMax-M2.7 (conversational)
      │                                         │
      │                                         ▼
      │                                   Discord reply
      │
      └── route: "claude-code" ──────────► claude-code subprocess
            + task description                  │
                                          (superpowers plugins,
                                           Anthropic Pro tools,
                                           file ops, web access)
                                                 │
                                                 ▼
                                           Discord reply
```

Ring is invisible to the user. Every message goes through it; only MiniMax or claude-code replies are delivered.

---

## Why This Combination Works

### Ring-2.6-1T
- 1T-parameter thinking model, specialized for multi-step agent workflows
- Free tier on OpenRouter — zero cost for routing
- Returns deterministic JSON (`{"route": "...", "task": "..."}`)
- Sees only the first 400 chars of each message (enough to detect attachments/intent without sending full file content, which would trigger Ring's full thinking mode and exhaust its token budget)

### Claude Code
- Full Anthropic Pro subscription capabilities: long context, tool use, file access
- Superpowers plugins: `brainstorming`, `subagent-driven-development`, `systematic-debugging`, `writing-plans`, TDD, and more
- Runs as a subprocess — completely isolated from the Hermes gateway process
- `--dangerously-skip-permissions` + `--max-turns 50` for autonomous task completion

### MiniMax-M2.7
- Fast, capable conversational model
- Handles the majority of messages (questions, opinions, chat)
- Zero routing overhead for conversational turns

---

## Key Implementation Details

### Thinking Model Quirk
Ring-2.6-1T sometimes returns `content: null` with the actual output in `reasoning_content`. The classifier checks both fields:
```python
text = (_msg.get("content") or _msg.get("reasoning_content") or "").strip()
```

### Response Format Branching
MiniMax uses `api_mode = "anthropic_messages"` (Anthropic transport), which expects `response.content` as a list of content blocks — not OpenAI-style `response.choices`. The routing block branches on `self.api_mode` to build the correct response shape.

### Retry Guard
`retry_count == 0` prevents Ring from re-firing on MiniMax validation retries, which would re-run claude-code wastefully (3+ minutes per retry).

### MAX TURNS Graceful Handling
If claude-code hits its turn budget but wrote the output file within the last 10 minutes, the routing block treats it as success. This handles complex 63K+ scripts that need all 50 turns to fix but can't finish their summary response in time.

### MiniMax Always Wins on Failure
Every Ring error path (timeout, bad JSON, API error, null content, network failure) falls through to MiniMax. No message is ever dropped.

---

## Log Markers

| Log line | Meaning |
|----------|---------|
| `RING_ROUTER_FIRED len=<n>` | Classifier invoked for message of n chars |
| `RING_ROUTER_RESULT route=minimax` | Routed to conversational model |
| `RING_ROUTER_RESULT route=claude-code task=...` | Routed to dev agent |
| `RING_ROUTER_ERR type=timeout` | Ring took >15s, fell back to MiniMax |
| `RING_ROUTER_ERR type=api_err detail=status=429` | Rate limit, fell back |
| `RING_ROUTER_ERR type=null_content` | Ring returned no text, fell back |
| `ROUTER_CC_RC=0` | claude-code succeeded |
| `ROUTER_CC_RC=1` | claude-code failed, fell back to MiniMax |
