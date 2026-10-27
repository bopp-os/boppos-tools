# Research: more kargs and Flatpaks to consider (2026-10-27)

What was checked, so the confidence of each line is clear:

- **Verified in source:** upstream Bazzite `main` (b00fd74), cloned and grepped; CachyOS-Settings `master` (f79b237), cloned and grepped; this repo; the cachyos image's `base.yaml`.
- **Verified live:** every Flatpak ID below was looked up on the Flathub API on 2026-10-27 (exists, name, licence, latest release).
- **Forum or search results only (marked "unverified"):** SteamOS's own kargs, the CachyOS installer's cmdline, whether a karg is still needed on a 7.x kernel.
- **Not done:** nothing was booted or installed. No karg was tested on hardware. Valve's `grub-steamos` file was not read.

## 1. Kargs

### 1a. Candidates for the image defaults (your rule: Bazzite and/or CachyOS ship it)

| Karg | Who | Verdict |
|---|---|---|
| `bluetooth.disable_ertm=1` | Bazzite applies it on every machine (`bazzite-hardware-setup:105-106`) | Candidate, but test first. It exists to let Xbox controllers pair. A NixOS commit applies it only below kernel 5.12 (search result, unverified), so it may do nothing on the image's 7.x kernel. Check Xbox pairing without it; if that works, skip. It could also be a modprobe option instead. |
| `quiet`, `splash` | CachyOS installs show them (user reports, unverified) | Needed for the BoppOS Plymouth theme; see the earlier discussion. Not on the image today. |
| `zswap.enabled=0` | CachyOS (reports) | Skip. The image has `zram-generator` and `cachyos-settings`, which keeps zswap off anyway. |

Nothing else Bazzite or CachyOS sets statically is missing from the image.

### 1b. Presets worth adding (off by default, optional)

| Preset | Karg | Fits | Risk | Source / note |
|---|---|---|---|---|
| AMD: PSR off for flicker | `amdgpu.dcdebugmask=0x10` | AMD graphics | low | Kernel doc: 0x10 disables Panel Self Refresh. Reports on Radeon 680M, Navi 31 (Fedora, CachyOS, Framework threads) say it fixes flicker or artifacts; some needed `0x410` or `0x600` instead. Costs battery (display stays in a high-power state). |
| AMD: HDMI 2.1 / FreeSync features | `amdgpu.dcfeaturemask=0x402` | AMD graphics | medium | Bazzite `ujust configure-amd-hdmi21`, which warns of flicker or instability on some displays. Bazzite's description only; not independently checked. |
| Intel Ethernet (igc) fix | `pcie_aspm=off pcie_aspm.policy=performance` | any (cannot detect the NIC yet) | medium | Bazzite `manage-intel-ethernet`. Raises idle power. |
| PCIe power-management fix | `pcie_port_pm=off` | any | medium | Bazzite `manage-pcie-power`. |
| Intel: sleep fix (gen 7/8) | `i915.enable_dc=0` | Intel graphics | low | Bazzite `toggle-i915-sleep-fix`: avoids random reboots or failed suspend; costs power. |
| Virtual machines | `kvm.ignore_msrs=1 kvm.report_ignored_msrs=0` | any | low | Added by Bazzite's virt installer. |
| Sleep: use s2idle | `mem_sleep_default=s2idle` | laptops, handhelds | low | The counterpart of the existing `20-sleep-deep`, for machines that fail to resume from deep sleep. Not in Bazzite or CachyOS; from general knowledge. |
| Watchdog fully off | `modprobe.blacklist=iTCO_wdt` (Intel CPU) or `sp5100_tco` (AMD CPU) | by CPU vendor | low | Bazzite `configure-watchdog` pairs these with `nowatchdog`. The image's `nowatchdog` alone leaves those modules loaded. Needs CPU vendor detection, which `kargs.fit` does not have. |
| NVIDIA: resume fixes | `nvidia-drm.fbdev=1`; `nvidia.NVreg_PreserveVideoMemoryAllocations=1` | NVIDIA graphics | medium | Forum reports only. Gentoo notes call `fbdev` experimental. The second also needs the `nvidia-suspend`, `-resume` and `-hibernate` services; check the image has them first. |

### 1c. Per-model fixes Bazzite applies on its own (use `match.*` presets with `enabled = true`)

`bopp-kargs apply` already applies enabled presets that match this device, so these need no new mechanism.

| Model (DMI) | Karg | Why |
|---|---|---|
| ONEXPLAYER F1 / F1 EVA-01 | `video=eDP-1:panel_orientation=left_side_up`, `plymouth.use-simpledrm=0`, `loglevel=0` | Portrait-native panel, so the splash shows sideways without it |
| SLIDE | `acpi=strict` | Stops random reboots |
| AOKZOE A1 AR07 | `drm.edid_firmware=eDP-1:edid/aokzoea1ar07_edid.bin` | Needs the EDID file shipped too |
| Framework laptops with Intel CPUs | `module_blacklist=hid_sensor_hub` | Keyboard fix |
| Valve and other handhelds | `amdgpu.ppfeaturemask` ORed with `0x4000` | Bazzite ORs it into the module's current value at run time, which a static preset cannot do. The existing static `0xffffffff` is much broader. |

On Valve and handhelds Bazzite also removes `amd_iommu=off` if present.

### 1d. Not recommended

- SteamOS's own kargs (`amdgpu.gttsize=8128`, `spi_amd.speed_dev=1`, `amd_iommu=off`, `audit=0`, `fbcon=vc:4-6`; forum-sourced, unverified). They are Steam Deck firmware workarounds, and Bazzite does not copy them.
- `nomodeset` (Bazzite deletes it), `acpi_osi=` tricks, and anything that weakens security.
- `reserve_mem=64K:4096:ramoops pstore.backend=ramoops` (Bazzite crash-log recipe): it also needs a sysctl and modprobe files, so it is not a pure karg preset.

## 2. Flatpaks

All IDs below exist on Flathub with a recent release unless noted. "Native" means the image already installs it; do not offer a duplicate (see the `# native:` marker idea).

### 2a. Strong fits for the main list

| App (ID) | Why |
|---|---|
| Mission Center (`io.missioncenter.MissionCenter`) | Bazzite preinstalls it. System monitor with GPU graphs. `net.nokyan.Resources` is the alternative; pick one. |
| Trayscale (`dev.deedles.Trayscale`) | The image installs `tailscale` natively with no GUI. |
| Steam ROM Manager (`com.steamgriddb.steam-rom-manager`) | Adds emulated and non-Steam games to Steam; fits handhelds and the Steam shortcut feature. |
| Bottles (`com.usebottles.bottles`) | Windows apps and games outside Steam. |
| A PDF viewer: Papers (`org.gnome.Papers`) or Okular (`org.kde.okular`) | The lists have no PDF viewer. Which one is already native depends on the flavor. |
| VLC (`org.videolan.VLC`) | The image has `vlc-plugins-all` but I did not find the `vlc` app itself in `base.yaml`. Recheck before adding. |
| Firmware (`org.gnome.Firmware`) | Bazzite preinstalls it. Ties in with the wizard's firmware check. Latest release Oct 2025. |

### 2b. Good optional extras

- **Gaming:** Vesktop (`dev.vencord.Vesktop`, Discord client with working screen-share audio; the official Discord is proprietary), Prism Launcher (`org.prismlauncher.PrismLauncher`), Cartridges (`page.kramo.Cartridges`; the old `hu.kramo.Cartridges` ID is a 404), Ludusavi (`com.github.mtkennerly.ludusavi`, game save backup), AntiMicroX (`io.github.antimicrox.antimicrox`), AdwSteamGtk (`io.github.Foldex.AdwSteamGtk`).
- **Emulators the lists lack:** Azahar for 3DS (`org.azahar_emu.Azahar`), RMG for N64 (`com.github.Rosalie241.RMG`), shadPS4 (`net.shadps4.shadPS4`, early), Eden for Switch (`dev.eden_emu.eden`; legally grey, your call). I left out Ryujinx (Ryubing) for the same reason.
- **Browsers (the list has only Mullvad):** LibreWolf (`io.gitlab.librewolf-community`), Brave (`com.brave.Browser`), Zen (`app.zen_browser.zen`), Chromium (`org.chromium.Chromium`). Firefox is native.
- **Messaging and remote:** Signal (`org.signal.Signal`), Telegram (`org.telegram.desktop`), RustDesk (`com.rustdesk.RustDesk`), Remmina (`org.remmina.Remmina`).
- **Creative:** Krita (`org.kde.krita`), Blender (`org.blender.Blender`), HandBrake (`fr.handbrake.ghb`), darktable (`org.darktable.Darktable`), Shotcut (`org.shotcut.Shotcut`).
- **Productivity:** KeePassXC (`org.keepassxc.KeePassXC`), Obsidian (`md.obsidian.Obsidian`, proprietary), OnlyOffice (`org.onlyoffice.desktopeditors`), Anki (`net.ankiweb.Anki`), Xournal++ (`com.github.xournalpp.xournalpp`), Cryptomator (`org.cryptomator.Cryptomator`).
- **Developer:** VSCodium (`com.vscodium.codium`), VS Code (`com.visualstudio.code`, proprietary), Zed (`dev.zed.Zed`), Podman Desktop (`io.podman_desktop.PodmanDesktop`), Wireshark (`org.wireshark.Wireshark`).
- **Hardware:** OpenTabletDriver (`net.opentabletdriver.OpenTabletDriver`), RazerGenie (`xyz.z3ntu.razergenie`), Polychromatic (`app.polychromatic.controller`), Solaar for Logitech (`io.github.pwr_solaar.solaar`), Helvum audio patchbay (`org.pipewire.Helvum`).

### 2c. Skip

- **Already native:** Firefox, Steam, Protontricks, LACT, OpenRGB, CoolerControl, MangoHud, GOverlay, vkBasalt, Waydroid, distrobox.
- **Duplicates of apps already listed:** ProtonUp-Qt (ProtonPlus), DistroShelf (BoxBuddy), Mango Juice (GOverlay), Jellyfin Media Player (Jellyfin Desktop is already listed).
- **Stale:** Piper (`org.freedesktop.Piper`), last release Sep 2024.
- **Not on Flathub under the IDs I tried (404):** EmuDeck, GeForce NOW, CoolerControl's Flatpak, ES-DE. Bazzite installs the first two another way; ES-DE may ship as an AppImage (unverified).
- Flatpak apps can only use MangoHud if the Flatpak layer runtime `org.freedesktop.Platform.VulkanLayer.MangoHud` is installed. Bazzite preinstalls it with `vkBasalt` and `OBSVkCapture`. Worth adding only if Flatpak games are expected; I did not check those runtime IDs against Flathub.

### 2d. Existing entries

All 47 IDs in the two current lists exist on Flathub (checked), so none is stale.
