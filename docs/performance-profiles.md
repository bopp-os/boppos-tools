# Performance profiles: what was researched and why

The **Performance** page of boppos-deck (see `bopp-performance` in the README) has a *Use recommended settings* button.
It applies one of the profiles in `PERF_PROFILES` (`lib/boppos/py/boppos/performance.py`), chosen from the detected
hardware. This file records what the values are based on, so they can be checked again later.

Researched on 2026-11-09 in the current CachyOS and Bazzite sources. Only things changed or still present in 2026
were considered; anything older was left out.

**Last reviewed: 2026-11-09.** Review it again every month (see the end of this file) and change this date.

## The profiles

| Profile | Matches | Screen power saving on battery | Scheduler | CPU boost |
|---|---|---|---|---|
| `legion-go` | DMI product `83E1` (handheld) | 3 | scx_lavd, Auto | system |
| `steam-deck` | `Jupiter`, `Galileo` | system (Valve's own tuning) | scx_lavd, Auto | system |
| `handheld` | any other handheld | 2 | scx_lavd, Auto | system |
| `laptop` | laptop | 1 | kernel (BORE on CachyOS) | system |
| `desktop` | desktop | system | kernel | system |
| `vm` | virtual machine | system | kernel | system |

"system" leaves the setting to power-profiles-daemon and the kernel. Settings a machine cannot use are dropped when the
profile is applied: nothing without a battery, no screen level without an amdgpu panel
(`performance.effective`). A device profile comes before the profile of its kind. It needs both the kind and the
product to match.

To add a device: add a `Profile` with `products=("<dmi product_name pattern>",)` before the profile of its kind, and a
row in the test table in `tests/test_performance.py`. Only put a value in that was measured on the device or is in
the sources below.

## What each source says

### Adopted

| Setting | Source (read 2026-11-09) | Notes |
|---|---|---|
| scx_lavd for handhelds | CachyOS-Handheld README: lavd is the default scheduler, "intended to be used for handhelds and gaming". CachyOS wiki, Handheld Edition (updated 2026-03-17). Bazzite's `deck/shared/etc/scx_loader/config.toml`: `default_sched = "scx_lavd"`, `default_mode = "Auto"` | Both handheld editions ship it. Bazzite keeps the CachyOS copr "for the latest sched_ext schedulers" (Deck 44 post, 2026-08-19). |
| Kernel scheduler for desktop, laptop, VM | Same sources: scx is a handheld default, not a desktop one | Not a measurement: nothing found shows a gain on these. The picker still offers every scheduler. |
| Screen power saving on battery (ABM) | Bazzite `desktop/shared/usr/lib/tuned/profiles/powersave-battery-bazzite`: `panel_power_savings=3`. Measured here on a Legion Go: 18.5 W off, 17.9 W at level 3 in one back-to-back run of 10 s each, not repeated | Bazzite's own plain powersave profile sets it to 0, so the level is a battery-only choice. ppd's own `amdgpu_panel_power` action picks 0 to 3 from the profile and the battery charge, and is 0 on the charger. Level 3 is used only where measured (Legion Go). Level 2 is the generic handheld value; level 1 for laptops. |
| Keep the screen level across profile changes | power-profiles-daemon 0.30 source (`ppd-action-amdgpu-panel-power.c`) | ppd rewrites the value on every profile or power-source change; hence the watcher service, and `powerprofilesctl configure-action` to switch ppd's own panel action off. |
| CPU boost left to the system | ppd's amd-pstate driver writes `boost` itself: 0 in its power-saver profile, 1 otherwise | Turning boost off on battery stays an optional switch. It costs frame rate in CPU-heavy games, so no profile turns it on. |

### Looked at and not adopted

| Setting | Where it is used | Why not a profile default |
|---|---|---|
| GPU `power_dpm_force_performance_level=low` on battery | Bazzite `powersave-battery-bazzite` | Pins the GPU to its lowest clocks. It is a battery-saver mode, not a default; games can become unplayable. A possible opt-in switch later. |
| USB autosuspend on battery | Bazzite `powersave-bazzite/script.sh` | Can drop controllers and input devices. Needs a per-device allow-list first. |
| Wi-Fi power save | Bazzite 2026-09-13 update: "now defaults off, toggleable in gamemode settings" | Bazzite turned it off by default. It trades battery for latency and stability, so it is a user choice, not a profile value. (A Legion Go here had it on.) |
| Switch scx to `powersave` mode on battery | Bazzite `powersave-bazzite/script.sh` runs `scxctl switch -m powersave` | Only inside Bazzite's power-saver tuned profile. lavd's Auto mode already adapts to load. Worth measuring before adding. |
| `kernel.nmi_watchdog=0`, `kernel.soft_watchdog=0`, `kernel.watchdog=0` | CachyOS-Settings `70-cachyos-settings.conf` (nmi only); Bazzite `deck/shared/.../60-kernel.conf` (all three) | Image-level sysctls, not page settings. The Legion Go here already shows `nmi_watchdog=0` from cachyos-settings. |
| `kernel.split_lock_mitigate=0` | Bazzite (deck and desktop sets). CachyOS-Handheld commit "Remove split lock mitigation" (2026-02-05); its diff was not inspected | A performance and security trade-off, not battery. |
| `vm.swappiness`, zram, THP, I/O schedulers | CachyOS-Settings: swappiness 100 (150 with zram, zstd), `kyber` for NVMe since 2026-03-21, THP `defer+madvise`. Bazzite: swappiness 180, `min(ram/2, 16G)` zram | Already shipped by `cachyos-settings`; changing them is an image decision. |
| `amdgpu.ppfeaturemask=0xffffffff` | CachyOS-Handheld, Steam Deck only | Unlocks overclocking features; not a battery setting. |
| Audio power save | CachyOS-Settings `20-audio-pm.rules`: off on AC, restored on battery | Already shipped by `cachyos-settings`. |
| `mt7921e disable_aspm=Y` | Bazzite `mt7921.conf`; also in this image | Fixes Wi-Fi link drops on resume, at a small idle-power cost. Already in the image. |

## What chwd says (CachyOS hardware detection, read 2026-11-09)

`CachyOS/chwd` decides which packages a CachyOS install gets for the detected hardware. Its handheld profiles
(`profiles/pci/handhelds/profiles.toml`) set **no** power, battery, screen, ASPM, USB, TDP, boost, scheduler, sysctl,
udev or kernel-argument values. They only install packages and link audio configs:

- Generic handheld: `steamos-manager inputplumber steamos-powerbuttond`, plus `fwupd` on a Lenovo BIOS. Steam Deck adds
  `jupiter-fan-control steamdeck-dsp` (and `galileo-mura` on the OLED). The Ally and the MSI Claw get audio links
  (the Claw also loads `msi-wmi-platform`). All of this the deck image already installs.
- So the profiles here are BoppOS's own: the CachyOS-side inputs are only the lavd default and `cachyos-settings`.
- The one power-related chwd profile is `intel-lpmd` (installs and enables `intel_lpmd` on Intel hybrid CPUs). It is
  Intel only and a service, not a setting on this page, so it is not used here. Worth a look for Intel laptops and
  the MSI Claw 8 AI+ later.
- Its list of handheld DMI product names is the best public list of devices. It named Lenovo machine types
  `83E1 83L3 83N0 83N1 83N6 83Q2 83Q3` that BoppOS did not all detect as handhelds; they are now in
  `hwprofiles.HANDHELD_PRODUCTS`. Which exact model each code is was not checked, so only `83E1` has its own profile
  (the one measured); the rest get the generic `handheld` profile. Bare Ayaneo, OneXPlayer and AOKZOE names in that list
  (`AIR`, `SLIDE`, `KUN`, `NEXT`, `GEEK`, `Win600`, `Loki *`, `ONEXPLAYER F1`, ...) are still not matched by BoppOS
  detection, and chwd also requires a PCI class check that BoppOS does not do. Add them only with a vendor check.
- chwd moved the Ally, Claw and Steam Deck matching to board names or exact product names on 2026-07-09. The Ally and
  Claw are matched by board name there, but BoppOS reads the product name, which on the Ally contains `RC71L` or `RC72LA`.

## Not checked, or uncertain

- Bazzite's October 2026 release notes (pcgameshardware.de returned HTTP 403) and the 44 launch article on the same site.
- Bazzite file dates and history: the files were read from `main` without checking when each last changed.
- Parts of the Bazzite file tree (middle ranges) were summarised, not read. A missing entry there is not proof that a file
  does not exist.
- chwd's release notes: no 2026-08 release exists on GitHub (the latest tag is 1.25.0, 2026-10-23), and notes for tags
  1.13 to 1.25 could not be fetched. The Japanese news item about better handheld detection in the 2026-08 CachyOS
  release was not matched to a chwd change.
- CachyOS-Handheld and chwd ship no battery tuning of their own (see the next section). Anything claimed about
  "CachyOS handheld battery tuning" beyond the scheduler is the `cachyos-settings` package or the kernel.
- The Legion Go level-3 saving is one short A/B run. It should be repeated, and colour quality at levels 2 and 3 has only
  been looked at on that one device.

## Follow-ups that belong in the image repository

Not changed here: a `sysctl.d` file for the deck image with `kernel.soft_watchdog=0` and `kernel.watchdog=0` (Bazzite
does this on its handheld images), and a decision on Wi-Fi power save defaults. See the research notes above.

## Monthly review

Do this once a month, and after any big CachyOS or Bazzite release. Report what changed first; edit only after.

1. **CachyOS.** Read the commits since the last review of `CachyOS/CachyOS-Settings`, `CachyOS/CachyOS-Handheld` and
   `CachyOS/chwd` (`https://api.github.com/repos/<repo>/commits?per_page=30`; for chwd add `&path=profiles`), and the wiki
   pages Handheld Edition and sched-ext. Look for: sysctl, udev, modprobe and tmpfiles changes, kernel arguments, the
   default scheduler, and new device names in chwd's `handhelds/profiles.toml`.
2. **Bazzite.** Read the release posts since the last review (Universal Blue Discourse, GamingOnLinux) and the current
   `system_files/deck/shared` and `system_files/desktop/shared` files: `sysctl.d`, `etc/scx_loader/config.toml`, and the
   `tuned/profiles/powersave*` profiles. Look for: Wi-Fi power save, TDP and power stack changes, anything reverted.
3. **Upstream tools.** Release notes of power-profiles-daemon (`amdgpu_panel_power` action, boost, `configure-action`),
   scx / scx_loader (mode names, `default_mode`, `scxctl` syntax) and the Linux kernel's amdgpu panel power savings.
4. **Check each row** of the tables above: still true, changed, or reverted? Move rows between *Adopted* and *Looked at*
   when the evidence changed, and add new findings with their date. Ignore anything older than a year.
5. **Check the profiles.** Does any value in `PERF_PROFILES` no longer match the sources, or is there a new device that
   should have its own profile? Change `performance.py` and the table in `tests/test_performance.py` together.
6. **Re-measure** the Legion Go screen power saving figure when the device is at hand, and update the number.
7. Set *Last reviewed* to today, and note anything that could not be fetched under *Not checked*.
