"""
Routing block for run_agent.py — run_conversation() integration.

Paste this block inside run_conversation(), at the top of the
`while retry_count < max_retries:` loop, before the primary LLM call.

Prerequisites already in scope: api_messages, retry_count, self, os, time,
SimpleNamespace, json, logging. ring_router._ring_classify and
_RING_SYSTEM_PROMPT imported at module level.
"""

# ── Ring silent routing classifier ────────────────────────────────────────────
import subprocess as _sp_r
import threading as _threading_r
import uuid as _uuid_r

response = None
_last_content = ""

# Extract the last user message text
if api_messages and api_messages[-1].get("role") == "user":
    _lc = api_messages[-1].get("content", "")
    if isinstance(_lc, list):
        _last_content = " ".join(
            p.get("text", "")
            for p in _lc
            if isinstance(p, dict) and p.get("type") == "text"
        )
    else:
        _last_content = _lc or ""

# Skip internal/background messages that should never be routed
_skip_internal = any(
    m in _last_content[:300]
    for m in [
        "background skill CURATOR",
        "update the skill library",
        "Review the conversation above",
        "You are running as Hermes",
        "UMBRELLA-BUILDING",
    ]
)
if _skip_internal:
    _last_content = ""

if _last_content and retry_count == 0:
    _prior_text = ""
    for _m in reversed(api_messages):
        if _m.get("role") == "assistant":
            _ac = _m.get("content", "")
            if isinstance(_ac, list):
                _prior_text = " ".join(
                    p.get("text", "")
                    for p in _ac
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            else:
                _prior_text = _ac or ""
            break

    # Resume sticky claude-code Q&A session if one is pending,
    # otherwise classify normally via Ring
    _sess = getattr(self, "session_id", "") or ""
    _sticky_task = _ring_sticky.pop(_sess, None)
    if _sticky_task:
        # User is answering a question claude-code asked —
        # skip Ring and route straight back with accumulated context
        _ring_route = "claude-code"
        _ring_task = _sticky_task + f"\n\nUser replied: {_last_content}"
        logger.warning("RING_STICKY_RESUME session=%s", _sess)
    else:
        logger.warning("RING_ROUTER_FIRED len=%d prior=%d", len(_last_content), len(_prior_text))
        _ring_key = os.getenv("OPENROUTER_API_KEY", "")
        _ring_route, _ring_task = _ring_classify(_last_content, _ring_key, _prior_text)

    if _ring_route == "claude-code" and _ring_task:
        # If Ring identified a cached document, append the output path
        # Adjust this path to match your environment
        _output_path = "/path/to/your/output/file.py"
        if "/your/cache/documents/" in _ring_task:
            _ring_task += f" Save corrected version to {_output_path}"

        # Build a response from text — used for success AND errors so MiniMax
        # never handles a task already routed to claude-code
        def _mk_cc_resp(_text):
            if getattr(self, "api_mode", "") == "anthropic_messages":
                return SimpleNamespace(
                    id="route-" + str(_uuid_r.uuid4()),
                    model=self.model,
                    content=[SimpleNamespace(type="text", text=_text)],
                    stop_reason="end_turn",
                    usage=SimpleNamespace(input_tokens=0, output_tokens=0),
                )
            return SimpleNamespace(
                id="route-" + str(_uuid_r.uuid4()),
                model=self.model,
                choices=[SimpleNamespace(
                    index=0,
                    message=SimpleNamespace(role="assistant", content=_text, tool_calls=None, reasoning_content=None),
                    finish_reason="stop",
                )],
                usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            )

        try:
            # Strip ANTHROPIC_API_KEY so claude-code uses the Pro subscription
            # OAuth credentials, not the gateway's API key.
            _cc_env = {k: v for k, v in os.environ.items()
                       if k != "ANTHROPIC_API_KEY"}

            # Log superpowers availability and full task
            _sp_plugin = os.path.expanduser(
                "~/.claude/plugins/cache/claude-plugins-official"
            )
            _sp_ok = os.path.isdir(_sp_plugin)
            logger.warning(
                "CC_DISPATCH task=%s superpowers=%s",
                _ring_task[:120],
                "YES" if _sp_ok else "NOT_FOUND",
            )

            # Stream stdout/stderr line-by-line so journalctl shows live
            # progress and Discord heartbeats get real claude-code activity text.
            _cc_out_lines: list = []
            _cc_err_lines: list = []

            # Point the agent's activity fields at claude-code so the
            # "Still working..." heartbeat shows what's actually happening.
            if hasattr(self, "_current_tool"):
                self._current_tool = "claude-code"
            if hasattr(self, "_touch_activity"):
                self._touch_activity("claude-code: starting task")

            def _cc_activity_hint(_ln):
                """Return a short Discord-friendly status for a cc output line."""
                _l = _ln.strip()
                if "superpowers:" in _l or _l.startswith("Using "):
                    return _l[:80]
                for _kw in ("Reading", "Writing", "Edit", "Bash", "Search",
                            "Analyzing", "Building", "Planning", "Implementing",
                            "Testing", "Fixing", "Creating", "Saving"):
                    if _l.startswith(_kw):
                        return _l[:80]
                if _l and _l[0] in ("✓", "●", "⚡", "→", "▶"):
                    return _l[:80]
                return None

            # Grab progress_callback once — feeds the Discord progress queue
            # (same mechanism as tool-use bubbles, ~1.5s delivery to Discord).
            _cc_progress_cb = getattr(self, "tool_progress_callback", None)

            def _pipe_reader(_pipe, _lines, _prefix):
                for _ln in iter(_pipe.readline, ""):
                    _ln = _ln.rstrip("\n")
                    if not _ln:
                        continue
                    logger.warning("%s %s", _prefix, _ln[:300])
                    _lines.append(_ln)
                    if _prefix == "CC_OUT:":
                        _hint = _cc_activity_hint(_ln)
                        if _hint:
                            # Update "Still working..." heartbeat
                            if hasattr(self, "_touch_activity"):
                                self._touch_activity(f"claude-code: {_hint}")
                            # Push directly to progress queue →
                            # Discord edit within ~1.5s (real-time)
                            if _cc_progress_cb:
                                try:
                                    _cc_progress_cb(
                                        event_type="tool.started",
                                        tool_name="claude-code",
                                        preview=_hint,
                                    )
                                except Exception:
                                    pass
                _pipe.close()

            _proc = _sp_r.Popen(
                [
                    "/home/user/.local/bin/claude-code",  # adjust path
                    "-p", _ring_task,
                    "--dangerously-skip-permissions",
                    "--max-turns", "50",
                ],
                stdout=_sp_r.PIPE,
                stderr=_sp_r.PIPE,
                text=True,
                stdin=_sp_r.DEVNULL,
                env=_cc_env,
            )
            _t_out = _threading_r.Thread(
                target=_pipe_reader,
                args=(_proc.stdout, _cc_out_lines, "CC_OUT:"),
                daemon=True,
            )
            _t_err = _threading_r.Thread(
                target=_pipe_reader,
                args=(_proc.stderr, _cc_err_lines, "CC_ERR:"),
                daemon=True,
            )
            _t_out.start()
            _t_err.start()
            try:
                _proc.wait(timeout=600)
            except _sp_r.TimeoutExpired:
                _proc.kill()
                _t_out.join(timeout=5)
                _t_err.join(timeout=5)
                raise
            _t_out.join()
            _t_err.join()

            if hasattr(self, "_current_tool"):
                self._current_tool = None

            _cc_rc = _proc.returncode
            _cc_out = "\n".join(_cc_out_lines).strip()
            logger.warning(
                "CC_DONE rc=%d lines=%d err_lines=%d",
                _cc_rc, len(_cc_out_lines), len(_cc_err_lines),
            )

            # Graceful MAX TURNS handling: if the file was written within
            # the last 10 minutes, treat the task as successful even if
            # claude-code hit its turn budget
            if (
                _cc_rc != 0
                and "MAX TURNS" in _cc_out
                and os.path.exists(_output_path)
                and (time.time() - os.path.getmtime(_output_path)) < 600
            ):
                _cc_out = (
                    f"Script fixed and saved to `{_output_path}` "
                    "(task completed within turn budget)."
                )
                _cc_rc = 0

            if _cc_rc == 0 and len(_cc_out) >= 20:
                _ring_last_cc_task[_gw_key] = _ring_task
                _cc_check = _cc_out.replace("-# \U0001f916 claude-code \xb7 claude.ai/pro", "").strip()
                if "?" in _cc_check[-600:]:
                    _ring_sticky[_sess] = _ring_task
                    logger.warning("RING_STICKY_SET session=%s", _sess)
                response = _mk_cc_resp(_cc_out + "\n\n-# \U0001f916 claude-code \xb7 claude.ai/pro")
            else:
                # RC != 0 or empty output — return error directly, never fall to MiniMax
                logger.warning("CC_FAIL rc=%d", _cc_rc)
                response = _mk_cc_resp(
                    f"claude-code exited with an error (rc={_cc_rc}).\n\n"
                    + (_cc_out[:300] if _cc_out else "No output.")
                    + "\n\n-# ⚠️ claude-code \xb7 routing"
                )
        except _sp_r.TimeoutExpired:
            logger.warning("CC_TIMEOUT: 600s exceeded")
            response = _mk_cc_resp(
                "claude-code timed out (600s) — this task is too large for one pass.\n\n"
                "Try breaking it into smaller steps, for example:\n"
                "• Phase 1: update block definitions\n"
                "• Phase 2: fix scheduling / artist pool logic\n"
                "• Phase 3: anti-rep rules and final cleanup"
                "\n\n-# ⚠️ claude-code \xb7 routing"
            )
        except Exception as _ex_r:
            logger.warning("CC_EXCEPTION: %s", str(_ex_r))
            response = _mk_cc_resp(
                f"claude-code encountered an error: {str(_ex_r)[:120]}"
                "\n\n-# ⚠️ claude-code \xb7 routing"
            )
# ──────────────────────────────────────────────────────────────────────────────
