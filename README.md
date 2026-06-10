# BoppOS Tools

A collection of custom enhancements, tools, and utilities designed specifically for BoppOS to streamline system management and administration.

## Included Tools

### `boppos-update`
A comprehensive update manager that seamlessly updates your OS (`bootc`), firmware, Flatpaks, Homebrew, and Distroboxes. It also features an interactive package diff preview to show you exactly what is changing before you apply an update.
```bash
boppos-update
```

### `bopp-tray`
A background system tray application that periodically checks for new image updates and sends desktop notifications when one becomes available. It is designed to autostart with your desktop environment.
```bash
systemctl --user enable --now bopp-tray.service
```

### `bopp-diff`
A package diff analyzer that compares the currently running system against staged or upstream `bootc` images. It provides a clear, detailed breakdown of all upgraded, downgraded, added, or removed packages.
```bash
sudo bopp-diff
```

### `install-optional-flatpaks`
An interactive script that fetches, allows customization of, and installs a curated list of essential Flatpak applications sourced from Bazzite-DX and BoppOS recommendations.
```bash
install-optional-flatpaks
```

### `bopp-kargs`
A kernel arguments manager for atomic deployments. It provides an easy-to-use interface to view, edit, add, remove, and diff Boot Loader Specification (BLS) kernel arguments safely.

> ⚠️ **Warning:** Modifying kernel arguments directly affects how the OS boots. Incorrect arguments can render your system unbootable. Use with caution.
```bash
sudo bopp-kargs help
```

### `bopp-tpm-refresh`
A utility to easily re-enroll LUKS/TPM2 decryption keys, which is often required after significant system or kernel updates to ensure seamless boot decryption.

> ⚠️ **Warning:** This tool interacts directly with system encryption configurations and modifies `/etc/crypttab` to bind decryption to the TPM.
```bash
bopp-tpm-refresh
```

## Migration Tool (`bopp-migrate`)

⚠️ **WARNING: HIGHLY EXPERIMENTAL AND POTENTIALLY DESTRUCTIVE** ⚠️

BoppOS includes an experimental migration script (`bopp-migrate`) designed to help users transition their `/etc` and `$HOME` configurations from a Fedora-based atomic OS (like Bazzite or Bluefin) to this Arch-based BoppOS image. 

**Do NOT run this script unless you fully understand what it does.** It will forcefully manipulate user IDs, group IDs, and move hidden configuration folders in your home directory into a backup "Vault". While it attempts to preserve critical data (like SSH keys, browser profiles, and game data), **it is largely untested and could result in a broken system or data loss.** Always ensure you have a separate, verified backup of your home directory before attempting a migration.

To use the migration tool, run it with `sudo` after booting into BoppOS for the first time:

```bash
sudo bopp-migrate
```
