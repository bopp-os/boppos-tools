# BoppOS Tools

A collection of custom enhancements, tools, and utilities designed specifically for BoppOS to streamline system management, development environment provisioning, and administration.

---

## Table of Contents
1. [bopp-dev-setup](#bopp-dev-setup-dev-containers)
2. [boppos-update](#boppos-update-update-manager)
3. [bopp-diff](#bopp-diff-package-diff-analyzer)
4. [bopp-tray](#bopp-tray-update-notifier)
5. [install-optional-flatpaks](#install-optional-flatpaks-curated-apps)
6. [bopp-setup-howdy](#bopp-setup-howdy-howdy-face-authentication-setup)
7. [boppos-deck](#boppos-deck-controller-friendly-settings-app), [bopp-system](#bopp-system-updates-and-deployments-json-friendly), [bopp-decky](#bopp-decky-decky-loader), [bopp-steam-shortcut](#bopp-steam-shortcut-steam-library-entry)
8. [bopp-kargs](#bopp-kargs-kernel-arguments-manager)
9. [bopp-tpm-refresh](#bopp-tpm-refresh-tpm2-auto-unlock)
10. [bopp-migrate](#bopp-migrate-migration-tool)

---

## Included Tools

### `bopp-dev-setup` (Dev Containers)

Automatically provisions a high-performance CachyOS development container using Distrobox. It auto-detects host CPU capabilities (mapping up to Zen 4/5 optimized architectures), synchronizes Chaotic-AUR and BoppOS repositories, installs default utility toolchains, and configures graphical desktop exports.

```bash
bopp-dev-setup [OPTIONS]
```

#### Options:
| Flag | Description |
|---|---|
| `-n, --name NAME` | Specify a custom name for the container (default: `bopp-dev`). |
| `-i, --image IMAGE` | Override default CPU auto-detected CachyOS image. Must be Arch or CachyOS-based. |
| `-H, --home DIR` | **Sandbox Isolation:** Specify a custom isolated home directory path to decouple container state from host `$HOME`. |
| `-y, --yes` | Recreate the container automatically if it already exists, skipping interactive prompts. |
| `-k, --keep` | Keep the container if it already exists (do not recreate it). |
| `--ide IDES` | Comma-separated list of IDEs to install and export (options: `code`, `vscodium`, `antigravity-ide`, `zed`, `pycharm`, `intellij`). |
| `--packages PKGS` | Comma-separated list of additional `pacman`/AUR packages to install inside the container. |
| `--additional-packages PKGS` | Alias for `--packages`. |
| `-a, --app APPS` | Comma-separated list of applications to export (using `distrobox-export`) to the host desktop. |
| `-h, --help` | Show the help menu. |

> [!TIP]
> Use the `-H` / `--home` option to run untrusted AUR packages inside a container without risking security exposure to your host SSH keys, documents, or active credentials.

#### Docker & Podman Socket Integration

`bopp-dev-setup` automates host-to-container socket mapping and client execution, allowing containerized development environments to interact directly with the host's container engine:
* **Automatic Socket Activation**: Activates and mounts host user-level `podman.socket` or `docker.socket` dynamically.
* **Reboot-Safe Socket Symlinking**: Maps `/var/run/docker.sock` to the active rootless user socket inside the container, configured via a persistent login profile script (`/etc/profile.d/bopp-docker.sh`).
* **Host-Exec CLI Fallbacks**: Deploys wrappers for `docker` and `podman` in `/usr/local/bin` inside the container that forward commands to the host engine or fall back to `distrobox-host-exec`.

---

### `boppos-update` (Update Manager)

A comprehensive update manager that updates your OS (`bootc`), firmware, Flatpaks, Homebrew, AppImages (through Gear Lever), Proton "Latest" installs (through ProtonPlus), and all Distrobox and Litterbox containers. If an OS update is available, it displays a package diff preview to show you exactly what is changing. The OS step runs through `bopp-system upgrade --only os` (the same polkit-checked helper the settings app uses, so no sudo password) when `bopp-system` is installed, and falls back to `sudo bootc upgrade` otherwise. Firmware still uses sudo.

```bash
boppos-update [OPTIONS]
```

#### Options:
| Flag | Description |
|---|---|
| `-o, --os` | Update OS image (`bootc upgrade`). |
| `-w, --fw` | Update firmware (`fwupdmgr`). |
| `-f, --flatpak` | Update user and system Flatpaks. |
| `-b, --brew` | Update Homebrew package databases and formulae. |
| `-g, --appimage` | Update AppImages that Gear Lever manages and has an update source for (`gearlever --update --all --yes`). Running apps are skipped. |
| `-p, --proton` | Update the "Latest" compatibility tools installed by ProtonPlus, such as GE-Proton Latest (`protonplus update all`). Pinned versions are left alone. |
| `-d, --distrobox` | Upgrade packages inside all Distrobox containers. |
| `-l, --litterbox` | Upgrade packages inside all Litterbox containers (via `litterbox-update --all` from `litterbox-sandbox-addons`, falling back to `litterbox enter <name> --root` with the box's package manager). |
| `-a, --all` | Update everything: OS, Firmware, Flatpak, Brew, AppImages, Proton, Distroboxes and Litterboxes. *Running with no options does the defaults only (OS, Firmware, Flatpak, Brew, AppImages, Proton).* |
| `-v, --verbose` | Show verbose output logs for sub-package managers. |
| `-y, --yes`, `-F, --force` | Auto-confirm updates and skip interactive prompts (useful for automated update hooks). |
| `-h, --help` | Show the help menu. |

---

### `bopp-diff` (Package Diff Analyzer)

A package difference analyzer comparing the running host package list against staged or remote upstream `bootc` container images. It downloads registry SBOM manifests via `cosign` to calculate diffs quickly without downloading full images.

```bash
sudo bopp-diff [OPTIONS] [TARGET_IMAGE | TAG]
```

#### Options:
| Parameter | Description |
|---|---|
| `--staged` | Explicitly compare against the pre-downloaded staged update waiting for a reboot. |
| `--json` | Format output diffs as raw JSON. |
| `[TAG]` | Compare current booted image against a tag on the same registry (e.g. `sudo bopp-diff latest`). |
| `[TARGET_IMAGE]` | Compare against an arbitrary remote image registry URI. |

---

### `bopp-tray` (Update Notifier)

A background system-tray application that reads the update state and alerts you through desktop notifications when a new OS image is ready. Its menu:

* **Status line**: "BoppOS is up to date", "Update available" or "Restart to finish the update". When a restart is pending, *Restart to Finish Update…* appears right below it (it asks again through a notification button first).
* **Open BoppOS Settings**: starts `boppos-deck` (also what a left click does, where the desktop supports it).
* **The update entries** (Everything, Operating System, Flatpaks, ...) open `boppos-deck` on its update screen and start the update there, so you see progress, any password prompt and the restart offer. They are `boppos-deck --update all` or `--update os,fw,flatpak,brew`; if the app is already open, the running window is used. Without `boppos-deck` they fall back to background notifications, and without `bopp-system` to a terminal running `boppos-update`.
* **Check for Updates**, **Install Updates ▸** (Everything, Operating System, Flatpak Apps, Firmware, and Containers in a terminal), **Review Changes…** (`bopp-diff`).
* **Last checked** and **Quit**.

*Install Updates* runs `bopp-system upgrade` in the background and reports progress and the result by notification; changing the system asks for the administrator password through your desktop's polkit agent.

```bash
# Enable the tray monitor to start with your user session
systemctl --user enable --now bopp-tray.service
```

---

### `install-optional-flatpaks` (Curated Apps)

An interactive wizard that prompts you to install optional, recommended Flatpak applications (from the curated BoppOS list in `/usr/share/boppos/boppos-flatpaks.txt`). It provides menu selections for developer tools, multimedia, social apps, gaming tools, and virtualization.

```bash
install-optional-flatpaks
```

---

#### Non-interactive mode (per user unless `--system`; used by boppos-deck)

```bash
install-optional-flatpaks --list FILE --yes [--only ID[,ID...]] [--remove] [--json]
install-optional-flatpaks --status [--json]
```

Any option selects this mode. Without `--list` it works on the core list plus `boppos-flatpaks-extra.txt` (opt-in extras: emulators, game and media streaming, launchers); the interactive mode above only ever uses the core list, so "install everything" does not grow. It needs `--yes` to change anything, only touches entries of the list, and installs for the current user (`flatpak --user`, no password); `--system` installs for all users instead (Flatpak's own polkit rules let the active `wheel` user do that). Status shows each app's scopes, and removal takes it out of every scope it is in. The Apps page offers the system choice as "Install for all users" under Advanced. The interactive mode above is unchanged.

### `bopp-setup-howdy` (Howdy Face Authentication Setup)

An interactive wizard that configures Howdy face authentication for BoppOS. Since the `/usr` filesystem is read-only at runtime on BoppOS, this helper configures PAM configuration files in the writable `/etc` directory and guides users through selecting their IR/webcam device from `/dev/v4l/by-path/`.

```bash
sudo bopp-setup-howdy
```

#### Steps:
1. **Device Selection**: Automatically lists and helps select your IR/webcam device from `/dev/v4l/by-path/`.
2. **Face Enrollment**: Registers your face model using the `howdy` tool.
3. **PAM Configurations**: Optionally enables Howdy face authentication for specific services, including terminal auth (`sudo`), lockscreen (`swaylock`), display manager (`sddm`), and local login shells (`system-local-login`).

---

### `boppos-deck` (Controller-friendly settings app)

A full-screen GTK4/libadwaita app for the things you would otherwise do in a terminal: updates and rollback, kernel-argument presets, the curated Flatpak list, putting apps into the Steam library, Decky Loader, a support report. It works with a controller in Steam Gaming Mode, and with mouse and keyboard on the desktop: same large rows, same pages.

* Keyboard: arrows move, `Enter` selects, `Esc` goes back, `Ctrl+Tab` / `Ctrl+Shift+Tab` switch pages.
* Controller (SDL3, read directly): D-pad or left stick moves (a diagonal push does nothing, so a drifting thumb cannot send a stray Left or Right; holding a direction speeds up), `A` selects, `B` goes back, `LB`/`RB` switch pages, **right stick up/down or `LT`/`RT` jump a screenful** on long pages, `Start` asks to exit (Gaming Mode). The controller only works while the boppos-deck window is focused, so a game in front of it never presses its buttons. Keyboard: `PgUp`/`PgDn` jump, `Home`/`End` go to the first/last row. Movement stays inside a page (it never slips into the sidebar), and going back to a page from the sidebar returns to the row you left.
* Risky actions are hidden until **Advanced options** is switched on in *About*, and always ask first: rollback, pin, switching image, Decky uninstall, and **everything on the Kernel arguments page** (in normal mode it only shows the running command line; the presets, high-risk presets and custom arguments are Advanced). Apps are grouped in collapsible categories (`## Category` lines in the Flatpak list), with an *Only show installed apps* switch. Each app has one switch, which installs or removes it; Steam shortcuts are managed on the **Steam library** page (uninstalling an app from the Apps page also removes its shortcut). Bazaar is always recommended by the first-run setup wizard, which installs the apps you keep switched on (user scope, no password) in the update window, and it can be opened from the Apps page in Desktop Mode only; it is made for a mouse and keyboard, so Gaming Mode points to the desktop instead.
* Everything it does is one of the CLIs below, so scripts and a future Decky plugin can share the same backend. Set `BOPPOS_DECK_BIN_DIR` to point it at other copies of those tools (testing).
* Privileged actions go through `pkexec` helpers in `/usr/libexec/boppos/`. Changing the system asks for an administrator password (`auth_admin_keep`); only fixed read-only calls (`status`, `check`, `dropins`) are waived by `polkit/rules.d/51-boppos-deck.rules`. Gaming Mode has no polkit agent, so boppos-deck registers its own for the login session and shows a built-in gamepad keyboard: D-pad moves, `A` types, `RB` shift, `LB` delete, `Start` confirms, `B` cancels; a physical or Steam Input keyboard works too. On the desktop the desktop's own agent is used and boppos-deck does not register (set `BOPPOS_DECK_AGENT=1` to force it, with no other agent running).

Install adds `libadwaita` (about 5 MiB); `gtk4`, `python-gobject` and `sdl3` (through gamescope) are already in the image.

### `bopp-system` (Updates and deployments, JSON-friendly)

```bash
bopp-system [--json] status | check | upgrade [--only os,fw,flatpak,brew,appimage,proton,distrobox,litterbox] | rollback | pin N | unpin N | switch IMAGE | reboot | about | support-report
```

`upgrade` defaults to everything but firmware: OS, Flatpaks, Homebrew, AppImages, Proton, Distroboxes and Litterboxes (`boppos-update` with no options does firmware but not containers). Gear Lever and ProtonPlus are used as a native binary or as their Flatpaks (`it.mijorus.gearlever`, `com.vysp3r.ProtonPlus`); a target whose tool is missing is skipped. `distrobox` and `litterbox` run `boppos-update --distrobox` / `--litterbox`; a box that fails to update makes that step fail. With `--json`, actions print one JSON event per line (`log`, `step`, `done`) and keep the last 20 logs in `~/.local/state/boppos-deck/logs/`. `switch IMAGE` takes the **complete** reference (`ghcr.io/bopp-os/cachyos-plasma:v4`, or `…@sha256:…`) and nothing is filled in for you: a bare tag, a missing registry or tag, or a transport prefix (`oci:`, `docker://`, `containers-storage:`) is refused. It runs `bootc switch --transport registry --enforce-container-sigpolicy`, so images from `ghcr.io/bopp-os` must carry the BoppOS signature. boppos-deck offers it only under Advanced, as three steps (warning, typing the whole reference, a final confirmation that shows from/to). Staying on your channel is what updates do; the only ready-made way back is *Roll back*. App names in the Flatpak list come from a `# name: ...` comment on each line (looked up on Flathub); installed apps show the name they call themselves.

### `bopp-decky` (Decky Loader)

```bash
bopp-decky [--json] status | install | uninstall
```

Installs per user into `~/homebrew` from the latest stable GitHub release, like the upstream installer, but verifies the download against the sha256 digest GitHub publishes (a release without one is refused). The root helper writes a fixed `plugin_loader.service` to `/etc/systemd/system` and refuses a binary the user does not own or that others can write to. It also sets Steam's CEF remote-debugging flag. In Gaming Mode it restarts Steam; elsewhere restart Steam yourself. As with upstream Decky, this is a root service that runs a file from your home directory.

### `bopp-steam-shortcut` (Steam library entries)

```bash
bopp-steam-shortcut add [--force] | status                 # first login: the "BoppOS" shortcut
bopp-steam-shortcut apps                                   # BoppOS and installed Flatpak apps, in Steam or not
bopp-steam-shortcut sync --add ID[,ID] --remove ID[,ID] [--restart-steam]
bopp-steam-shortcut apply-pending [--wait SECONDS] [--reopen-steam]
```

Adds and removes non-Steam shortcuts so apps can be started from Gaming Mode. Flatpak apps get `flatpak run <id>` as launch options and their exported PNG icon; each shortcut is identified by what it runs, so your own shortcuts are never touched. The BoppOS shortcut gets the blue app icon and library artwork (capsule, portrait, hero, logo and the small list icon, which is a JPEG because Steam's sidebar did not draw PNG icons; rendered by `tools/render-steam-art` onto the icon's blue gradient and installed in `/usr/share/boppos/steam-art/`); the images are copied into Steam's `grid/` folder under the shortcut's id when it is added (and the shortcut's `icon` points at the JPEG there; Flatpak apps get the same, converted from their exported PNG with Pillow), never over a file that is already there, and removed with the shortcut only if still unchanged. boppos-deck has a **Steam library** page: what is already in Steam on top, everything else in collapsed categories (the curated list's, plus "Other apps").

Steam only keeps changes to `shortcuts.vdf` when it is closed, and rewrites the file when it exits, so changes are **queued** in `~/.local/state/boppos-deck/steam-pending.json` and applied as soon as Steam is not running:
* Steam closed: applied right away.
* Steam running and `--restart-steam` (what boppos-deck uses): a detached helper (`systemd-run --user`) is started, then Steam is asked to quit (`steam -shutdown`). When it has quit the helper edits the files and, on the desktop, reopens Steam; in Gaming Mode the session brings Steam back.
* Otherwise `sync` exits 3 and the changes wait; `bopp-steam-pending.service` applies anything left over at the next login.

Every edit backs the file up first (last 3 kept), checks the new bytes parse back, then replaces the file atomically; a file that does not parse is left alone. `add` offers the BoppOS shortcut once per user via `bopp-steam-shortcut.service`, which retries every minute until Steam is closed and is enabled by the preset in `systemd/user-preset/` (otherwise: `systemctl --user enable bopp-steam-shortcut.service bopp-steam-pending.service`).

---

### `boppos-boot-theme` (Plymouth and GRUB artwork)

A separate package that installs the BoppOS boot splash (black, the white mark breathing, the small wordmark at the
bottom, with a working disk-passphrase prompt) and a matching GRUB theme. It only installs files; selecting the
Plymouth theme is a step for the image, and `sudo bopp-grub-theme install` puts the GRUB theme on `/boot` (reversible, with `--dry-run`); both are described in [docs/boot-theme.md](docs/boot-theme.md), which also has a way to preview Plymouth without rebooting. `boppos-artwork` now also ships the logo lockups
(`boppos-logo-{horizontal,stacked}-{white,black,blue}.svg`; *blue* is the white logo on the icon's blue gradient).

## Critical System & Migration Tools

The tools below modify critical system boot, encryption, or overlay structures. Always handle them with appropriate caution.

---

### `bopp-kargs` (Kernel Arguments Manager)

Reads and changes the kernel arguments of the deployment that boots next, and applies device-specific presets ("drop-ins"). Every command accepts `--json`.

```bash
sudo bopp-kargs COMMAND [ARGS]
```

> [!WARNING]
> **CRITICAL BOOT CONFIGURATION:** Incorrect kernel arguments can prevent the system from booting. `bopp-kargs` refuses to touch `ostree=` and `root=`, matches arguments token-exact (`remove quiet` never changes `rd.quiet`), and refuses to change anything while an update is staged or a rollback is queued, because the staged deployment has no boot entry yet. Reboot first.

#### Commands:
* **`status`**: boot entries (booted one marked) and whether changes are currently possible.
* **`list [index]`**: arguments of the next-boot entry, or of entry `index`.
* **`add <argument>`** / **`remove <argument>`**: change one argument. `add` skips arguments that are already present. Both write the full list back with `ostree admin instutil set-kargs`, which makes ostree write a new boot version and regenerate a GRUB menu (`edit-in-place` would only change the entry file, which a generated grub.cfg never sees). On GRUB without `blscfg`, bopp-kargs refuses to change anything unless ostree will regenerate grub.cfg (`sysroot.bootloader=grub2` in `/ostree/repo/config`, or `auto` without static bootupd configs), and warns when grub.cfg does not carry an added argument afterwards. `status` shows this as `layout`. Both need root (`sudo`). `--dry-run` shows the plan.
* **`edit`**: edit the arguments in `$EDITOR`; the difference is applied as add/remove.
* **`diff [a] [b]`**: arguments that differ between two entries.
* **`dropins`**: preset files and whether each matches this device.
* **`apply`**: make the system match the enabled, matching drop-ins. Idempotent. Only arguments listed in `/var/lib/bopp-kargs/managed` (the ones it added itself) are ever removed.
* **`enable <name>`** / **`disable <name>`**: switch a drop-in on or off (writes an override in `/etc/bopp-kargs.d`) and apply.

`status`, `list`, `apply`, `enable`, `disable`, `add` and `remove` also work without `sudo`: they run the polkit-gated helper `/usr/libexec/boppos/kargs-helper` (password for everything that changes the system). The app and the helper will not *add*: anything the boot needs (`ostree`, `root`, `rootflags`, `rootfstype`, `rw`, every `rd.*` early-boot argument, `luks.*`), anything that opens a shell or changes init (`init`, `rd.break`, `single`, all `systemd.*`), security switches (`selinux`, `apparmor`, `security`, `lsm`, `lockdown`, `audit`, `fips`...), driver blacklists (`module_blacklist`, `modprobe.blacklist`...) and hardware limits (`acpi`, `nosmp`, `maxcpus`, `mem`...). *Removing* is only refused for what the boot needs, so a bad argument that is already there can always be taken out. `sudo bopp-kargs add|remove --force` overrides all of this except `ostree=`/`root=`. `bopp-kargs describe ARG` says what an argument does and whether adding it is allowed. In boppos-deck, an argument that has no description needs a second "I know what it does" confirmation.

#### Drop-in files

`/usr/lib/bopp-kargs.d/*.conf` ships with the image; a file with the same name in `/etc/bopp-kargs.d/` replaces it completely.

```ini
description = Use the active amd-pstate driver     # shown in boppos-deck
category = Power & sleep                           # group heading in boppos-deck
risk_level = low                                   # low, medium or high (high is hidden until Advanced is on)
risk = What can go wrong, in plain words           # shown next to the switch; medium/high ask for confirmation
kargs = amd_pstate=active                          # may repeat
enabled = true                                     # false = off by default
match.sys_vendor = Valve                           # DMI field from /sys/class/dmi/id
match.product_name = Jupiter|Galileo               # fnmatch patterns, any case, '|' = or
```

Without any `match.*` line a drop-in applies to every device. `kargs.d/` ships opt-in presets for any machine (NVMe power states, deep sleep, threaded IRQs, 1000 Hz USB mice, AMD/Intel/Nvidia graphics fixes, PCIe error reporting, Intel IOMMU for VMs), each off by default with its risk written out. Presets whose arguments the running system already has show as *Active* and are not applied twice.

**Descriptions and safeguards.** `share/boppos/kargs-info.json` holds plain-language descriptions of common arguments (including the ones the image sets in `bootc/kargs.d`); it is help text only and easy to extend. What is needed to boot is fixed in code (`boppos/kargs_info.py`): `ostree=`, `root=`, `rootflags=`, `rootfstype=`, `rw`, and the disk-unlock families (`rd.luks.*` except `rd.luks.options`, `rd.lvm.*`, `rd.md.*`, `luks.*`). `bopp-kargs remove` refuses them unless `sudo bopp-kargs remove --force` is used (`ostree=` and `root=` never), drop-ins cannot set them, the helper never touches them, and a rewrite that would drop one is refused. A drop-in with an invalid line is reported by `dropins` and ignored.

---

### `bopp-tpm-refresh` (TPM2 Auto-Unlock)

A utility to easily re-enroll LUKS decryption keys to your hardware's TPM2 chip. Running this tool is often required after kernel or system bootloader updates when PCR measurement values shift.

```bash
sudo bopp-tpm-refresh
```

> [!CAUTION]
> **DISK ENCRYPTION:** This tool interacts directly with disk encryption keys and modifies `/etc/crypttab` to bind decryption to the system TPM2 chip. Interrupted operations or misconfigurations could lead to boot unlock issues.

---

### `bopp-migrate` (Migration Tool)

A transition script designed to assist advanced users transitioning their `/etc` and `$HOME` configurations from a Fedora-based atomic OS (like Bazzite or Bluefin) to Arch-based BoppOS.

```bash
sudo bopp-migrate
```

> [!CAUTION]
> **POTENTIALLY DESTRUCTIVE:** Do not run `bopp-migrate` unless you fully understand what it does and have verified backups of `/etc` and your home directory.

#### Script Execution Phases:
1. **Pristine System Reset**: Backs up `/etc` to `/var/roothome/migration_backups/` and restores pristine Arch-native PAM and NSS configurations to prevent GDM login loops.
2. **User & Group ID Alignment**: Merges local system users/groups with Arch-native base users, keeping file permissions aligned.
3. **OverlayFS Maintenance**: Clears stale OverlayFS metadata to prevent file system locking issues on `/var`.
4. **Interactive Home Directory Cleaning**:
   * **Surgical Mode**: Safely relocates conflicting Fedora shell configuration files (`.bashrc`, `.zshrc`, fish configurations) to a backup vault folder.
   * **Total Reset Mode**: Relocates all hidden config folders into the backup vault directory, retaining raw user data folders and container files.
   * **Protected Files**: Important data such as Flatpak settings (`.var`), Steam games (`.steam`), browser profiles (`.mozilla`, `.config/google-chrome`), SSH/GPG keys, and game launchers are kept safe from removal.
5. **TPM Encryption Setup**: Optionally re-registers TPM2 decryption bindings.

---

## Running from a checkout (development)

The root helpers normally live in `/usr/libexec/boppos/`. To try the tools from a git checkout without installing:

```bash
export BOPPOS_HELPER_DIR="$PWD/libexec/boppos"     # use the helpers from this checkout
sudo -v; export BOPPOS_HELPER_ESCALATE=sudo         # optional: one sudo password instead of one polkit prompt per action
sudo -E bin/bopp-system --json status               # as root the helper runs directly
bin/bopp-system --json status                       # as a user: pkexec asks for a password (the polkit rule only knows /usr)
bin/boppos-deck                                     # the app; privileged pages will prompt for a password
sudo -E bin/bopp-kargs --dry-run add foo=1          # read-only/--dry-run first on a real system
```

`BOPPOS_HELPER_ESCALATE=sudo` swaps `pkexec` for `sudo -n`, so run `sudo -v` first and again whenever the sudo timestamp expires. It skips the polkit agent and password screen, so test those through `pkexec` (see `docs/boppos-deck-test-plan.md`).

### Trying the on-screen keyboard (no sudo, no password)

`BOPPOS_HELPER_ESCALATE=sudo` replaces polkit, so with it the password screen never appears. To try the keyboard on its own:

```bash
BOPPOS_DECK_TEST=keyboard bin/boppos-deck          # a pretend password prompt; prints what you typed and exits
BOPPOS_DECK_TEST=keyboard-retry bin/boppos-deck    # the first attempt is "wrong", to see the retry message
BOPPOS_DECK_TEST=text bin/boppos-deck              # the text-entry variant (used for custom kernel arguments)
SteamDeck=1 BOPPOS_DECK_TEST=keyboard bin/boppos-deck   # same, looking and behaving like Gaming Mode (full screen)
```

Nothing typed there is checked or sent anywhere. To test the **real** thing (our polkit agent answering a real
`pkexec` request) without leaving KDE, use `tools/dev-gaming`: it starts the app from a new login session (`su -l`), where KDE's
password agent does not apply, so the on-screen keyboard answers instead. `tools/dev-gaming --check` first proves that with a
harmless `pkexec true` (the password must be asked in the terminal, not in a KDE window). Details in
`docs/boppos-deck-test-plan.md`.

The tests need no root and touch nothing real: `pytest`.
