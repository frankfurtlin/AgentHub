# AgentHub

**A floating capybara on your desktop that knows which AI coding tool is waiting for you.**

[中文文档](README_CN.md) · [Download latest build](https://github.com/frankfurtlin/AgentHub/releases/latest)

---

## The problem

You run four GUI-based AI coding assistants at once. They don't work badly — the problem
is you **can't tell which one is currently stuck waiting for you to click something**.

CodeBuddy's permission dialog, Trae's "waiting for input", Qoder's authorization request —
each one lives inside its own window. You don't see them unless you switch over, and
switching over breaks whatever you were doing.

AgentHub sits in a corner of your desktop and tells you who needs you. Click the card,
jump straight to that window.

```
                 ┌──────────────────────────────┐
   floating      │  AgentHub          3/7 running│  ← click ball to open
   ball          │ ────────────────────────────  │
   (snaps        │  [W] WorkBuddy   needs you  › │  ← click card to jump
    to edge)     │  [D] DeepSeek    idle · tray › │
    ●●●●●●●      │  [C] CodeBuddy   streaming  › │      one dot per tool,
                │  [T] Trae CN        not run  › │      colour = state
                │  [Q] Qoder CN       not run  › │
                │ ────────────────────────────  │
                │  1 tool needs you             │
                └──────────────────────────────┘
```

---

## Why it works

### Two independent detection channels

Relying on system notifications alone **does not work**. On this machine, CodeBuddy and
Qoder had exactly **0 records** in the notification database: they register a handler
but only send notifications when their window is *unfocused* — while the approval
dialog only appears when you're *looking at* the window. Logically deadlocked.

So AgentHub also reads the target UI **directly** via UI Automation, scanning for
approval bars sitting on screen.

| Channel | How | Reality check |
|---|---|---|
| System notifications | Reads the notification centre's SQLite store | CodeBuddy / Qoder: 0 records. Dead end on its own |
| UI probe | UI Automation reads whether an approval bar is on screen | Covers the two above; ~80 ms per scan, so it runs on its own 2.5 s thread instead of riding the main loop |

Either one saying "waiting" counts. Union, not intersection.

### It only speaks up when it should

Alerts fire on the **edge** — the moment a tool transitions from "not waiting" → "waiting" —
and once per transition. Not every scan cycle. Switching windows will not get you spammed.

### It stays out of the way

No background service, no registry entries, no drivers. Config is a single JSON file.
The window is removed from the taskbar (it's a tool window); the entry point lives in
the system tray.

---

## Quick start

### Option A — download a build (no Python needed)

Grab the zip for your platform from
[the latest release](https://github.com/frankfurtlin/AgentHub/releases/latest), unzip,
run it.

> **Windows** needs the WebView2 Runtime. Windows 11 ships with it; on Windows 10 it
> normally came along with Edge. It's a system component, so it isn't bundled.
>
> **macOS**: the build is unsigned, so the first launch needs a right-click → Open
> (Gatekeeper). Also note the macOS backend is still a stub — see
> [Platform status](#platform-status).

### Option B — run from source

```bash
git clone https://github.com/frankfurtlin/AgentHub.git
cd AgentHub

python -m venv .venv
.venv\Scripts\activate            # macOS: source .venv/bin/activate
pip install -e .
python -m agenthub
```

Want to see what it reads without opening any window?

```bash
python -m agenthub --once
```

```
平台：windows  能力：{'platform': 'windows', 'uiProbe': True, 'notifications': True, 'icons': True}
扫描耗时 418ms
WorkBuddy      有新结果     cpu=  0.0%    任务已完成
DeepSeek Harness 空闲中      cpu=  0.0% · 托盘
CodeBuddy CN   空闲中      cpu=  0.0%
Trae CN        未启动      cpu=  0.0%
Qoder CN       未启动      cpu=  0.0%
Claude         未启动      cpu=  0.0%
Codex          未启动      cpu=  0.0%
合计：{'running': 3, 'waiting': 0, 'unread': 1}
```

---

## Supported tools

Only tools **actually installed on this machine** are shown. Install three of the
seven, and the panel shows three cards — not a row of permanently greyed-out entries.

| Tool | How state is decided | How "installed" is detected |
|---|---|---|
| CodeBuddy CN | UI probe | Registry uninstall entry / Start Menu shortcut |
| Trae CN | Notifications + CPU | Same |
| Qoder CN | UI probe | Same |
| WorkBuddy | Notifications | Same |
| Claude | Notifications | Store (MSIX) build — only visible via `AppModel\Repository\Packages` |
| DeepSeek Harness | Notifications | Registry + tray presence |
| Codex | Notifications | CLI, no installer — probe for `codex.exe` |

Detection takes the **union of four sources**: registry uninstall keys
(`HKCU` / `HKLM`, plus the WOW6432Node view), Start Menu `.lnk` names, MSIX package
names, and a few executable probes. A tool that is **currently running always counts as
installed** — green builds and CLI tools leave no installation trace at all.
Results are cached for 5 minutes, so installing something new doesn't require a restart.

### Adding a tool

Add one entry to `AGENTS` in [`agenthub/models.py`](agenthub/models.py): process name
for matching, brand colours for the icon backing, `ui_probe=True` if it shows approval
bars, and `install_hints` (lowercase fragments that appear in its installer name or
shortcut).

---

## Usage

| Action | Result |
|---|---|
| Click the ball | Toggle the panel |
| Hold and drag the ball | Move it (needs >4 px to count as a drag, so clicks don't misfire) |
| Click a card | Bring that tool's window to the front (works from minimized / tray) |
| `Esc` or the × | Collapse the panel |
| Tray icon · left click | Toggle the panel |
| Tray icon · right click | Show/hide panel, quit |

The panel snaps next to whichever edge the ball is closest to, and its **height follows
its content** (clamped to 220–900 px, plus a screen-height guard). Position is remembered
across restarts.

### States

| State | Level | Meaning |
|---|---|---|
| 待审批 / needs you | 2 | Something is sitting there waiting for you to confirm |
| 有新结果 / finished | 1 | A task finished, results are waiting |
| 输出中 / streaming | 0 | Running and burning CPU |
| 空闲中 / idle | 0 | Running, not busy |
| 未启动 / not running | 0 | Process not found |

Secondary hints append to the state: `· minimized`, `· tray`.

---

## How it's built

```
models.py      data model + text heuristics (no syscalls, shared across platforms)
runtime.py     state hub: poll → merge both channels → detect alert edges → snapshot
app.py         pywebview shell: two frameless transparent windows + JS bridge
webview_env.py environment setup before WebView starts (one step is mandatory, see docs)
tray.py        system tray icon
host/base.py   platform abstraction (Backend / UiProbeSession / WindowInfo)
host/windows/  Win32 backend: winapi (ctypes), uia (probe), icons, notify, installed
web/           plain HTML/CSS/JS, no build step
```

Three layering rules:

1. **Everything platform-specific stays inside `host/`.** `Backend` is an abstract base
   class and the layers above only know that interface. Windows implements it; a macOS
   backend just fills in the same class and nothing above changes.
2. **`models.py` contains no system calls.** Notification classification (`classify()`)
   and "is it waiting" (`AgentState.waiting`) are pure functions — testable, reusable.
3. **State is computed in Python; the frontend only renders.** The frontend receives a
   snapshot and makes no decisions, so swapping the UI later doesn't mean rewriting logic.

### What's under the hood

- **UI**: pywebview over the system WebView2 Runtime — no bundled browser, no Electron.
- **Windows internals**: pure `ctypes`. `EnumWindows`, `SetWindowLongPtr` (`GWLP_WNDPROC`
  subclassing for frame-accurate drag following), DWM glass for real transparency,
  `Shell_NotifyIcon` for the tray.
- **UIA probe**: runs on its own thread because COM objects are thread-affine.

---

## Configuration

Stored at `%APPDATA%\AgentHub\config.json`
(`~/Library/Application Support/AgentHub/config.json` on macOS), created on first run.
It is **not** tracked by git — paths and coordinates are machine-specific.
See [`config.example.json`](config.example.json) for a template.

```jsonc
{
  "exePaths": {},        // pin a tool's executable; empty = find it from running processes
  "ballPos": null,       // [x, y]; null = snap to bottom-right
  "refreshMs": 1500,     // main scan interval
  "probeIntervalS": 2.5, // UI probe interval (~80 ms each, no need to match the loop)
  "theme": "auto",       // auto follows the system / light / dark
  "sound": true,         // chime when someone needs you
  "onTop": true,
  "webviewArgs": []      // extra WebView2 flags, usually left alone
}
```

---

## Platform status

| | |
|---|---|
| Windows | ✅ Full — processes, windows, UIA probe, notifications, icons, tray, autostart |
| macOS | 🚧 Interface is in place; `agenthub/host/macos/` is a stub with a TODO list at the top of the file |

The Windows backend is complete. The macOS one needs five pieces filled in
(windows / AX probe / notification DB / icons / misc) — the todo list in
`agenthub/host/macos/__init__.py` spells out each with the APIs to use.

---

## Troubleshooting

**Panel and ball won't move smoothly, or icons are blank, or clicking does nothing.**
Run the low-level self-check first — it separates the detection layer from the UI layer:

```bash
python tools/selftest.py        # 11 checks; all PASS = problem is in the WebView layer
python tools/probe_uia.py       # what the UI probe actually reads from each tool
```

If the self-check passes but the UI is wrong, see
[`docs/webview2-notes.md`](docs/webview2-notes.md).

**Autostart:** `python tools/install_startup.py on|off|status`

---

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/ -q        # 22 cases, no GUI needed
python -m pytest tests/test_x.py  # or run a single file directly
```

Packaging: `python tools/build.py --clean --zip`
→ see [`docs/packaging.md`](docs/packaging.md).

Every push to `main` triggers a build for Windows and macOS and republishes the
`latest` release — see [`.github/workflows/build.yml`](.github/workflows/build.yml).

Contributions: [`CONTRIBUTING.md`](CONTRIBUTING.md).

---

## About the capybara

The capybara on the ball is an **original SVG** (`web/index.html`, template `tpl-ball`),
not an existing character.

Worth spelling out, because it always comes up: **could we use the 水豚噜噜 design?**
No. Popular capybara characters are commercial IP with rights holders (噜噜 belongs to
铁罐文化, currently doing blind boxes and paid collaborations). Drawing one into an MIT
open-source project carries clear risk. But **the species itself isn't copyrightable**, so
drawing an independent capybara from species traits — round face, small round ears, broad
muzzle, black bean eyes — is both clean and just as cute. That's this one.

To customise:

- **Colours**: all in `web/tokens.css` under `--capy-*`, one set each for light and dark.
- **Shapes**: the two `<svg>` blocks (`tpl-ball`, `tpl-panel`) in `web/index.html`.
- **Expression**: the ball reacts to state — level 1 widens the eyes 6%, level 2 by 17%
  (`.ball[data-level="1"] / [data-level="2"]` → `.capy__eye`). It also blinks every 6.4 s.

⚠️ **A trap you will hit when restyling**: the three `<stop>` colours in the fur gradient
**must come from CSS classes**, never `stop-color="var(--capy-hi)"`. SVG `<stop>` elements
do not resolve CSS variables inside presentation attributes, so the gradient silently
fails — and "fails" means the circle referencing it **is not drawn at all**, with
nothing printed to the console. (`<circle fill="var(--x)">` is fine; only `<stop>` breaks,
which is exactly what makes it confusing.)

⚠️ **Never use `#000000` in the UI**: the transparency is DWM glass, whose key colour is
black. Pure black pixels are keyed out and become transparent. Semi-transparent shadows
are fine — they never composite to pure black.

---

## License

MIT
