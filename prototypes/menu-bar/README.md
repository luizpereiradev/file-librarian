# Menu bar item: spike prototypes (not shipped)

Measured in #111; the findings and the choice are in
[ADR 0004](../../docs/decisions/0004-menu-bar-item.md). #116 starts from
`applet/menu.applescript` (the chosen mechanism); `swift/main.swift` is the
measured alternative, kept as the fallback the ADR describes.

| File | What |
| --- | --- |
| `applet/menu.applescript` | AppleScriptObjC stay-open applet: status item, template icon per pose, count, menu with actions, idle re-read and a reopen nudge |
| `swift/main.swift` | The same item in Swift (needs the Command Line Tools): kqueue on the state folder, wake observer, single-instance guard |
| `icons/*.svg` | Placeholder template icons, one per pose (#112 draws the real ones) |
| `build.sh` | Builds either into `~/Library/Application Support/io.flibtest.menu/` |
| `agent.plist` | The LaunchAgent that was measured (`APP` is replaced with the executable) |
| `measure.sh` | Idle CPU time and memory footprint of a running pid |

Both apps read `<state>/state.json`, for example
`{"pose": "attention", "count": 3, "line": "3 files wait for review"}`, where
`<state>` comes from `FlibMenuStateDir` in the bundle's `Info.plist`. The
`selftest.request` and `quit.request` files exist only for measuring: the first
renders the button in light and dark appearance and fires "Organize now"
through the menu, the second chooses Quit from the menu.

Try it (it shows a temporary item named flib-menu-test in the menu bar):

```sh
./build.sh applet
open -g ~/Library/Application\ Support/io.flibtest.menu/flib-menu-test-applet.app
# ... then choose "Quit flib-menu-test" from its menu and clean up:
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
    -u ~/Library/Application\ Support/io.flibtest.menu/flib-menu-test-applet.app
rm -rf ~/Library/Application\ Support/io.flibtest.menu
```
