# 4. A menu bar item built from an AppleScriptObjC applet

- Status: accepted
- Date: 2026-11-24

## Context

M5 puts flib in the menu bar (#116): an icon whose pose follows flib's state
(#112), a short menu, launch at login, never a Dock icon. It has to be built
from what ships with macOS, like the notifier app (#106): no third-party
package, no Python GUI dependency (PyObjC, rumps), no Xcode project. The
spike (#111) built the same item two ways and measured both on macOS 27.2
(Apple silicon), as `flib-menu-test` with temporary bundle identifiers, under
`~/Library/Application Support/io.flibtest.menu/` (apps under `/tmp` are
treated as temporary). The code is in
[`prototypes/menu-bar/`](../../prototypes/menu-bar/README.md).

1. **Applet**: an AppleScriptObjC script (`use framework "AppKit"`) compiled
   with `osacompile -s` (stay open). It creates an `NSStatusItem` with an
   `NSMenu` whose items target the script itself (`setTarget:me`), and
   re-reads the state in its `idle` handler.
2. **Swift**: one source file compiled at install time with
   `swiftc -O -framework AppKit`, which needs the Command Line Tools
   (`xcode-select -p`). It watches the state folder with kqueue
   (`DispatchSource`), observes wake, and refuses to run twice.

Both read the same contract: flib writes `state.json` atomically
(`{"pose": …, "count": …, "line": …}`) into a folder named by a key in the
bundle's `Info.plist`; icons are SVGs in `Contents/Resources`, loaded as
template images (AppKit reads SVG natively); a menu click runs a fixed
executable with fixed arguments (`NSTask`/`Process`), never a shell string.
Both get `LSUIElement`, an ad hoc signature and a build in a temporary folder
swapped in only when complete, as the notifier does.

| Measured | Applet (`osacompile`) | Swift (`swiftc`) |
| --- | --- | --- |
| Needs | nothing beyond macOS | Command Line Tools |
| Build time | 0.5 s | 6.5–11.4 s |
| Bundle size | 208 KB | 152 KB (123 KB binary) |
| Launch to item shown | ~3 s | 0.3 s |
| Idle CPU, 300 s window | 0.21 s CPU = 0.07 % | 0.18 s CPU = 0.06 % |
| Memory (physical footprint) | 49 MB | 14 MB |
| State file change to new icon | next idle tick (≤ 30 s); immediate with the `open -g` nudge | ~10 ms (kqueue) |
| Template icon, light / dark menu bar | tinted correctly (rendered ink 0 / 254) | same |
| Menu item fires its action | yes | yes |
| Shown in the Dock | no (`UIElement`) | no (`UIElement`) |
| Started by launchd (direct exec) | works, keeps its bundle identity | same |
| Killed (crash) under the LaunchAgent | restarted after ~10 s | same |
| Quit from its menu | exit 0, launchd leaves it off | same |
| Bundle rebuilt while running | old process keeps running; `open` reuses it; `launchctl kickstart -k` picks up the new build | same |
| Second launch | Launch Services reuses the running one | reused; with `open -n`, the second copy sees the first and quits |

How it was measured: CPU is the process's CPU time over a 300 s idle window
(`ps -o time`), memory is `footprint`'s physical footprint (RSS was
misleading: both dropped to ~14 MB resident after the system compressed
them). Light and dark rendering was checked inside the app by drawing the
status button with `aqua` and `darkAqua` appearances into a bitmap; the
maintainer's own appearance was never changed. Menu actions were fired
through `NSMenu.performActionForItem(at:)`, the same target/action path a
click takes; a physical click was not automated because that needs
Accessibility access the test harness does not have. The launchd tests loaded
a LaunchAgent plist from the test folder, not from `~/Library/LaunchAgents`.

What failed or surprised:

- `swiftc` left alone built a binary with a minimum macOS of 28.0 on a 27.2
  Mac (the SDK's version), and Launch Services refused to open it (-10825).
  An explicit `-target <arch>-apple-macos26.0` fixed it. On the same Mac
  `xcrun` looked for an SDK under the Command Line Tools while `xcode-select`
  pointed at Xcode: install-time compilation depends on toolchain state flib
  does not control.
- On a Mac without the Command Line Tools, `/usr/bin/swiftc` is a shim that
  opens the "install developer tools" dialog. A Swift path must check
  `xcode-select -p` first and never call the shim blind.
- AppleScriptObjC cannot pass blocks, so the applet cannot use kqueue,
  `DispatchSource` or completion handlers; it re-reads on `idle` instead. A
  reopen event fixes the latency: running `open -g <app>` while the applet
  runs calls its `reopen` handler (measured: refreshed within ~0.1 s), with
  no Automation permission involved because Launch Services sends the event.
- AppleScript reserves words that ObjC uses (`button`, `display`, `bounds`,
  `idle`): they need renaming or `|pipes|`. C functions such as
  `NSStringFromRect` cannot be called.
- JXA (`osacompile -l JavaScript`) was not built: it runs on the same OSA
  runtime and bridge as AppleScriptObjC and needs `ObjC.registerSubclass` for
  menu targets, so it offers nothing the applet lacks.

## Decision

**The menu bar item is an AppleScriptObjC stay-open applet built with
`osacompile`**, like the notifier. It works on every Mac flib supports with
nothing installed, builds in half a second, and its idle cost is the same as
the Swift build's. It is a separate bundle from the notifier
(`io.github.luizpereiradev.file-librarian.menu`), because a stay-open applet
and a post-and-quit one cannot be the same process.

**Fallback: the Swift source**, kept in `prototypes/menu-bar/swift/`, with the
same state contract, `Info.plist` key, icons and LaunchAgent. It is not
shipped. It replaces the applet only if AppleScriptObjC stops working on a new
macOS, or if #116 needs an API the applet cannot reach (anything that takes a
block); in that case `flib menu --install` compiles it when `xcode-select -p`
succeeds, with an explicit `-target`, and otherwise says the Command Line
Tools are needed. Keeping one shipped mechanism avoids testing every menu
feature twice. When the item cannot be built or run, nothing else changes:
`flib status` and the notifications keep working, and `flib doctor` says why.

**State.** flib writes `menu-state.json` atomically (temporary file, rename)
in its own state folder whenever its status changes: after a run, a watch
event, an undo, a pause. The JSON carries the pose, the count and a line of
text, counts only and never file names. The applet only reads it; it never
runs Python on a timer and never parses `flib status` output. After writing,
flib nudges the applet with `open -g <app>` **only when it is running**
(`open` would otherwise start an app the user quit). The applet's `idle`
handler re-reads every 30 s as a safety net, which only checks the
modification date. Wake needs nothing special: the next idle tick or nudge
catches up.

**Install, update, removal** (`flib menu --install/--remove`, #116):

- build into flib's environment next to the notifier (`<env>/bin/`), never
  /Applications; stamp, ad hoc signature and `lsregister -f` as in
  `notifier.py`; the state folder path goes into `Info.plist` at build time;
- a LaunchAgent `io.github.luizpereiradev.file-librarian.menu` in
  `~/Library/LaunchAgents` with `Program` set to the bundle's executable,
  `RunAtLoad`, `KeepAlive = {SuccessfulExit = false}` (back after a crash,
  off after Quit until the next login), `LimitLoadToSessionType = Aqua` and
  `ProcessType = Interactive` (it draws UI; `Background` would throttle it);
- update (`flib schedule --refresh`, `scripts/install.sh`): rebuild when the
  stamp no longer matches, swap the bundle, then `launchctl kickstart -k` the
  agent if it is loaded; a plain `open` keeps the old process;
- removal: `launchctl bootout`, delete the plist, `lsregister -u`, delete the
  bundle.

## Consequences

- No new dependency and no compiler: the item works on a clean Mac, and the
  build reuses the notifier's code path (osacompile, plist edits, codesign).
- The applet uses about 35 MB more memory than a compiled binary (49 MB
  footprint versus 14 MB) and starts in ~3 s instead of 0.3 s. Acceptable for
  one process; revisit if users notice.
- Everything the applet does runs on the main thread: a handler that waits
  freezes the menu. #116 must start flib commands with `NSTask` and never
  wait for them, and wrap every handler in `try`, as an AppleScript error
  would otherwise show a dialog.
- No Apple events to the applet from flib or the terminal: they would ask
  for Automation permission. The nudge goes through `open` instead.
- Open risks, not verified in the spike: a real login (the agent was loaded
  with `launchctl bootstrap`, which is what launchd does at login); a real
  sleep and wake (the Mac was not put to sleep); a physical click; whether a
  ⌘-dragged position survives a rebuild (`autosaveName` is set in the Swift
  version, not yet in the applet); macOS may hide the item when the menu bar
  is crowded or behind the notch, or when the user turns it off under Menu
  Bar settings, which flib cannot detect from outside; installing the agent
  shows macOS's one-time "Background Items Added" notice, as the schedule's
  agents already do.
- If Apple deprecates AppleScriptObjC, the Swift fallback is ready, at the
  price of requiring the Command Line Tools.
