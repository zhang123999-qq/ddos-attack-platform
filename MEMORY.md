# MEMORY.md - Long-Term Memory

---

## ⚠️ Disclaimer

> **This file is the assistant's curated long-term memory.**  
> It is loaded **only in the main session** (direct 1-on-1 chats).  
> Never load or reference this file in shared contexts — it holds personal context and operational history that must not leak.  
> Entries are distilled wisdom, not raw logs. Accuracy is best-effort; verify critical facts independently.

---

## Identity & Role

| Field | Value |
|-------|-------|
| **Name** | ClawX (🐾) |
| **Creature** | Desktop AI Assistant |
| **Vibe** | Concise, capable, practical |
| **Workspace** | `C:\Users\Administrator\.openclaw\workspace` |

---

## Environment Baseline (2026-08-21)

| Component | Version / Detail |
|-----------|------------------|
| **OpenClaw** | 2026.7.1-2 (0790d9f) |
| **Model** | nvidia/nemotron-3-ultra-550b-a55b:free via custom-custom2c |
| **Model Endpoint** | https://api.006336.xyz/v1 |
| **Context Window** | 200k tokens |
| **Execution** | Elevated, full security, no approval prompts |
| **Core Skills** | docx, pdf, pptx, xlsx (all enabled) |
| **Plugin Skills** | browser-automation, canvas (linked) |
| **Browser** | Chrome at `C:\Program Files\Google\Chrome\Application\chrome.exe`, CDP port 18800 |

---

## Key Conventions Established

| # | Convention | Detail |
|---|------------|--------|
| 1 | **Memory structure** | Daily raw logs in `memory/YYYY-MM-DD.md`, curated wisdom in `MEMORY.md` |
| 2 | **Local tool notes** | Go in `TOOLS.md` (SSH, cameras, TTS preferences, etc.) |
| 3 | **HEARTBEAT.md** | Stays empty unless proactive checks are needed |
| 4 | **Configs** | Auto-backup to `.bak` and `.last-good` on write |
| 5 | **dir_list** | For remote nodes only; local FS uses `exec`/`read`/`write` |
| 6 | **Language** | Chinese (simplified) for user communication |
| 7 | **User** | 老板, Asia/Shanghai, 河南永城, prefers concise/practical responses |

---

## Skill Mastery Notes (2026-08-21)

| Skill | Creation | Editing | Validation | Key Gotchas |
|-------|----------|---------|------------|-------------|
| **docx** | docx-js (npm) | unzip → XML → zip | pandoc + soffice + pdftoppm | Tables need dual widths (DXA); bullets use numbering, not literal `•` |
| **pdf** | reportlab | pypdf / pdfplumber | — | Unicode sub/superscript → black boxes; OCR needs tesseract + pdf2image |
| **pptx** | pptxgenjs | unzip → XML → zip | `validate.py` (with `--original`) | Layout first (16:9 = 10×5.625"); hex no `#`; shadow offset ≥ 0; charts need dual axes |
| **xlsx** | openpyxl + pandas | openpyxl (keep_vba) | `recalc.py` (LibreOffice) | Two-pass load (data_only + formulas); `_xlfn.` prefix for 6 post-2007 funcs; no XLOOKUP/FILTER/UNIQUE |
| **browser-automation** | CDP/Playwright | N/A | — | Stable tab handles via label/suggestedTargetId; snapshot→act loop; aria refs durable; `profile="user"` only when login needed |
| **canvas** | HTML/CSS/JS | N/A | — | Gateway port 18789; routes under `/__openclaw__/canvas/`; host root `~/.openclaw/canvas`; liveReload enabled |

---

## Outstanding Setup Items

- [ ] Decide on heartbeat/cron schedule for proactive checks
- [ ] Add any SSH/camera/TTS/local device notes to `TOOLS.md`
- [ ] Fill in `USER.md` Context section with projects/interests

---

## Lessons Learned

- `dir_list` requires `node` param; local FS uses `exec` + `dir`/`read`/`write`
- Config writes create `.bak` and `.last-good` automatically
- Gateway SQLite in both `.openclaw/state/` and `.openclaw/agents/main/agent/`
- Memory files must be concrete updates, never empty placeholders

---