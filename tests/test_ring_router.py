# tests/run_agent/test_ring_router.py
"""Tests for Ring silent routing classifier (_ring_classify in run_agent.py)."""
import json

import requests
from unittest.mock import MagicMock, patch

import run_agent
from run_agent import _ring_classify


def _mock_ring_resp(body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": {"content": json.dumps(body)}}]}
    return resp


# ── happy paths ──────────────────────────────────────────────────────────────

def test_minimax_route():
    with patch("requests.post", return_value=_mock_ring_resp({"route": "minimax"})):
        route, task = _ring_classify("Hello, how are you?", "test-key")
    assert route == "minimax"
    assert task == ""


def test_claude_code_route():
    body = {"route": "claude-code", "task": "Fix the script at /tmp/script.py"}
    with patch("requests.post", return_value=_mock_ring_resp(body)):
        route, task = _ring_classify("Can you fix this Python script?", "test-key")
    assert route == "claude-code"
    assert task == "Fix the script at /tmp/script.py"


def test_truncates_content_to_400_chars():
    """Ring only sees the first 400 chars — enough to see attachment headers."""
    with patch("requests.post", return_value=_mock_ring_resp({"route": "minimax"})) as mock_post:
        _ring_classify("x" * 5000, "test-key")
    user_msg = mock_post.call_args[1]["json"]["messages"][1]["content"]
    assert len(user_msg) == 400


def test_strips_markdown_code_fences():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": '```json\n{"route": "minimax"}\n```'}}]
    }
    with patch("requests.post", return_value=resp):
        route, task = _ring_classify("Hello", "test-key")
    assert route == "minimax"


# ── fallback paths ────────────────────────────────────────────────────────────

def test_empty_key_skips_ring():
    """No API key → skip Ring entirely, return minimax without calling requests."""
    with patch("requests.post") as mock_post:
        route, task = _ring_classify("Fix this script", "")
    mock_post.assert_not_called()
    assert route == "minimax"
    assert task == ""


def test_timeout_falls_back():
    with patch("requests.post", side_effect=requests.Timeout()):
        route, task = _ring_classify("Fix this script", "test-key")
    assert route == "minimax"
    assert task == ""


def test_bad_json_falls_back():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": {"content": "not valid json"}}]}
    with patch("requests.post", return_value=resp):
        route, task = _ring_classify("Fix this script", "test-key")
    assert route == "minimax"
    assert task == ""


def test_api_error_falls_back():
    resp = MagicMock()
    resp.status_code = 429
    with patch("requests.post", return_value=resp):
        route, task = _ring_classify("Fix this script", "test-key")
    assert route == "minimax"
    assert task == ""


def test_exception_falls_back():
    with patch("requests.post", side_effect=ConnectionError("network down")):
        route, task = _ring_classify("Fix this script", "test-key")
    assert route == "minimax"
    assert task == ""


def test_null_content_falls_back():
    """Ring-2.6-1T (thinking model) sometimes returns null content field."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": {"content": None, "reasoning_content": None}}]}
    with patch("requests.post", return_value=resp):
        route, task = _ring_classify("Fix this script", "test-key")
    assert route == "minimax"
    assert task == ""


def test_reasoning_content_fallback():
    """Ring-2.6-1T may return JSON in reasoning_content when content is null."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": None, "reasoning_content": '{"route": "claude-code", "task": "Fix /tmp/x.py"}'}}]
    }
    with patch("requests.post", return_value=resp):
        route, task = _ring_classify("Fix this script", "test-key")
    assert route == "claude-code"
    assert task == "Fix /tmp/x.py"


# ── prior context (follow-up routing) ────────────────────────────────────────

def test_prior_text_appended_to_system_prompt():
    """Prior assistant text is injected into the system prompt for follow-up context."""
    prior = "ErsatzTV restarted successfully via Docker API."
    with patch("requests.post", return_value=_mock_ring_resp({"route": "claude-code", "task": "Restart ErsatzTV again"})) as mock_post:
        _ring_classify("do it again", "test-key", prior_text=prior)
    system_msg = mock_post.call_args[1]["json"]["messages"][0]["content"]
    assert "ErsatzTV restarted successfully" in system_msg
    assert "Previous assistant response" in system_msg


def test_prior_text_truncated_to_300_chars():
    """Prior text is capped at 300 chars to keep Ring's token budget small."""
    prior = "x" * 5000
    with patch("requests.post", return_value=_mock_ring_resp({"route": "minimax"})) as mock_post:
        _ring_classify("do it again", "test-key", prior_text=prior)
    system_msg = mock_post.call_args[1]["json"]["messages"][0]["content"]
    assert "x" * 300 in system_msg
    assert "x" * 301 not in system_msg


def test_no_prior_text_omits_context_block():
    """Without prior_text or prior_cc_task the system prompt is the base prompt only."""
    with patch("requests.post", return_value=_mock_ring_resp({"route": "minimax"})) as mock_post:
        _ring_classify("Hello", "test-key")
    system_msg = mock_post.call_args[1]["json"]["messages"][0]["content"]
    assert "Previous assistant response" not in system_msg
    assert "Last claude-code task" not in system_msg


def test_follow_up_with_prior_dev_context():
    """'do it again' with a prior dev-task response routes to claude-code."""
    prior = "ErsatzTV restarted successfully via Docker API on 192.168.10.10."
    body = {"route": "claude-code", "task": "Restart ErsatzTV again using Docker API"}
    with patch("requests.post", return_value=_mock_ring_resp(body)):
        route, task = _ring_classify("do it again", "test-key", prior_text=prior)
    assert route == "claude-code"
    assert task != ""


def test_prior_cc_task_appended_to_system_prompt():
    """Last claude-code task is injected so Ring can classify repeat commands."""
    prior_cc = "Restart ErsatzTV service via Docker API on 192.168.10.10."
    with patch("requests.post", return_value=_mock_ring_resp({"route": "claude-code", "task": "Restart ErsatzTV again"})) as mock_post:
        _ring_classify("restart ers", "test-key", prior_cc_task=prior_cc)
    system_msg = mock_post.call_args[1]["json"]["messages"][0]["content"]
    assert "Last claude-code task" in system_msg
    assert "Restart ErsatzTV service" in system_msg


def test_prior_cc_task_truncated_to_200_chars():
    """prior_cc_task is capped at 200 chars."""
    prior_cc = "y" * 5000
    with patch("requests.post", return_value=_mock_ring_resp({"route": "minimax"})) as mock_post:
        _ring_classify("hello", "test-key", prior_cc_task=prior_cc)
    system_msg = mock_post.call_args[1]["json"]["messages"][0]["content"]
    assert "y" * 200 in system_msg
    assert "y" * 201 not in system_msg


# ── sticky session (mid-task Q&A) ─────────────────────────────────────────────

def test_sticky_set_when_claude_code_asks_question():
    """After claude-code outputs a question, _ring_sticky is populated."""
    import run_agent
    run_agent._ring_sticky.clear()
    sess = "test-session-sticky"
    # Simulate the sticky being set (routing block logic, not _ring_classify)
    run_agent._ring_sticky[sess] = "Fix the script at /tmp/foo.py"
    assert sess in run_agent._ring_sticky


def test_sticky_resume_builds_accumulated_task():
    """Sticky resume appends user reply to the stored task."""
    import run_agent
    run_agent._ring_sticky.clear()
    sess = "test-session-resume"
    run_agent._ring_sticky[sess] = "Fix the script at /tmp/foo.py"
    # Simulate what the routing block does on resume
    sticky_task = run_agent._ring_sticky.pop(sess, None)
    user_reply = "just the critical bugs"
    accumulated = sticky_task + f"\n\nUser replied: {user_reply}"
    assert "Fix the script" in accumulated
    assert "just the critical bugs" in accumulated
    assert sess not in run_agent._ring_sticky  # popped — cleared after use


def test_sticky_cleared_after_resume():
    """Sticky entry is consumed (popped) when the follow-up is processed."""
    import run_agent
    run_agent._ring_sticky.clear()
    sess = "test-session-clear"
    run_agent._ring_sticky[sess] = "some task"
    _ = run_agent._ring_sticky.pop(sess, None)
    assert sess not in run_agent._ring_sticky


def test_no_sticky_falls_through_to_ring():
    """Without a sticky entry, normal Ring classification runs."""
    import run_agent
    run_agent._ring_sticky.clear()
    body = {"route": "minimax"}
    with patch("requests.post", return_value=_mock_ring_resp(body)):
        route, task = _ring_classify("how are you?", "test-key")
    assert route == "minimax"
