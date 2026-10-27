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

A comprehensive update manager that updates your OS (`bootc`), firmware, Flatpaks, Homebrew, and all Distrobox and Litterbox containers. If an OS update is available, it displays a package diff preview to show you exactly what is changing.

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
| `-d, --distrobox` | Upgrade packages inside all Distrobox containers. |
| `-l, --litterbox` | Upgrade packages inside all Litterbox containers (via `litterbox-update --all` from `litterbox-sandbox-addons`, falling back to `litterbox enter <name> --root` with the box's package manager). |
| `-a, --all` | Update everything: OS, Firmware, Flatpak, Brew, Distroboxes and Litterboxes. *Running with no options does the defaults only (OS, Firmware, Flatpak, Brew).* |
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

A background system-tray application that reads system update states, alerts you via desktop notifications when a new OS image is staged, and provides right-click shortcut options to check, view changes (`bopp-diff`), apply updates, or update Distrobox and Litterbox containers.

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

#### Non-interactive mode (system scope, used by boppos-deck)

```bash
install-optional-flatpaks --list FILE --yes [--only ID[,ID...]] [--remove] [--json]
install-optional-flatpaks --status [--json]
```

Any option selects this mode. It needs `--yes` to change anything, only touches entries of the list, and installs with `flatpak --system` (Flatpak's own polkit rules let the active `wheel` user do that without a password). The interactive mode above is unchanged.

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

A full-screen GTK4/libadwaita app for the things you would otherwise do in a terminal: updates and rollback, kernel-argument presets, the curated Flatpak list, Decky Loader, a support report. It works with a controller in Steam Gaming Mode, and with mouse and keyboard on the desktop: same large rows, same pages.

* Keyboard: arrows move, `Enter` selects, `Esc` goes back, `Ctrl+Tab` / `Ctrl+Shift+Tab` switch pages.
* Controller (SDL3, read directly): D-pad or left stick moves, `A` selects, `B` goes back, `LB`/`RB` switch pages, `Start` asks to exit (Gaming Mode).
* Risky actions (rollback, pin, switch update tag, custom kernel arguments, Decky uninstall, high-risk kernel presets) are hidden until **Advanced options** is switched on in *About*, and always ask first. Apps are grouped in collapsible categories (`## Category` lines in the Flatpak list), with an *Only show installed apps* switch. Bazaar is installed once for every wheel user by `boppos-default-flatpaks.service` (`install-optional-flatpaks --defaults --yes`) and can be opened from the Apps page in Desktop Mode only; it is made for a mouse and keyboard, so Gaming Mode points to the desktop instead.
* Everything it does is one of the CLIs below, so scripts and a future Decky plugin can share the same backend. Set `BOPPOS_DECK_BIN_DIR` to point it at other copies of those tools (testing).
* Privileged actions go through `pkexec` helpers in `/usr/libexec/boppos/`. Changing the system asks for an administrator password (`auth_admin_keep`); only fixed read-only calls (`status`, `check`, `dropins`) are waived by `polkit/rules.d/51-boppos-deck.rules`. Gaming Mode has no polkit agent, so boppos-deck registers its own for the login session and shows a built-in gamepad keyboard: D-pad moves, `A` types, `RB` shift, `LB` delete, `Start` confirms, `B` cancels; a physical or Steam Input keyboard works too. On the desktop the desktop's own agent is used and boppos-deck does not register (set `BOPPOS_DECK_AGENT=1` to force it, with no other agent running).

Install adds `libadwaita` (about 5 MiB); `gtk4`, `python-gobject` and `sdl3` (through gamescope) are already in the image.

### `bopp-system` (Updates and deployments, JSON-friendly)

```bash
bopp-system [--json] status | check | upgrade [--only os,fw,flatpak,brew] | rollback | pin N | unpin N | switch TAG | tags | reboot | about | support-report
```

`upgrade` defaults to OS, Flatpaks and Homebrew (same as `boppos-update`). With `--json`, actions print one JSON event per line (`log`, `step`, `done`) and keep the last 20 logs in `~/.local/state/boppos-deck/logs/`. `switch` only changes the tag of the booted image, never the registry or repository.

### `bopp-decky` (Decky Loader)

```bash
bopp-decky [--json] status | install | uninstall
```

Installs per user into `~/homebrew` from the latest stable GitHub release, like the upstream installer, but verifies the download against the sha256 digest GitHub publishes (a release without one is refused). The root helper writes a fixed `plugin_loader.service` to `/etc/systemd/system` and refuses a binary the user does not own or that others can write to. It also sets Steam's CEF remote-debugging flag. In Gaming Mode it restarts Steam; elsewhere restart Steam yourself. As with upstream Decky, this is a root service that runs a file from your home directory.

### `bopp-steam-shortcut` (Steam library entry)

```bash
bopp-steam-shortcut add [--force] | status
```

Adds a "BoppOS" non-Steam shortcut. It only edits `shortcuts.vdf` while Steam is not running (exit 3 otherwise), backs the file up first (last 3 kept), checks the new bytes parse back, and replaces the file atomically. Corrupt files are left alone. It offers the shortcut once per user. `bopp-steam-shortcut.service` retries every minute until Steam is closed and is enabled by the preset in `systemd/user-preset/`; if your image does not apply user presets, enable it with `systemctl --user enable bopp-steam-shortcut.service`.

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
* **`add <argument>`** / **`remove <argument>`**: change one argument. `add` skips arguments that are already present. Add uses `ostree admin kargs edit-in-place --append-if-missing=`; ostree has no delete option, so `remove` writes the full list back with `ostree admin instutil set-kargs`. Both need root (`sudo`). `--dry-run` shows the plan.
* **`edit`**: edit the arguments in `$EDITOR`; the difference is applied as add/remove.
* **`diff [a] [b]`**: arguments that differ between two entries.
* **`dropins`**: preset files and whether each matches this device.
* **`apply`**: make the system match the enabled, matching drop-ins. Idempotent. Only arguments listed in `/var/lib/bopp-kargs/managed` (the ones it added itself) are ever removed.
* **`enable <name>`** / **`disable <name>`**: switch a drop-in on or off (writes an override in `/etc/bopp-kargs.d`) and apply.

`status`, `list`, `apply`, `enable`, `disable`, `add` and `remove` also work without `sudo`: they run the polkit-gated helper `/usr/libexec/boppos/kargs-helper` (password for everything that changes the system). `add`/`remove` through the helper refuse `ostree=`/`root=` and keys that open a shell or change init (`init`, `rd.break`, `single`, `systemd.unit`, ...); the CLI run with `sudo` is not restricted.

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

The tests need no root and touch nothing real: `pytest`.
