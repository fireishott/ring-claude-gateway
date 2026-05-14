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
    # The injected prior text block should contain exactly 300 x's
    assert "x" * 300 in system_msg
    assert "x" * 301 not in system_msg


def test_no_prior_text_omits_context_block():
    """Without prior_text the system prompt is the base prompt only."""
    with patch("requests.post", return_value=_mock_ring_resp({"route": "minimax"})) as mock_post:
        _ring_classify("Hello", "test-key")
    system_msg = mock_post.call_args[1]["json"]["messages"][0]["content"]
    assert "Previous assistant response" not in system_msg


def test_follow_up_with_prior_dev_context():
    """'do it again' with a prior dev-task response routes to claude-code."""
    prior = "ErsatzTV restarted successfully via Docker API on 192.168.10.10."
    body = {"route": "claude-code", "task": "Restart ErsatzTV again using Docker API"}
    with patch("requests.post", return_value=_mock_ring_resp(body)):
        route, task = _ring_classify("do it again", "test-key", prior_text=prior)
    assert route == "claude-code"
    assert task != ""
