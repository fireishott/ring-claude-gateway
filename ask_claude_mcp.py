#!/usr/bin/env python3
# Ignyte ask_claude MCP Server v2.2 -- MiniMax-M2.7 fallback
import json, sys, subprocess, os, urllib.request, logging, threading, queue

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s mcp: %(message)s")

CLAUDE_CLI = "/home/fihadmin/.local/bin/claude-code"
MINIMAX_URL = "https://api.minimax.io/anthropic/v1/messages"
MINIMAX_MODEL = "MiniMax-M2.7"
RATE_LIMIT_SIGNALS = [
    "rate limit","rate_limit","429","quota","quota_exceeded","over_quota",
    "session limit","session_full","too many requests","limit reached",
    "usage limit","out of credits","billing"
]

msg_queue = queue.Queue()
write_lock = threading.Lock()

def respond(id, result):
    with write_lock:
        print(json.dumps({"jsonrpc":"2.0","id":id,"result":result}), flush=True)

def is_rate_limit(text):
    return any(s in text.lower() for s in RATE_LIMIT_SIGNALS)

def invoke_minimax(prompt, effort="medium"):
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY not set")
    max_tokens = 8192 if effort in ("high", "max") else 4096
    payload = json.dumps({
        "model": MINIMAX_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(MINIMAX_URL, data=payload, headers={
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read())
    blocks = data.get("content", [])
    text = next((b["text"] for b in blocks if b.get("type") == "text"), None)
    if not text:
        raise RuntimeError("MiniMax returned no text block")
    return text

def handle_call(args):
    prompt = args.get("prompt", "")
    effort = args.get("effort", "medium")
    file_path = args.get("file_path", "")
    cmd = [CLAUDE_CLI, "-p", prompt, "--dangerously-skip-permissions"]
    if file_path:
        cmd += ["--allowedTools", "Read,Edit,Write,Bash", "--max-turns", "15", "--add-dir", "/home/fihadmin"]
    else:
        cmd += ["--max-turns", "10"]
    if effort != "medium":
        cmd += ["--effort", effort]
    rate_limited = False
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
        out = r.stdout.strip()
        err = r.stderr.strip()
        combined = out + " " + err
        if r.returncode == 0 and out and len(out) >= 20 and not is_rate_limit(combined):
            logging.info("Claude Pro success")
            return {"content": [{"type": "text", "text": out + "\n\n-# claude-code claude.ai/pro"}], "isError": False}
        if is_rate_limit(combined):
            logging.info("Rate limit — falling back to MiniMax-M2.7")
            rate_limited = True
        else:
            return {"content": [{"type": "text", "text": out or err or "No output."}], "isError": True}
    except subprocess.TimeoutExpired:
        logging.warning("Timeout — falling back to MiniMax-M2.7")
        rate_limited = True
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}
    if not rate_limited:
        return {"content": [{"type": "text", "text": "Claude Code failed."}], "isError": True}
    try:
        logging.info("Invoking MiniMax-M2.7 fallback")
        out = invoke_minimax(prompt, effort)
        return {"content": [{"type": "text", "text": out + "\n\n-# minimax-m2.7 (pro at limit)"}], "isError": False}
    except Exception as e:
        logging.warning(f"MiniMax fallback failed: {e}")
        return {"content": [{"type": "text", "text": f"All layers failed. Pro: limit. MiniMax: {e}"}], "isError": True}

def stdin_reader():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            method = req.get("method", "")
            if method == "ping":
                respond(req.get("id"), {})
            elif method == "notifications/initialized":
                pass
            else:
                msg_queue.put(req)
        except Exception:
            pass
    msg_queue.put(None)

def main():
    threading.Thread(target=stdin_reader, daemon=True).start()
    while True:
        req = msg_queue.get()
        if req is None:
            break
        try:
            method = req.get("method", "")
            id = req.get("id")
            if method == "initialize":
                respond(id, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "ask-claude", "version": "2.2.0"}})
            elif method == "tools/list":
                respond(id, {"tools": [{
                    "name": "ask_claude",
                    "description": "Send reasoning, analysis, writing, debugging or planning tasks to Claude Pro via claude-code CLI. Falls back to MiniMax-M2.7 if Pro is at limit. Always prefer this over answering complex questions yourself.",
                    "inputSchema": {"type": "object", "properties": {
                        "prompt": {"type": "string"},
                        "effort": {"type": "string", "enum": ["low", "medium", "high", "max"], "default": "medium"},
                        "file_path": {"type": "string"}
                    }, "required": ["prompt"]}
                }]})
            elif method == "tools/call":
                args = req.get("params", {}).get("arguments", {})
                result = handle_call(args)
                respond(id, result)
        except Exception:
            pass

if __name__ == "__main__":
    main()
