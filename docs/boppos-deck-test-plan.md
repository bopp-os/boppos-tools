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

## 3. On the device in Gaming Mode as a `wheel` user (no other polkit agent)
Add the app via `bopp-steam-shortcut add` with Steam closed, then start Steam.
- [ ] A "BoppOS" shortcut is in the library; running `bopp-steam-shortcut add` again changes nothing; a `shortcuts.vdf.bak-*` exists. While Steam is running it exits 3 and changes nothing.
- [ ] The app prints no "polkit agent not registered" warning (check by starting it from a terminal on a TTY session), so the agent registered.
- [ ] Reading never asks: System page loads, *Check for updates*, Kernel page lists presets.
- [ ] Every changing action shows the password screen with the action's text: Update OS, Pin/Unpin and Roll back (Advanced), kernel-argument switch, Decky install/uninstall. Flatpak apply and firmware update do not ask (their own rules).
- [ ] Typing with the controller works: D-pad moves, `A` types, `RB` shift, `LB` delete, `Start` OK, `B` cancel, Show/Hide, the 123 layer (`!@#$%^&*()-_=+[]{}\|;:'"/?<>`~,.@` all reachable). A correct password runs the action; a wrong one shows "Wrong password. Try again." and allows 2 more tries; Cancel aborts the action cleanly with a readable message.
- [ ] After a successful password a second action within a few minutes: note whether it asks again (`auth_admin_keep` retention depends on polkit's subject) and record the result here.
- [ ] Decky: Steam restarts, the Decky menu appears; *Uninstall* removes the service. A tampered download (wrong hash) is refused.
- [ ] Kernel arguments: toggling a preset changes `/proc/cmdline` after a reboot; toggling it back removes only what it added. With an update staged the page shows the *reboot first* banner and the switches are disabled.

## 3d. Update channels and names
- [ ] Advanced > System: *Channel* offers v3 (default), v4 and znver4 with the current one selected (a system on `v3` or `latest` shows v3). *Earlier build* lists dated builds of your channel, newest first; *Test build* lists the published `-test` tags; *Use another tag…* accepts e.g. `v4-20260301` and rejects spaces or odd characters. Each switch warns before running, and a tag that does not exist fails without changing anything.
- [ ] Apps page names are proper names (Bitwarden Desktop, Easy Effects, Virtual Machine Manager...), and installed apps use the name Flatpak reports.

## 3c. Steam library page
- [ ] **Steam library** lists BoppOS Settings and every installed Flatpak app; switches reflect what is already in Steam (apps added by hand with the same `flatpak run <id>` count).
- [ ] Desktop, Steam closed: toggling and *Apply* changes the library at once; open Steam: the app appears under Games > non-Steam, starts, and has its icon. Toggling off removes only that shortcut; your own shortcuts and `shortcuts.vdf.bak-*` backups are intact.
- [ ] Desktop, Steam running: *Apply* warns, Steam closes, the change lands, Steam reopens by itself.
- [ ] **Gaming Mode** (the risky one): *Apply* closes Steam; check that (a) the changes appear after Steam is back, (b) boppos-deck is not left half-done if the session restarts (`bopp-steam-pending.service` should pick up anything unfinished at the next login), (c) a running game is closed with the warning shown first. Record what actually happens here.
- [ ] A Flatpak app installed after the page was opened shows up after leaving and re-entering the page.

## 3a. Presets, custom arguments and apps
- [ ] Kernel arguments lists the presets in categories with a Low/Medium/High risk label and "Adds:" and "Risk:" lines. High-risk presets appear only with Advanced on.
- [ ] Turning on a medium or high preset shows its risk text with Cancel as default; cancelling puts the switch back; confirming asks for the password and applies.
- [ ] Advanced: *Add an argument* opens the keyboard with the text visible; `pci=noaer` is accepted; `init=/bin/sh`, `rd.break`, `ostree=x` and `a b` are rejected with a message. *Remove* on a listed argument asks first; arguments set by a preset show "preset" instead of a button.
- [ ] Current arguments each show a description (or the "no description yet" text); `rw`, `rootflags=`, `rd.luks.uuid=` show *Required to boot* and have no Remove button; image defaults show *Image default*. `sudo bopp-kargs remove rootflags=subvol=root` is refused; `sudo bopp-kargs --dry-run remove --force ...` shows the plan.
- [ ] A preset whose argument is already on the command line (for example `preempt=full`) shows *Active* and cannot be toggled.
- [ ] Apps: with a controller, A (and Right) expands a category, Left collapses it, Down walks into its apps. Bazaar is present after the first login of a wheel user (`boppos-default-flatpaks.service`), the row *Open Bazaar* starts it on the desktop, and in Gaming Mode the row is replaced by a note.
- [ ] Apps: categories are collapsed; *Only show installed apps* hides the rest and keeps the pending selection; Apply installs and removes.

## 3b. Desktop session with its own agent (KDE/GNOME)
- [ ] boppos-deck starts without registering an agent; a changing action shows the desktop's own password dialog; cancelling it fails the action with a readable message.

## 4. Same actions as a user outside `wheel`
Create a test user, log in on a local session (not wheel).
- [ ] Each changing action fails after the password screen (the wrong-password path, since only administrators can authorize), with a clear message and nothing changed.
- [ ] `bootc-helper pin 99`, `kargs-helper add init=/bin/sh` and `decky-helper foo` are rejected even for an administrator (usage error, exit 2), before any prompt matters.
- [ ] Over SSH (not an active local session) as a wheel user, the read-only calls are not waived and the changing actions fail instead of succeeding.

## 5. Kernel-argument safety (VM)
- [ ] `sudo bopp-kargs remove quiet` on a cmdline containing `rd.quiet`: `rd.quiet` survives.
- [ ] `ostree admin instutil set-kargs` path: after a removal the machine boots and `ostree admin status` shows the booted and rollback deployments. This path could not be tested without a real ostree system; verify it before relying on it.
