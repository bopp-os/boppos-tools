# First run: kernel-argument presets and the installer handoff (design, 2026-10-28)

The marker, the account rule and the entry point are now specified in [first-run.md](first-run.md); this file keeps the
preset design and its open questions.

Status: **design only**. Nothing here is built yet. Open questions are listed at the end and are not answered here.

## What the installer will do

- It passes the kernel arguments of the presets the user picked as `bootc install --karg <arg>` (one per argument).
  On the ostree backend they land in the first deployment's boot entry (`/boot/loader/entries/*.conf`) and in the
  generated grub.cfg.
- It writes `/var/lib/boppos/first-run` with `mode=welcome` (a user account was created) or `mode=oobe` (no account).

## What exists today (facts from the code)

| Piece | What it does |
|---|---|
| `boppos-oobe.service` + `libexec/boppos/oobe-needed` | Runs `boppos-deck --first-run` on tty1 before the login screen, whenever there is no real account (the marker is not needed). |
| Account setup (`oobe.py`, `ui/oobe.py`, `oobe-helper`; `--first-run` with no account, or `--oobe`) | Language, network, time zone, account, start-in. It **never touches kernel arguments**. `finish-setup` starts the display manager; the marker stays, and the tour runs at the first sign-in. |
| Welcome tour (`--first-run` with an account, or `--welcome`; started by `autostart/bopp-welcome.desktop`) | Per sign-in while the marker exists; finishing removes it (and still writes `$XDG_STATE_HOME/boppos-deck/welcome-done` for `--welcome --if-needed`). The Hardware step lists every preset from `welcome.karg_options()`, ordered by `kargs.fit` (fits, optional, risky, unsupported). `72-amdgpu-overclock` gets a switch of its own. None is preselected (`recommended()` never returns a preset). Finish runs `bopp-kargs --json enable <name>` for each picked preset. |
| boppos-deck, Kernel arguments page (`ui/pages.py`) | Uses `bopp-kargs --json dropins` and `kargs.sections()`. The "on" section holds presets that are `enabled`, **or** `already_active` (all their kargs are on the running cmdline but not in `/var/lib/bopp-kargs/managed`). A preset that is `already_active` but not `enabled` is switched off by removing its arguments one by one (`bopp-kargs remove`). Every other preset is switched with `bopp-kargs enable/disable`. |
| `bopp-kargs apply/enable/disable` (`kargs.apply_dropins`) | Wanted = the kargs of enabled presets whose `match.*` (DMI) fits. It removes only tokens listed in `/var/lib/bopp-kargs/managed`. |

## (a) Showing the presets the installer already enabled

The boppos-deck page already does the right thing: a preset whose kargs are all on the running cmdline is
`already_active` and shows under "on". **The welcome wizard does not**. `karg_options()` never reads the cmdline, so
it offers the installer's presets as if they were off. Proposed: `KargOption` gains `active` (from the same
`already_active` rule, via `bopp-kargs --json dropins`), and the wizard shows those switched on and labelled
"Set up by the installer".

## (b) Switching presets on or off through bopp-kargs

**A gap to close first:** kargs the installer passed are never *owned* by bopp-kargs. In `apply_dropins`, `owned`
is `managed ∩ wanted` plus `to_add` (`kargs.py`, `apply_dropins`). A preset whose kargs are already on the cmdline
adds nothing, so its kargs never reach `managed`. Left alone, that is harmless: boppos-deck shows the preset as
`already_active` and switches it off with `bopp-kargs remove`. It breaks once something runs `enable` on it, and the
welcome wizard does exactly that for every preset the user leaves on. After that the preset is `enabled`, the
boppos-deck switch calls `disable`, and `disable` removes nothing. Concretely: install with
`--karg usbhid.mousepoll=1`, then `bopp-kargs enable 60-usb-mouse-1khz`, then `disable` → the argument stays.

Proposed fix (to build later; choose one, see the questions):
1. **Adopt on first run:** a new `bopp-kargs adopt <preset>…` (root helper verb). For each named preset that is
   `already_active`, it writes the `/etc` enabled override and adds its kargs to `managed`, without changing the
   cmdline. The first-run consumer (c) calls it with the presets the installer enabled.
2. **Adopt on enable:** `apply_dropins` also counts as owned the tokens of presets that were *just switched on* and
   are already present. This needs no new verb, but it adopts a karg the user set by hand if it happens to match.

Either way, switching then goes through `bopp-kargs enable/disable` as today. With the fix in the previous commit,
each switch rewrites the full list with `ostree admin instutil set-kargs`, which regenerates grub.cfg.

**A second gap, for presets on by default:** `wanted_tokens` checks only `match.*` (DMI), not the graphics vendor.
`kargs.fit` knows the vendor, but only the UI uses it. A graphics preset with `enabled = true` would be applied to
every machine. All current presets on by default (90–92) match by DMI, so nothing is affected today. The list below
proposes on-by-default presets for AMD (graphics and CPU), so `wanted_tokens` needs the GPU rule, plus a CPU-vendor
`match` that does not exist yet.

## (c) Consuming `/var/lib/boppos/first-run`

Proposed flow (each step only when the marker exists):

- **mode=oobe:** `boppos-oobe.service` runs as today. The marker can make `oobe-needed` stricter (run only when the
  marker says `oobe`), so an image booted without the installer does not start the setup by accident. After the
  account is created, the welcome wizard runs at the user's first login, as for `mode=welcome`.
- **mode=welcome:** OOBE stays skipped (there is a user). The welcome wizard at first login shows the installer's
  presets as on (a).
- **Adopting the installer's presets (b)** needs root and must happen once per system, not per user. Candidates: the
  oobe-helper (only covers `mode=oobe`), a small root oneshot unit `ConditionPathExists=/var/lib/boppos/first-run`,
  or the kargs-helper the first time welcome runs.
- **Deleting the marker:** whoever adopts last. If it stays, nothing must act on it twice: adopt is idempotent.

## The full preset list

The image's own kargs file is down to one entry, so the presets are now the only source of the tuning kargs it used
to set. Below: the 23 presets that exist, then a proposed preset for each former image default (the list in
`tests/test_kargs.py`, `test_every_image_default_from_the_image_repo_is_described`). "Default" is a suggestion for the
installer's preselection, not a decision.

### Existing (`kargs.d/`)

| Preset | kargs | Risk | On by default | Match |
|---|---|---|---|---|
| 10-nvme-no-apst | `nvme_core.default_ps_max_latency_us=0` | low | no | any |
| 20-sleep-deep | `mem_sleep_default=deep` | medium | no | any |
| 30-threaded-irqs | `threadirqs` | low | no | any |
| 40-amd-sg-display-off | `amdgpu.sg_display=0` | low | no | AMD graphics |
| 41-intel-psr-off | `i915.enable_psr=0` | low | no | Intel graphics |
| 50-pci-noaer | `pci=noaer` | medium | no | any |
| 60-usb-mouse-1khz | `usbhid.mousepoll=1` | low | no | any |
| 70-amdgpu-no-runpm | `amdgpu.runpm=0` | low | no | AMD graphics |
| 71-nvidia-drm-modeset | `nvidia-drm.modeset=1` | low | no | NVIDIA |
| 72-amdgpu-overclock | `amdgpu.ppfeaturemask=0xffffffff` | high | no | AMD graphics |
| 73-amd-psr-off | `amdgpu.dcdebugmask=0x10` | low | no | AMD graphics |
| 74-amd-hdmi21-freesync | `amdgpu.dcfeaturemask=0x402` | medium | no | AMD graphics |
| 75-intel-sleep-fix | `i915.enable_dc=0` | low | no | Intel graphics |
| 76-nvidia-fbdev | `nvidia-drm.fbdev=1` | medium | no | NVIDIA |
| 77-nvidia-keep-vram | `nvidia.NVreg_PreserveVideoMemoryAllocations=1` | medium | no | NVIDIA |
| 78-pcie-aspm-off | `pcie_aspm=off pcie_aspm.policy=performance` | medium | no | any |
| 79-pcie-port-pm-off | `pcie_port_pm=off` | medium | no | any |
| 80-intel-iommu-on | `intel_iommu=on` | medium | no | any |
| 81-kvm-ignore-msrs | `kvm.ignore_msrs=1 kvm.report_ignored_msrs=0` | low | no | any |
| 82-sleep-s2idle | `mem_sleep_default=s2idle` | low | no | any |
| 90-onexplayer-f1-panel | `video=eDP-1:panel_orientation=left_side_up plymouth.use-simpledrm=0 loglevel=0` | low | **yes** | ONEXPLAYER F1 |
| 91-ayaneo-slide-acpi-strict | `acpi=strict` | low | **yes** | AYANEO SLIDE |
| 92-framework-intel-keyboard | `module_blacklist=hid_sensor_hub` | low | **yes** | Framework, Intel |

The three "yes" presets are only applied when something runs `bopp-kargs apply`. Nothing on an installed system runs
it by itself, so the installer has to pass them (see the questions).

### Proposed: the former image defaults as presets

None of these is on the forbidden list (`kargs_info.forbidden_reason`) or boot-critical.

| Proposed preset | kargs | Category | Risk | Suggested default | Match |
|---|---|---|---|---|---|
| 11-amd-pstate-active | `amd_pstate=active` | Performance | low | on | AMD CPU (needs a CPU-vendor match) |
| 12-amdgpu-gpu-recovery | `amdgpu.gpu_recovery=1` | Stability | low | on | AMD graphics |
| 31-split-lock-off | `split_lock_detect=off` | Performance | low | on | any |
| 32-watchdogs-off | `nowatchdog nmi_watchdog=0` | Performance | low | on | any |
| 33-preempt-full | `preempt=full` | Performance | low | on | any |
| 34-thp-madvise | `transparent_hugepage=madvise transparent_hugepage.defrag=defer+madvise` | Performance | low | on | any |
| 35-iommu-passthrough | `iommu=pt` | Performance | low | on | any |
| 61-usb-no-autosuspend | `usbcore.autosuspend=-1` | Stability | low | on (desktops); costs battery on handhelds and laptops | any |
| 62-sysrq-always | `sysrq_always_enabled=1` | Troubleshooting | medium (anyone at the keyboard can kill processes or reboot) | open | any |
| 36-mitigations-off | `mitigations=off` | Performance | high (removes Spectre/Meltdown protection) | off | any |
| 72-amdgpu-overclock (exists) | `amdgpu.ppfeaturemask=0xffffffff` | Performance | high | was on for everyone as an image default, is off as a preset | AMD graphics |
| — | `rd.luks.options=tpm2-device=auto` | not a tuning preset: it belongs to disk encryption (bopp-tpm-refresh) | — | — | — |

Follow-ups this list implies, once decided: `kargs-info.json` marks all 14 tokens `image_default: true`, which
becomes wrong for every token the image no longer sets. The test above has to change with it.

## Open questions

1. Which single kargs entry did the image keep? It decides what moves into presets, and whether
   `rd.luks.options=tpm2-device=auto`, `quiet` and `splash` are the image's job or the installer's.
2. Should the marker name the presets (e.g. `presets=60-usb-mouse-1khz,…`), or should first run work it out from the
   cmdline (`already_active`)? The cmdline route cannot tell an installer preset from a karg set some other way.
3. Adopt with an explicit verb (option 1) or adopt on enable (option 2)?
4. Which unit or helper consumes the marker for `mode=welcome`, where no root setup step runs before the user logs
   in? Who deletes the marker?
5. Should the installer pass the on-by-default device presets (90–92), and the proposed on-by-default ones? Or should
   the system run `bopp-kargs apply` once on first boot?
6. On `bootc upgrade` with the ostree backend, are `--karg` kargs from install time carried forward as user kargs,
   separate from the image's `kargs.d`? To be checked on the VM: install with a preset, upgrade, compare
   `/proc/cmdline`.
7. A CPU-vendor match for `amd_pstate=active`: a new `match.cpu_vendor` key (from `/proc/cpuinfo`), or leave it
   unmatched (it is ignored on Intel)?
8. Should `mitigations=off` and `sysrq_always_enabled=1` be offered at all, or go on the forbidden list now that they
   would be user choices rather than image defaults?
9. With `mode=oobe`, should `oobe-needed` require the marker, so an image that was not put on disk by the installer
   never starts the setup?
