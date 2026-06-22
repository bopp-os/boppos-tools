# BoppOS Tools

A collection of custom enhancements, tools, and utilities designed specifically for BoppOS to streamline system management, development environment provisioning, and administration.

---

## Table of Contents
1. [bopp-dev-setup](#bopp-dev-setup-dev-containers)
2. [boppos-update](#boppos-update-update-manager)
3. [bopp-diff](#bopp-diff-package-diff-analyzer)
4. [bopp-tray](#bopp-tray-update-notifier)
5. [install-optional-flatpaks](#install-optional-flatpaks-interactive-apps)
6. [bopp-kargs](#bopp-kargs-kernel-arguments-manager)
7. [bopp-tpm-refresh](#bopp-tpm-refresh-tpm2-auto-unlock)
8. [bopp-migrate](#bopp-migrate-migration-tool)

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

---

### `boppos-update` (Update Manager)

A comprehensive update manager that updates your OS (`bootc`), firmware, Flatpaks, Homebrew, and all active Distrobox containers. If an OS update is available, it displays a package diff preview to show you exactly what is changing.

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
| `-a, --all` | Run default updates (OS, Firmware, Flatpak, Brew). *Note: Distroboxes must be updated explicitly via `-d`.* |
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

A background system-tray application that reads system update states, alerts you via desktop notifications when a new OS image is staged, and provides right-click shortcut options to check, view changes (`bopp-diff`), or apply updates.

```bash
# Enable the tray monitor to start with your user session
systemctl --user enable --now bopp-tray.service
```

---

### `install-optional-flatpaks` (Interactive Apps)

An interactive wizard that prompts you to install optional, recommended Flatpak applications (sourced from Bazzite-DX configurations). It provides menu selections for developer tools, multimedia, social apps, gaming tools, and virtualization.

```bash
install-optional-flatpaks
```

---

## Critical System & Migration Tools

The tools below modify critical system boot, encryption, or overlay structures. Always handle them with appropriate caution.

---

### `bopp-kargs` (Kernel Arguments Manager)

A manager for Boot Loader Specification (BLS) kernel arguments on atomic deployments. It reads, diffs, and updates boot loader configs.

```bash
sudo bopp-kargs COMMAND [ARGS]
```

> [!WARNING]
> **CRITICAL BOOT CONFIGURATION:** Modifying kernel arguments directly edits system bootloader configurations. Incorrect parameters can prevent the operating system from booting successfully. Pinned deployments are preserved as safe recovery fallbacks.

#### Commands:
* **`status`**: List all current boot deployments (including Pinned, Staged, Booted, and Fallback states).
* **`list [index]`**: Show detailed kernel arguments for a deployment (default index: `0`).
* **`edit [index]`**: Interactively edit arguments using your configured editor (e.g., `nano` or `vi`).
* **`add <argument>`**: Append a new kernel argument parameter to the active deployment.
* **`remove <argument>`**: Delete a specific kernel argument parameter.
* **`diff [idx1] [idx2]`**: Print colorized kernel parameter diffs between two deployments (defaults to booted vs fallback).

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
