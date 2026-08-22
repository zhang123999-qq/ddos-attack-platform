# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup: camera names and locations, SSH hosts and aliases, preferred TTS voices, speaker/room names, device nicknames, anything environment-specific.

---

## ⚠️ Disclaimer

> **This file documents local environment configuration for the AI assistant.**  
> It contains paths, versions, and credentials references (not actual secrets).  
> Do not share this file externally. Actual secrets (API keys, tokens) are stored in `auth-profiles.json` and `openclaw.json` — never commit those.

---

## Environment

| Item | Value |
|------|-------|
| **OS** | Windows 10.0.26200 (x64) |
| **Shell** | PowerShell |
| **Node** | v24.15.0 |
| **Python** | Available via system PATH (prefer `uv`) |

---

## Browser

| Item | Value |
|------|-------|
| **Chrome** | `C:\Program Files\Google\Chrome\Application\chrome.exe` |
| **CDP Port** | 18800 (OpenClaw managed profile) |
| **Profile** | `"openclaw"` (isolated, not user profile) |

---

## OpenClaw Paths

| Path | Purpose |
|------|---------|
| `C:\Users\Administrator\.openclaw\openclaw.json` | Config |
| `C:\Users\Administrator\.openclaw\workspace` | Workspace |
| `C:\Users\Administrator\.openclaw\state\openclaw.sqlite` | State DB |
| `C:\Users\Administrator\.openclaw\agents\main\agent\openclaw-agent.sqlite` | Agent DB |
| `C:\Users\Administrator\.openclaw\logs\config-audit.jsonl` | Logs |
| `C:\Users\Administrator\.openclaw\skills\` | Core skills (docx, pdf, pptx, xlsx) |
| `C:\Users\Administrator\.openclaw\plugin-skills\` | Plugin skills (browser-automation, canvas) |

---

## Model Provider

| Field | Value |
|-------|-------|
| **Provider** | custom-custom2c |
| **Base URL** | https://api.006336.xyz/v1 |
| **Model** | nvidia/nemotron-3-ultra-550b-a55b:free |
| **Context** | 200k tokens |
| **API Key** | `sk-14c...891f` (stored in `auth-profiles.json`) |

---

## Gateway

| Field | Value |
|-------|-------|
| **Port** | 18789 |
| **Mode** | local |
| **Auth** | token (`clawx-...81c6`) |
| **Control UI** | http://127.0.0.1:18789, http://localhost:18789 |

---

## Execution

| Setting | Value |
|---------|-------|
| **Security** | full |
| **Ask** | off |
| **Elevated** | available |

---

## Notes

- `dir_list` tool is for remote nodes only; use `exec` + `dir` / `read` / `write` for local filesystem
- Config auto-backups: `.bak` and `.last-good` created on write
- Browser not running by default; starts on first `browser` tool call
- No cron jobs configured yet
- HEARTBEAT.md is empty template (no proactive checks scheduled)
- USER.md needs human details filled in

---

<!-- clawx:begin -->
## ClawX Tool Notes

### uv (Python)

- `uv` is bundled with ClawX and on PATH. Do NOT use bare `python` or `pip`.
- Run scripts: `uv run python <script>` | Install packages: `uv pip install <package>`

### Browser

- `browser` tool provides full automation (scraping, form filling, testing) via an isolated managed browser.
- Flow: `action="start"` → `action="snapshot"` (see page + get element refs like `e12`) → `action="act"` (click/type using refs).
- Open new tabs: `action="open"` with `targetUrl`.
- To just open a URL for the user to view, use `shell:openExternal` instead.
- If a browser action fails, transient errors (timeout, network) can often be resolved by retrying once or navigating to a different URL.
- When asked to search, look up, or interact with a web page, use the browser tool. Do not substitute with guesses or training data when real-time web access is requested.
<!-- clawx:end -->