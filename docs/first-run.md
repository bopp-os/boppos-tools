# First run: one app, one marker (contract with the installer)

`boppos-deck --first-run` is the only entry point. It decides from the live system whether to show the account
setup, the welcome tour, or nothing. A bare `bootc install` (no installer, so no marker and no account) still ends
in the setup, never at a greeter nobody can sign in to. Code: `boppos/firstrun.py` (decision), `boppos/oobe_helper.py` and
`libexec/boppos/oobe-helper` (the root helper), `ui/oobe.py` (setup), `ui/welcome.py` (tour).

## The marker

`/var/lib/boppos/first-run` exists while a first run is pending. The installer writes one line:

```
mode=welcome     # the installer created an account
mode=oobe        # it did not
```

The mode is only a **hint**. The live system decides, and a disagreement is not an error:

| Real account | Marker | What runs |
|---|---|---|
| no | any, or none | **setup**: system settings, the account, where to start. It writes `mode=welcome` when the account exists. |
| yes | yes | **welcome tour**: apps, hardware presets, where to start (if not chosen yet) |
| yes | no | nothing (not even a window) |

The setup does not need the installer's marker: whoever puts the image on disk (the installer, or `bootc install`
by hand), a machine without a real account gets the setup before the login screen.

The tour's last step removes the marker (`oobe-helper finish-first-run`). Skipping or closing the tour leaves it, so
the tour comes back at the next sign-in. The setup leaves the marker too: it hands over to the tour at the first
sign-in.

**A real account** is an `/etc/passwd` entry with uid in `[1000, 60000)` and a login shell that is not `nologin` or
`false` (any directory: `/usr/sbin/nologin`, `/sbin/nologin`, `/usr/bin/nologin`, `/bin/false`). nobody (65534)
and system accounts never count. An empty shell field means `/bin/sh` and counts.

## What the installer must keep doing

Before first boot, when it creates an account (the app does **not** redo any of this once an account exists; the root
helper refuses these verbs from then on):

1. Create the first user in `wheel` (sudo and polkit admin checks need it; the image needs a `%wheel` sudoers rule).
2. Write `/etc/hostname`, `/etc/locale.conf`, `/etc/vconsole.conf` and the `/etc/localtime` symlink.
3. Pass the chosen preset kargs with `bootc install --karg` (see `first-run-presets.md`).
4. Write `/var/lib/boppos/first-run` with the right `mode=` line. **Without the marker the account exists but
   no welcome tour runs.**

When it creates no account, nothing is required: the setup runs because no account exists (writing `mode=oobe` is
still fine, and harmless).

## Where to start (Gaming Mode or Desktop)

Asked in both flows; the installer does not ask it. The app names no session and no display manager. The image
ships `/usr/share/boppos/start-in.conf`:

```ini
[start-in]
display-manager = sddm
gaming-session = <name of the .desktop file in wayland-sessions or xsessions, without .desktop>
desktop-session = <same>
```

The app offers a choice only when its session's `.desktop` file is installed. Today it implements **SDDM** only: it
writes `/etc/sddm.conf.d/50-boppos-autologin.conf` (`[Autologin] User= Session=`). Any other `display-manager`, a
missing file, or no installed session means: the step is left out and no autologin is written. Per image flavor:

| Flavor | Needs from the image | App does |
|---|---|---|
| Plasma (SDDM) | `start-in.conf` with both session names | writes the SDDM autologin file |
| GNOME (GDM) | not implemented; the mechanism is `/etc/gdm/custom.conf` `[daemon] AutomaticLogin=` and it needs a decision from the image session | nothing (step hidden) |
| anything else | a spec | nothing (step hidden) |

The tour asks only when no autologin file selects a session yet, so after the setup it does not ask twice.

## The root helper (`oobe-helper`)

Works only while a first run is pending (no real account, or the marker exists). Setup verbs (`create-user`, `set-locale`, `set-keymap`, `set-timezone`,
`set-hostname`) are refused once a real account exists. `set-start-in USER CHOICE` is allowed for the caller's own
account (or the setup account). `finish-setup` starts the display manager; `finish-first-run` removes the marker.
Old names `set-autologin` and `finish` are aliases. In a chroot (`systemd-detect-virt --chroot`) the settings are
written as files instead of calling `localectl`, `timedatectl` and `hostnamectl`.

polkit (`52-boppos-oobe.rules`) waives the password for `boppos-oobe` (setup) while the marker exists or no real
account does (`oobe-needed`), and for a local, active `wheel` user's own `set-start-in` and `finish-first-run`
command lines while the marker exists. Everything else asks for an administrator password. The setup needs
gamescope in the image (the image's job; there is no fallback compositor).

## Entry points

| Who starts it | How |
|---|---|
| `boppos-oobe.service` (before the login screen; runs while `oobe-needed` says no real account exists) | `boppos-deck --first-run` in gamescope |
| `autostart/bopp-welcome.desktop` (each sign-in) | `boppos-deck --first-run` |
| Settings > About "run the wizard again" | in-app `show_welcome` (unchanged) |
| `--welcome`, `--oobe`, `--welcome --hardware KEY` | force that flow (previews, tests); `--if-needed` still honours `welcome-done` |
