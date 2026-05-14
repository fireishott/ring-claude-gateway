"""
Ring silent routing classifier.

Drop _ring_classify() and _RING_SYSTEM_PROMPT into run_agent.py at module level
(before class AIAgent), then wire the routing block into run_conversation().

Requires: requests, OPENROUTER_API_KEY in environment.
"""
import json
import logging
import os

import requests

logger = logging.getLogger(__name__)

_RING_SYSTEM_PROMPT = (
    'You are a routing classifier for an AI assistant.\n'
    'Route to "claude-code" for: coding tasks, script fixes, file analysis, '
    'debugging, refactoring, building scripts, anything that requires a dev '
    'agent with tool access.\n'
    'Route to "minimax" for: conversation, questions, explanations, opinions, '
    'creative writing, or anything that does not require code execution.\n'
    'If the message contains a file attachment (text document, Python script, etc.), '
    'always route to "claude-code" and include the file path in the task description.\n'
    'Return ONLY valid JSON. No other text.\n'
    'Examples:\n'
    '  {"route": "minimax"}\n'
    '  {"route": "claude-code", "task": "Read and fix the Python script at /path/to/file.py. '
    'Fix all bugs and save the corrected version in place."}'
)


def _ring_classify(last_content: str, openrouter_key: str, prior_text: str = "") -> tuple[str, str]:
    """
    Classify a message and return (route, task).

    route: "minimax" | "claude-code"
    task:  task string for claude-code, "" otherwise

    prior_text is the last assistant response — gives Ring context to correctly
    classify follow-ups like "do it again" or "try that again".

    Always falls back to ("minimax", "") on any error — no message is ever dropped.
    """
    if not openrouter_key:
        logger.warning("RING_ROUTER_ERR type=no_key detail=OPENROUTER_API_KEY not set")
        return ("minimax", "")
    try:
        system = _RING_SYSTEM_PROMPT
        if prior_text:
            system += (
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
                "model": "inclusionai/ring-2.6-1t:free",
                "messages": [
                    {"role": "system", "content": system},
                    # 400 chars is enough to see attachment headers without
                    # sending full file content that triggers Ring's thinking mode
                    {"role": "user", "content": last_content[:400]},
                ],
                "max_tokens": 500,
                "temperature": 0,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning("RING_ROUTER_ERR type=api_err detail=status=%d", resp.status_code)
            return ("minimax", "")
        _msg = resp.json()["choices"][0]["message"]
        # Ring-2.6-1T is a thinking model — sometimes returns content=null
        # with the actual output in reasoning_content
        text = (_msg.get("content") or _msg.get("reasoning_content") or "").strip()
        if not text:
            logger.warning("RING_ROUTER_ERR type=null_content detail=Ring returned no text")
            return ("minimax", "")
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        decision = json.loads(text)
        route = decision.get("route", "minimax")
        task = decision.get("task", "")
        logger.warning("RING_ROUTER_RESULT route=%s task=%s", route, task[:80])
        return (route, task)
    except requests.Timeout:
        logger.warning("RING_ROUTER_ERR type=timeout detail=15s exceeded")
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("RING_ROUTER_ERR type=bad_json detail=%s", str(exc)[:80])
    except Exception as exc:
        logger.warning("RING_ROUTER_ERR type=exception detail=%s", str(exc)[:80])
    return ("minimax", "")
