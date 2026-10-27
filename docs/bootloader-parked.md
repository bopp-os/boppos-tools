# Bootloader work: parked, to move to its own project

**Decision (2026-10-26):** everything about the bootloader, Secure Boot and boot artwork leaves this repo later.
Until then this repo's focus is the management app (`boppos-deck` and the `bopp-*` tools). Nothing below blocks it.

## What is in this repo today and should move
| Path | What |
|---|---|
| `boot/plymouth/boppos/`, `boot/grub/boppos/` | Plymouth splash and GRUB theme (PNG art is committed) |
| `boot/grub/bopp-grub-theme` | installs the GRUB theme on `/boot` (status / install / remove, `--dry-run`) |
| `tools/render-boot-art` | renders the boot PNGs from the logo SVGs |
| `tests/test_boot_theme.py`, `tests/test_grub_theme.py`, `tests/fixtures/fedora-grub.cfg` | their tests |
| `docs/boot-theme.md`, the `boppos-boot-theme` package in `packaging/arch/` and `install-boot-theme` in the Makefile | docs and packaging |

The logo SVGs in `artwork/` stay: they are brand assets, not bootloader work.

## What we know about the reference machine
- Converted from Bazzite (Fedora Atomic) to the CachyOS-based image; the bootloader is **still Fedora's**:
  GRUB 2.12, `EFI/fedora` on the ESP, a static `/boot/grub2/grub.cfg` from April 2025 (made by `grub2-mkconfig`,
  `terminal_output console`, sources `${prefix}/user.cfg` from `01_users`, has a `load_video` function).
- `/boot` is a separate **ext4** partition, the ESP is FAT at `/boot/efi`; kernels are ostree BLS entries in `/boot/loader.0`.
- `bootupd` is **not installed**; `/boot/bootupd-state.json` is a leftover. Nothing updates the bootloader.
- **Secure Boot is disabled** (it was on under Bazzite; signing the kernel pieces on CachyOS was the problem).
  TPM2 works and the image uses TPM auto-unlock (`rd.luks.options=tpm2-device=auto`).

## Goal
Get **Secure Boot working** again, and have the bootloader updated automatically with the image.

## Options already looked at (facts checked, not yet tried)
- **bootupd**: AUR only (`bootupd` 0.2.34). Its [package-system code](https://github.com/coreos/bootupd/blob/main/src/packagesystem.rs)
  is meant to work with other package managers through a script. It updates what it ships, so we would have to ship
  shim and GRUB ourselves; Arch's `shim` (extra) is not Microsoft-signed.
- **systemd-boot / `bootctl`**: `bootc install --bootloader systemd` exists (values: `grub`, `grub-cc`, `systemd`,
  `ukiboot`, `none`). `systemd-boot-update.service` refreshes the bootloader at boot from `/usr`, which suits an image.
  Needs the boot files on FAT (it cannot read ext4): fine for new installs, a repartition for the reference machine.
  How bootc lays the disk out for this mode is **not verified**.
- **Secure Boot** would mean signing our own bootloader and kernel/UKIs (`sbctl`); bootc's `ukiboot` looks like the
  long-term route. Not investigated.

## Open questions for that project
1. Secure Boot with our own keys (`sbctl`) or a signed shim? Who holds the keys?
2. systemd-boot for new installs: what does `bootc install --bootloader systemd` produce, and does
   `systemd-boot-update.service` work with a read-only `/usr`?
3. What to do about machines that came from Fedora-based installs (stale GRUB, `bootupd-state.json`)?
4. TPM auto-unlock and PCR choices when the bootloader or Secure Boot state changes.
