# Deck image: first boot in Game Mode (contract with bopp-os/cachyos)

The `cachyos-deck` image boots plasmalogin → autologin → gamescope-session-cachyos → Steam. Game Mode never runs XDG
autostart (so `bopp-welcome.desktop` never fires there), and on a fresh install the account already exists (so
`boppos-oobe.service`, the setup with its Network step, correctly skips). Without the pieces below, an install with no
network ends on a black screen: Steam cannot download its client (`Failed to load steamui.so`).

## What the image does

A user unit, `boppos-deck-first-boot.service` (`/usr/libexec/boppos/deck-first-boot` in bopp-os/cachyos), runs once per
gamescope session **before** `steam-launcher.service`. It checks:

| Word | Missing when |
|---|---|
| `first-run` | `/var/lib/boppos/first-run` (the installer's marker) exists |
| `steam` | neither `~/.local/share/Steam/ubuntu12_64/steamui.so` nor `ubuntu12_32/steamui.so` exists |
| `network` | only checked when Steam is missing: no active NetworkManager connection after `nm-online -t 20` |

If anything is missing it runs, as the logged-in user, inside the running gamescope (X11 through Xwayland,
`GDK_BACKEND=x11`; **no Steam, so no Steam on-screen keyboard**):

```sh
BOPPOS_FIRST_BOOT_MISSING="first-run steam network" /usr/bin/boppos-deck --first-run   # marker present
BOPPOS_FIRST_BOOT_MISSING="steam network"           /usr/bin/boppos-deck --welcome     # marker gone
```

The exit code is ignored. Steam starts when `boppos-deck` exits, so `boppos-deck` must exit when the user is done
and must never wait for Steam. If Steam is still missing and the system still offline afterwards, the image opens
`kcmshell6 kcm_networkmanagement` as a last resort.

`/usr/share/boppos/start-in.conf` in the deck image:

```ini
[start-in]
display-manager = plasmalogin
gaming-session = gamescope-session
desktop-session = plasma
```

## What boppos-deck does (`boppos/firstboot.py`, `boppos/steam_client.py`)

**Which steps.** `BOPPOS_FIRST_BOOT_MISSING` set (even empty) means "started by the image": the window is fullscreen
with no title bar (as in Gaming Mode), and it quits when the tour is done.

- Known words are trusted; unknown words are ignored. Empty, or nothing known: the live system decides (marker,
  steamui.so, NetworkManager's state).
- The marker always adds `first-run`. `steam` while offline always adds `network` (nothing can be downloaded otherwise).
- With `first-run`: the normal tour, with **Network** and **Steam** right after Welcome.
- Without it (`--welcome` on a later boot): **only** the missing steps and Done; nothing is applied, the tour's done
  flag is not touched.
- Started by the image, but nothing is missing by now: exits at once.
- Not started by the image (manual `--welcome`, XDG autostart on a desktop): the tour as before. `--first-run` with
  the marker still adds Network (offline) and Steam (when the image ships the Steam bootstrap and steamui.so is
  missing).

**Network step.** The setup's network page (`ui/network.py`, shared with `ui/oobe.py`): connection status (a cable shows
as "Connected by cable"), the Wi-Fi list (Join), **Hidden network** (type the name, then the password; empty means an
open network), Scan again. Every text entry uses boppos-deck's own on-screen keyboard, driven by the controller; a USB
keyboard types into it too. Wi-Fi joins go through `nmcli` as the user, the password through a 0600 `passwd-file`,
never an argument. Next while offline is **Skip**, with a warning that Steam needs the internet once.

**Steam step.** Starts on its own when the step opens and the system is online; otherwise it says so and offers
"Network settings" (back to the Network step). Progress: checking the stored packages, then bytes downloaded of the
total, then unpacking. Next is **Skip** until the download is done (with a warning: Steam then downloads by itself,
about 5 minutes of black screen); a failed download shows the reason and **Try again**. Closing the window or skipping
stops the download.

The download is the installer's preload (bopp-os/fisherman, `internal/post/steam.go`), ported step for step:

1. the bootstrap `/usr/lib/steam/bootstraplinux_ubuntu12_32.tar.xz` into `~/.local/share/Steam` (0700), unless
   `steam.sh` is already there;
2. `https://client-update.steamstatic.com/steam_client_ubuntu12` (Valve KeyValues), stored as
   `package/steam_client_ubuntu12.manifest`;
3. every package into `package/` under Steam's own name (`zipvz` with `sha2vz` when listed, else `file`/`size`/`sha2`),
   through a `.part` file that only takes its name once size and SHA-256 match. Packages already stored with the right
   size and SHA-256 (the installer's preload, an earlier try) are not downloaded again.

Then one step more than the installer: **4.** unpack the packages (VZ is Valve's LZMA wrapper around a zip) into the
Steam folder, `steamui.so` last and only when everything else unpacked, so a failed or cancelled run never leaves a
`steamui.so` behind for the image's check to trust. No `.installed` marker is written (same as the installer), so
Steam still installs the packages on its first start: it only extracts then, nothing is downloaded. Everything runs as
the user, so every file is theirs.

Offline before the manifest arrives, nothing is written to the home. If nothing at all was stored and the Steam folder
was new, it is removed again.

**Start in.** `oobe-helper set-start-in USER gaming|desktop` writes `/etc/plasmalogin.conf.d/zz-steamos-autologin.conf`
on plasmalogin (see [first-run.md](first-run.md#where-to-start-gaming-mode-or-desktop)). It does not run
`steamos-session-select`, which would also end the running session.

## Controller, theme and the journal

- Under the image's own gamescope (no Steam, no window manager) GTK never marks the window active, so the usual rule
  "gamepad presses only reach the active window" is off whenever `BOPPOS_FIRST_BOOT_MISSING` is set: the window is
  the only one there.
- In Game Mode boppos-deck asks libadwaita for dark (`PREFER_DARK`): dark unless the system explicitly asks for
  light. `ADW_DEBUG_COLOR_SCHEME` still wins when set.
- At startup boppos-deck logs to stderr which SDL3 it loaded and every joystick SDL sees: name, `vid:pid`, and
  whether SDL has a gamepad mapping for it (a joystick without one gives no input). It also logs pad hotplug, the
  first press it received, and when presses are dropped because the window is not active. On the image:
  `journalctl -b --user | grep boppos-deck` (or the first-boot unit's journal). Typical causes: "no joysticks"
  means `/dev/input/event*` is not readable for the session user; "joystick only" means inputplumber's virtual
  device is not in SDL's gamepad database.

## Not verified on hardware

- The whole flow in gamescope before Steam (window size, controller input through SDL, the on-screen keyboard over
  Xwayland). No GTK in the dev box, so the UI files were only linted.
- plasmalogin reading `Session=gamescope-session.desktop` / `plasma.desktop` from `zz-steamos-autologin.conf` with
  `User=` in it too; the format follows the image's `deck-autologin`, not `steam-set-session`'s source (not found).
- Steam's first start over a client unpacked by boppos-deck (expected: it installs from `package/` without downloading).
- `nmcli` joins as the autologin user in that session (NetworkManager polkit; boppos-deck's own polkit agent answers
  prompts in Gaming Mode).

## Follow-ups for the image

- After the installer's preload, all packages are in `package/` but no `steamui.so` exists, so the first-boot check
  still says `steam` and opens boppos-deck: the Steam step then only unpacks (seconds, no download). Fine as it is; the
  check could also accept a complete `package/` to skip the window entirely.
