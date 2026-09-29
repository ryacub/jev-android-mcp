# Jev Android MCP

Local, accessibility-first Android emulator control for any configured Android
application. The server observes the UIAutomator hierarchy and exposes bounded
MCP tools; Jev chooses one operation and one observed target per loop step.

## Target

- App: configured with `ANDROID_APP_PACKAGE`
- AVD: any online Android emulator
- Transport: local stdio
- Observation: UIAutomator/accessibility hierarchy only
- Screenshots: disabled by default and not sent to either model

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run mypy src
uv run jev-android-mcp
```

`android_observe` returns indexed accessibility state. `android_act` executes
one action from that exact observation ID, including taps, text entry, scroll,
Back, Home, and fixed portrait/landscape rotation. `android_run` runs a bounded
Jev loop. It independently verifies Home and orientation goals; application-
specific completion checks can be supplied by callers through `run_jev`.
Each run resolves its selected provider credentials once. Run traces include
decision, text-helper, action, wait, and post-action capture/parse timings; the
initial credential and hierarchy capture/parse timings appear in
`setup_timings_ms`.
`android_reset` requires `confirm=true` because it clears the configured app's
data. The configured app and known Android system surfaces are allowed; other
packages are rejected.

Configure the app under test before starting the server:

```bash
export ANDROID_APP_PACKAGE=com.example.app
```

For the Jev loop, OpenRouter is the default provider when configured:

```bash
export TYPESAFE_API_KEY=...
export TYPESAFE_MODEL=jev-latest
```

The TypeSafe key can also be stored as a `typesafe` API-key entry in the
Pi-compatible `auth.json`, or as the exact macOS Passwords/Keychain item named
`TYPESAFE_API_KEY`.

Select the backend explicitly with `JEV_BACKEND=openrouter` or
`JEV_BACKEND=typesafe`. The default `JEV_BACKEND=auto` prefers OpenRouter and
falls back to TypeSafe, so both keys can remain configured.

It uses the Luna family with high reasoning by default and keeps the same
operation and observed-target validation:

```bash
export OPENROUTER_API_KEY=...
export OPENROUTER_DECISION_MODEL=openai/gpt-5.6-luna
export OPENROUTER_REASONING_EFFORT=high
```

Provider lookup follows the local Pi convention: a `0600`
`~/.pi/agent/auth.json` entry under `openrouter` is used first, then
`OPENROUTER_API_KEY`, then the exact macOS Passwords/Keychain item named
`OPENROUTER_API_KEY`; TypeSafe follows the same order with `typesafe` and
`TYPESAFE_API_KEY`. Set `PI_CODING_AGENT_DIR` to use another Pi auth directory.
Literal and environment-backed auth-file keys are supported. The Pi macOS
form `!security find-generic-password ...` is supported through an allowlisted,
non-shell Keychain invocation; arbitrary auth-file commands are rejected.

`TYPE_TEXT` can use an explicit `text_by_resource_id` mapping on `android_run`;
the resource ID must identify one currently focused editable field. This avoids
the separate text-generation request. If no caller value is supplied, the
optional OpenRouter helper model (`inception/mercury-2.5`, configurable with
`OPENROUTER_TEXT_MODEL`) can provide a value from the goal. Supplied values are
not added to per-step trace fields, although visible text can appear in normal
observations and the returned final observation. The `wait_seconds` argument
defaults to 0.5 and can be set from 0 through 5 seconds.

The MCP process must keep stdout reserved for protocol traffic. Diagnostics
belong on stderr through Python logging.

For MCP Inspector development, launch the installed module rather than loading
the file directly so package imports remain valid:

```bash
uv run mcp dev -e . src/jev_android_mcp/server.py
```

The Python entry point is intentionally small so the same observation and
action seams can later be timed against a Codex Luna-high decision baseline.

The read-only matched-decision benchmark uses the same observation for each
provider and never executes the selected action:

```bash
uv run python -m jev_android_mcp.benchmark \
  --goal "Open the main screen" \
  --serial emulator-5554 \
  --repeats 3 \
  --backend openrouter \
  --condition cold \
  --condition warm \
  --warmup 1 \
  --output benchmarks/matched-decisions.jsonl
```

The summary is split by cold and warm condition. Warm trials reuse one HTTP/2
connection; cold trials intentionally do not. Decision latency is measured
separately from the shared Android hierarchy capture and action execution
costs. The `record_external_decision` helper validates Codex Luna-high records
against the same observation ID, fingerprint, operation, and action ID before
they enter a summary.

OpenRouter is the current default Jev-compatible backend. The direct TypeSafe
backend remains available when `TYPESAFE_API_KEY` is supplied; both providers
support the Pi/Oh My Pi-compatible auth file, environment, and macOS
Passwords/Keychain options described above.
