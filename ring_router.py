"""
Ring silent routing classifier for Hermes AI Gateway.

Drop all module-level symbols into run_agent.py before class AIAgent,
then wire the routing block (routing_block.py) into run_conversation().

Routes:
  claude-code  — coding, scripts, file editing, debugging
  ask_claude   — complex reasoning, analysis, writing, planning (no tools)
  minimax      — casual conversation, simple questions

Requires: requests, OPENROUTER_API_KEY and MINIMAX_API_KEY in environment.
"""
import json
import logging
import os
import re as _re_cc

import requests

logger = logging.getLogger(__name__)

_RING_SYSTEM_PROMPT = (
    "You are a routing classifier for an AI assistant called Ignyte.\n"
    "Route to \"claude-code\" for: coding tasks, script fixes, file analysis, "
    "debugging, refactoring, building scripts, anything that requires a dev "
    "agent with file/tool access.\n"
    "Route to \"ask_claude\" for: complex reasoning, deep analysis, detailed writing, "
    "planning, research synthesis, long explanations, or any task that needs strong "
    "intelligence but NOT code execution or file editing.\n"
    "Route to \"minimax\" for: casual conversation, simple questions, quick opinions, "
    "or anything that is straightforward and does not require deep reasoning.\n"
    "If the message contains a file attachment (text document, Python script, etc.), "
    "always route to \"claude-code\" and include the file path in the task description.\n"
    "Return ONLY valid JSON. No other text.\n"
    "Examples:\n"
    "  {\"route\": \"minimax\"}\n"
    "  {\"route\": \"ask_claude\", \"task\": \"Analyze the pros and cons of X and give a detailed recommendation.\"}\n"
    "  {\"route\": \"claude-code\", \"task\": \"Read and fix the Python script at /path/to/file.py. "
    "Fix all bugs and save the corrected version in place.\"}"
)

# session_id → (route, task) tuple; set when CC/ask_claude asks a question
# mid-task so the user's reply routes back to the same handler.
# Legacy plain-string entries default to claude-code for backwards compat.
_ring_sticky: dict = {}

# gateway_key → last claude-code task; keyed by stable chat key so context
# survives /reset (new session_id, same channel)
_ring_last_cc_task: dict = {}

# Patterns that always mean claude-code regardless of Ring's decision
_CC_ALWAYS_R = _re_cc.compile(
    r'\bfix\s+(the\s+)?(script|bug|code|error)\b|'
    r'\brun\s+\S+\.py\b|'
    r'\blet\s+(him|claude|it)\s+(fix|do|handle|run|update)\b|'
    r'\b(build|create|write|generate)\b.{0,60}\bscript\b|'
    r'\bcoding\s+task\b',
    _re_cc.IGNORECASE
)
# Patterns that mean claude-code when a prior cc task exists in this channel
_CC_CONTEXT_R = _re_cc.compile(
    r'\brestart\b|\breboot\b|'
    r'\bupdate\s+(the\s+)?script\b|'
    r'\bdeploy\b|'
    r'\bdo\s+(those|that|the|it|them)\b|'
    r'\bapply\b|\bcontinue\b|\bproceed\b|\bgo\s+ahead\b|'
    r'\bphases?\b|\bfixes?\b|\bsteps?\b',
    _re_cc.IGNORECASE
)


def _ring_classify(last_content: str, openrouter_key: str, prior_text: str = "", prior_cc_task: str = "") -> tuple:
    """Call Ring-2.6-1T to classify message route. Returns (route, task, errored).

    route: 'minimax', 'claude-code', or 'ask_claude'.
    task: reformulated task description, or '' for minimax.
    errored: True when the classifier failed (vs. a deliberate minimax decision)
             — callers use this to trigger _minimax_classify fallback.
    Falls back to ('minimax', '', True) on any error.
    """
    if not openrouter_key:
        logger.warning("RING_ROUTER_ERR type=no_key detail=OPENROUTER_API_KEY not set")
        return ("minimax", "", True)
    try:
        _system = _RING_SYSTEM_PROMPT
        if prior_cc_task:
            _system += (
                "\n\nLast claude-code task executed in this channel (may still be relevant):\n"
                + prior_cc_task[:200]
            )
        if prior_text:
            _system += (
                "\n\nPrevious assistant response (use for follow-up context):\n"
                + prior_text[:300]
            )
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {openrouter_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "inclusionai/ring-2.6-1t",
                "messages": [
                    {"role": "system", "content": _system},
                    {"role": "user", "content": last_content[:400]},
                ],
                "max_tokens": 1000,
                "temperature": 0,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning("RING_ROUTER_ERR type=api_err detail=status=%d", resp.status_code)
            return ("minimax", "", True)
        _msg = resp.json()["choices"][0]["message"]
        text = (_msg.get("content") or _msg.get("reasoning") or _msg.get("reasoning_content") or "").strip()
        if not text:
            logger.warning("RING_ROUTER_ERR type=null_content detail=Ring returned no text")
            return ("minimax", "", True)
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        decision = json.loads(text)
        route = decision.get("route", "minimax")
        task = decision.get("task", "")
        logger.warning("RING_ROUTER_RESULT route=%s task=%s", route, task[:80])
        return (route, task, False)
    except requests.Timeout:
        logger.warning("RING_ROUTER_ERR type=timeout detail=15s exceeded")
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("RING_ROUTER_ERR type=bad_json detail=%s", str(exc)[:80])
    except Exception as exc:
        logger.warning("RING_ROUTER_ERR type=exception detail=%s", str(exc)[:80])
    return ("minimax", "", True)


def _minimax_classify(last_content: str, minimax_key: str, prior_text: str = "", prior_cc_task: str = "") -> tuple:
    """MiniMax-M2.7 fallback classifier when Ring errors. Returns (route, task).

    Uses the same routing prompt as Ring via MiniMax's Anthropic-compatible API.
    Only called on Ring failures — not on deliberate minimax decisions.
    Falls back to ('minimax', '') on any error.
    """
    if not minimax_key:
        logger.warning("RING_MM_ERR type=no_key detail=MINIMAX_API_KEY not set")
        return ("minimax", "")
    try:
        _system = _RING_SYSTEM_PROMPT
        if prior_cc_task:
            _system += (
                "\n\nLast claude-code task executed in this channel (may still be relevant):\n"
                + prior_cc_task[:200]
            )
        if prior_text:
            _system += (
                "\n\nPrevious assistant response (use for follow-up context):\n"
                + prior_text[:300]
            )
        resp = requests.post(
            "https://api.minimax.io/anthropic/v1/messages",
            headers={
                "x-api-key": minimax_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": "MiniMax-M2.7",
                "max_tokens": 200,
                "system": _system,
                "messages": [{"role": "user", "content": last_content[:400]}],
            },
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning("RING_MM_ERR type=api_err detail=status=%d", resp.status_code)
            return ("minimax", "")
        blocks = resp.json().get("content", [])
        text = next((b.get("text", "") for b in blocks if b.get("type") == "text"), "").strip()
        if not text:
            logger.warning("RING_MM_ERR type=null_content")
            return ("minimax", "")
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        decision = json.loads(text)
        route = decision.get("route", "minimax")
        task = decision.get("task", "")
        logger.warning("RING_MM_RESULT route=%s task=%s", route, task[:80])
        return (route, task)
    except requests.Timeout:
        logger.warning("RING_MM_ERR type=timeout detail=10s exceeded")
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("RING_MM_ERR type=bad_json detail=%s", str(exc)[:80])
    except Exception as exc:
        logger.warning("RING_MM_ERR type=exception detail=%s", str(exc)[:80])
    return ("minimax", "")
