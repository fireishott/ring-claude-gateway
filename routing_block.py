"""
Routing block for run_agent.py — run_conversation() integration.

Paste this block inside run_conversation(), inside the
`while retry_count < max_retries:` loop, before the primary LLM call.
Requires ring_router.py symbols at module level.

Prerequisites already in scope: api_messages, retry_count, self, os, time,
SimpleNamespace, json, logging, _sp_r (subprocess), _threading_r (threading),
_uuid_r (uuid). ring_router symbols imported at module level.

Routes:
  claude-code  → CC CLI, max 50 turns, superpowers enabled
  ask_claude   → CC CLI, max 20 turns, reasoning mode
  minimax      → primary LLM (falls through to normal API call)

Log markers (grep in journalctl):
  RING_ROUTER_FIRED     Ring classifier invoked
  RING_ROUTER_RESULT    Ring's decision (route + task)
  RING_ROUTER_ERR       Ring failed (bad_json, timeout, api_err, no_key)
  RING_STICKY_RESUME    Sticky session resumed, Ring skipped
  RING_STICKY_SET       Sticky set (CC/ask_claude asked a question)
  RING_ALWAYS_OVERRIDE  Keyword net forced claude-code
  RING_CONTEXT_OVERRIDE Context net forced claude-code
  RING_MM_OVERRIDE      MiniMax fallback classifier overrode to CC/ask_claude
  RING_MM_RESULT        MiniMax fallback decision
  RING_MM_ERR           MiniMax fallback failed
  CC_DISPATCH           claude-code subprocess launched
  AC_DISPATCH           ask_claude subprocess launched
  CC_OUT / CC_ERR       Live subprocess output lines
  CC_DONE               Subprocess finished (rc, line counts)
  CC_FAIL               Subprocess non-zero exit
  CC_TIMEOUT            600s wall-clock exceeded
  CC_EXCEPTION          Unexpected error in routing block
"""

# Only run on the first attempt — not on MiniMax validation retries
if retry_count == 0 and getattr(self, 'platform', None):

    # ── Extract last user message ─────────────────────────────────────────
    _last_content = ""
    for _m in reversed(api_messages):
        if _m.get('role') == 'user':
            _ac = _m.get('content', '')
            if isinstance(_ac, list):
                _last_content = ' '.join(
                    p.get('text', '') for p in _ac
                    if isinstance(p, dict) and p.get('type') == 'text'
                )
            else:
                _last_content = _ac or ''
            break

    # ── Extract prior assistant text for follow-up context ───────────────
    _prior_text = ""
    for _m in reversed(api_messages):
        if _m.get('role') == 'assistant':
            _ac = _m.get('content', '')
            if isinstance(_ac, list):
                _prior_text = ' '.join(
                    p.get('text', '') for p in _ac
                    if isinstance(p, dict) and p.get('type') == 'text'
                )
            else:
                _prior_text = _ac or ''
            break

    _sess = getattr(self, 'session_id', '') or ''
    _gw_key = getattr(self, '_gateway_session_key', '') or _sess
    _prior_cc = _ring_last_cc_task.get(_gw_key, '')

    # ── Sticky resume — skip Ring if CC/ask_claude asked a question ───────
    _sticky_entry = _ring_sticky.pop(_sess, None)
    if _sticky_entry:
        # Stored as (route, task) tuple; legacy strings default to claude-code
        if isinstance(_sticky_entry, tuple):
            _ring_route, _sticky_task = _sticky_entry
        else:
            _ring_route, _sticky_task = 'claude-code', _sticky_entry
        _ring_task = _sticky_task + f'\n\nUser replied: {_last_content}'
        logger.warning('RING_STICKY_RESUME session=%s route=%s', _sess, _ring_route)
    else:
        # ── Ring classification ───────────────────────────────────────────
        logger.warning('RING_ROUTER_FIRED len=%d prior=%d cc=%d',
                       len(_last_content), len(_prior_text), len(_prior_cc))
        _ring_key = os.getenv('OPENROUTER_API_KEY', '')
        _ring_route, _ring_task, _ring_err = _ring_classify(
            _last_content, _ring_key, _prior_text, _prior_cc
        )

        # ── Keyword safety nets (override deliberate minimax decisions) ───
        if _ring_route == 'minimax':
            if _CC_ALWAYS_R.search(_last_content):
                _ring_route = 'claude-code'
                _ring_task = _last_content + (
                    f'\n\nPrior context: {_prior_cc[:200]}' if _prior_cc else ''
                )
                logger.warning('RING_ALWAYS_OVERRIDE task=%s', _ring_task[:80])
            elif _prior_cc and _CC_CONTEXT_R.search(_last_content):
                _ring_route = 'claude-code'
                _ring_task = _last_content + f'\n\nPrior context: {_prior_cc[:200]}'
                logger.warning('RING_CONTEXT_OVERRIDE task=%s', _ring_task[:80])
            elif _ring_err:
                # Ring errored — try MiniMax-M2.7 as second-chance classifier
                _mm_key = os.getenv('MINIMAX_API_KEY', '')
                _mm_route, _mm_task = _minimax_classify(
                    _last_content, _mm_key, _prior_text, _prior_cc
                )
                if _mm_route in ('claude-code', 'ask_claude') and _mm_task:
                    _ring_route = _mm_route
                    _ring_task = _mm_task
                    logger.warning('RING_MM_OVERRIDE route=%s task=%s',
                                   _ring_route, _ring_task[:80])

    # ── Dispatch to CC/ask_claude subprocess ─────────────────────────────
    if _ring_route in ('claude-code', 'ask_claude') and _ring_task:

        # Document path injection (claude-code only)
        if _ring_route == 'claude-code' and '/path/to/cache/documents/' in _ring_task:
            # Adjust cache path and output path to match your deployment
            _ring_task += ' Save corrected version to /path/to/cache/output.py'

        def _mk_cc_resp(_text):
            """Build a synthetic response object so MiniMax never re-handles a CC turn."""
            if getattr(self, 'api_mode', '') == 'anthropic_messages':
                return SimpleNamespace(
                    id='route-' + str(_uuid_r.uuid4()),
                    model=self.model,
                    content=[SimpleNamespace(type='text', text=_text)],
                    stop_reason='end_turn',
                    usage=SimpleNamespace(input_tokens=0, output_tokens=0),
                )
            return SimpleNamespace(
                id='route-' + str(_uuid_r.uuid4()),
                model=self.model,
                choices=[SimpleNamespace(
                    index=0,
                    message=SimpleNamespace(
                        role='assistant',
                        content=_text,
                        tool_calls=None,
                        reasoning_content=None,
                    ),
                    finish_reason='stop',
                )],
                usage=SimpleNamespace(
                    prompt_tokens=0, completion_tokens=0, total_tokens=0,
                ),
            )

        try:
            # Strip ANTHROPIC_API_KEY so claude-code uses Pro OAuth credentials
            _cc_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

            _sp_plugin = os.path.expanduser('~/.claude/plugins/cache/claude-plugins-official')
            _sp_ok = os.path.isdir(_sp_plugin)
            _dispatch_label = 'CC_DISPATCH' if _ring_route == 'claude-code' else 'AC_DISPATCH'
            logger.warning('%s task=%s superpowers=%s',
                           _dispatch_label, _ring_task[:120], 'YES' if _sp_ok else 'NOT_FOUND')

            _cc_out_lines: list = []
            _cc_err_lines: list = []

            if hasattr(self, '_current_tool'):
                self._current_tool = _ring_route
            if hasattr(self, '_touch_activity'):
                self._touch_activity(f'{_ring_route}: {_ring_task[:80]}')

            def _cc_activity_hint(_ln):
                _l = _ln.strip()
                if 'superpowers:' in _l or 'Using ' in _l[:30]:
                    return _l[:80]
                for _kw in ('Reading', 'Writing', 'Edit', 'Bash', 'Search',
                            'Analyzing', 'Building', 'Planning', 'Implementing',
                            'Testing', 'Fixing', 'Creating', 'Saving'):
                    if _l.startswith(_kw):
                        return _l[:80]
                if _l and _l[0] in ('✓', '●', '⚡', '→', '▶'):
                    return _l[:80]
                return None

            _cc_progress_cb = getattr(self, 'tool_progress_callback', None)

            def _pipe_reader(_pipe, _lines, _prefix):
                try:
                    from hermes_logging import set_session_context as _set_sc
                    _set_sc(_sess)
                except Exception:
                    pass
                for _ln in iter(_pipe.readline, ''):
                    _ln = _ln.rstrip('\n')
                    if not _ln:
                        continue
                    logger.warning('%s %s', _prefix, _ln[:300])
                    _lines.append(_ln)
                    if _prefix == 'CC_OUT:':
                        _hint = _cc_activity_hint(_ln)
                        if _hint:
                            if hasattr(self, '_touch_activity'):
                                self._touch_activity(f'{_ring_route}: {_hint}')
                            if _cc_progress_cb:
                                try:
                                    _cc_progress_cb(
                                        event_type='tool.started',
                                        tool_name=_ring_route,
                                        preview=_hint,
                                    )
                                except Exception:
                                    pass
                _pipe.close()

            _max_turns = '50' if _ring_route == 'claude-code' else '20'
            _proc = _sp_r.Popen(
                ['/home/fihadmin/.local/bin/claude-code',
                 '-p', _ring_task,
                 '--dangerously-skip-permissions',
                 '--max-turns', _max_turns],
                stdout=_sp_r.PIPE, stderr=_sp_r.PIPE,
                text=True, stdin=_sp_r.DEVNULL,
                env=_cc_env,
            )
            _t_out = _threading_r.Thread(
                target=_pipe_reader, args=(_proc.stdout, _cc_out_lines, 'CC_OUT:'), daemon=True)
            _t_err = _threading_r.Thread(
                target=_pipe_reader, args=(_proc.stderr, _cc_err_lines, 'CC_ERR:'), daemon=True)
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

            if hasattr(self, '_current_tool'):
                self._current_tool = None

            _cc_rc = _proc.returncode
            _cc_out = '\n'.join(_cc_out_lines).strip()
            logger.warning('CC_DONE rc=%d lines=%d err_lines=%d',
                           _cc_rc, len(_cc_out_lines), len(_cc_err_lines))

            if _cc_rc == 0 and len(_cc_out) >= 20:
                if _ring_route == 'claude-code':
                    _ring_last_cc_task[_gw_key] = _ring_task
                _cc_footer = ('⚙️ claude-code · claude.ai/pro'
                              if _ring_route == 'claude-code'
                              else '🧠 ask_claude · claude.ai/pro')
                _cc_check = _cc_out.replace(f'-# {_cc_footer}', '').strip()
                if '?' in _cc_check[-600:]:
                    _ring_sticky[_sess] = (_ring_route, _ring_task)
                    logger.warning('RING_STICKY_SET session=%s route=%s', _sess, _ring_route)
                _route_summary = f'-# 🔀 Ring → {_ring_route} · {_ring_task[:100]}\n\n'
                response = _mk_cc_resp(_route_summary + _cc_out + f'\n\n-# {_cc_footer}')
            else:
                _fail_detail = _cc_out[:300] if _cc_out else 'No output.'
                logger.warning('CC_FAIL rc=%d', _cc_rc)
                response = _mk_cc_resp(
                    f'claude-code exited with an error (rc={_cc_rc}).\n\n{_fail_detail}'
                    '\n\n-# ⚠️ claude-code · routing'
                )
        except _sp_r.TimeoutExpired:
            logger.warning('CC_TIMEOUT: 600s exceeded task=%s', _ring_task[:80])
            _ring_sticky[_sess] = (_ring_route, _ring_task)
            logger.warning('RING_STICKY_SET session=%s route=%s (timeout recovery)', _sess, _ring_route)
            response = _mk_cc_resp(
                'claude-code timed out (600s) — try breaking the task into smaller steps.'
                '\n\n-# ⚠️ claude-code · routing'
            )
        except Exception as _ex_r:
            logger.warning('CC_EXCEPTION: %s', str(_ex_r))
            response = _mk_cc_resp(
                f'claude-code encountered an error: {str(_ex_r)[:120]}'
                '\n\n-# ⚠️ claude-code · routing'
            )
