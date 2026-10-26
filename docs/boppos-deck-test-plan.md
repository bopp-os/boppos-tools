# boppos-deck manual test plan

The unit tests (`pytest`) cover the parsing, kernel-argument logic, VDF handling, Decky checks and input mapping
against fixtures. They never touch the real `/boot`, `/sys` or Steam. The steps below cover what they cannot.
Tick each box on a real BoppOS install (a VM is fine for 1 and 2).

## 1. Keyboard and mouse on the desktop
- [ ] `boppos-deck` opens a normal window; the sidebar shows six pages.
- [ ] Arrow keys move the focus ring between rows (thick outline, always visible). `Enter` toggles a switch or presses a row's button. `Esc` from a page returns to the sidebar.
- [ ] `Ctrl+Tab` / `Ctrl+Shift+Tab` change page. The hint bar shows Enter / Esc / Ctrl+Tab.
- [ ] Mouse: every row and button works by clicking.
- [ ] Narrow the window below 900 px: the sidebar collapses; selecting a page opens it, `Esc` goes back.
- [ ] System: *Check for updates* fills the status of each row. *Update* on a row opens the progress page; *Back* returns.
- [ ] About: switch **Advanced options** on; System shows Deployments and Update channel; off hides them.

## 2. Nested gamescope with a controller
```bash
gamescope -W 1280 -H 800 -f -- boppos-deck       # then repeat with -W 2560 -H 1600
```
- [ ] Window is full screen. The hint bar switches to A / B / LB RB when the controller is used, and back to keyboard hints after a key press.
- [ ] D-pad and left stick move focus; holding repeats after a short delay. `A` selects, `B` goes back, `LB`/`RB` change page.
- [ ] On the sidebar, `B` or `Start` asks *Exit BoppOS settings?*; confirming exits with status 0 (back to the shell/Steam).
- [ ] Text and the focus ring are readable at 1280×800 and at 2560×1600 (text should be visibly larger there).
- [ ] With the controller unplugged or SDL3 missing, the app still starts and keyboard works (a warning is printed).
- [ ] Steam Input keyboard template: arrows, Enter, Esc work the same.

## 3. On the device (no polkit agent) as a `wheel` user in Gaming Mode
Add the app via `bopp-steam-shortcut add` with Steam closed, then start Steam.
- [ ] A "BoppOS" shortcut is in the library; running `bopp-steam-shortcut add` again changes nothing; a `shortcuts.vdf.bak-*` exists.
- [ ] `bopp-steam-shortcut add` while Steam is running exits 3 and changes nothing.
- [ ] **No password prompt** and success for each: System *Check*, *Update OS*, Pin/Unpin and Roll back (Advanced), kernel-argument switch, Apps apply (install and remove), Decky install, Support report, Reboot.
- [ ] Decky: Steam restarts, the Decky menu appears; *Uninstall* removes the service. A tampered download (wrong hash) is refused.
- [ ] Kernel arguments: toggling a preset changes `/proc/cmdline` after a reboot; toggling it back removes only what it added. With an update staged the page shows the *reboot first* banner and the switches are disabled.

## 4. Same actions as a user outside `wheel`
Create a test user, log in on a local session (not wheel).
- [ ] Each privileged action from step 3 **fails** with a clear message (not authorized), without hanging on a prompt; nothing changed.
- [ ] `pkexec /usr/libexec/boppos/bootc-helper status` is refused for that user, and `bootc-helper pin 99`, `kargs-helper add init=/bin/sh` and `decky-helper foo` are rejected even for a wheel user (usage error, exit 2).
- [ ] Over SSH (not an active local session) as a wheel user the actions also fail rather than succeed.

## 5. Kernel-argument safety (VM)
- [ ] `sudo bopp-kargs remove quiet` on a cmdline containing `rd.quiet`: `rd.quiet` survives.
- [ ] `ostree admin instutil set-kargs` path: after a removal the machine boots and `ostree admin status` shows the booted and rollback deployments. This path could not be tested without a real ostree system; verify it before relying on it.
