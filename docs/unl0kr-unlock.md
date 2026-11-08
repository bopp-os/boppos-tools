# Unlocking the disk on a handheld (`boppos-unl0kr` dracut module)

BoppOS unlocks LUKS with the TPM2 (`rd.luks.options=tpm2-device=auto`). When that fails (firmware update,
Secure Boot change, a wiped TPM) systemd asks for the passphrase, and a Steam Deck or Legion Go has no
keyboard to type it on. This module adds [unl0kr](https://gitlab.postmarketos.org/postmarketOS/buffybox)'s
on-screen keyboard to the initramfs: tap it on the touchscreen, or drive it with the trackpad or a mouse.
A physical keyboard keeps working.

| Installed to | What |
|---|---|
| `/usr/lib/dracut/modules.d/90boppos-unl0kr/` | `module-setup.sh`, the password agent and its `.path`/`.service` units |

It ships in `boppos-boot-theme` (`make install-unl0kr-dracut`); `unl0kr` itself is an optional dependency
packaged in bopp-os/pkgbuilds. Nothing changes until an image asks for the module.

## Enabling it (in the image build)

1. Install `unl0kr` (with its DRM backend, the buffybox default) next to `boppos-boot-theme`.
2. Add the module to the image's dracut config, e.g. `/usr/lib/dracut/dracut.conf.d/50-boppos.conf`:

   ```bash
   add_dracutmodules+=" boppos-unl0kr "
   ```

3. Build the initramfs as before. `check()` returns 255, so the module is only ever included when listed;
   if `unl0kr` is missing dracut stops with *module 'boppos-unl0kr' cannot be installed* instead of building
   an initramfs without a way in.

## How it works

- **TPM2 first.** The agent's `.path` unit only fires when a file appears in `/run/systemd/ask-password/`,
  which systemd-cryptsetup only writes after the TPM2 token failed. A working TPM2 boot never starts unl0kr.
- **The agent** (`boppos-unl0kr-agent`, bash) speaks systemd's
  [password agent protocol](https://systemd.io/PASSWORD_AGENTS/): for each `ask.*` file it runs
  `unl0kr -m "<Message>"` and hands the result to `systemd-reply-password`. A wrong passphrase makes
  cryptsetup ask again with a new file, which gets a new unl0kr. If the prompt is answered elsewhere or
  times out, unl0kr is closed. The passphrase only passes through a 0600 file in `/run` (RAM) for the
  moment it takes to reply.
- **Plymouth stays, and hands over.** Per prompt the agent runs `plymouth deactivate` (plymouthd drops its
  DRM master and stops reading the keyboard), runs unl0kr on the DRM backend, then `plymouth reactivate`,
  which brings the splash back with its wheel while the disk unlocks. Plymouth's own agent is masked in the
  initrd, because its password screen stayed up after unl0kr had answered. It is still the fallback: if unl0kr
  fails (no display, no input, a crash) the agent starts it, and a keyboard can answer the prompt there. After two failures
  in a row the agent stops trying for that boot. The first prompt can come before the GPU driver has
  loaded, so the agent waits up to 20 seconds for `/dev/dri/card*` (or `/dev/fb0`), and an unl0kr that
  exits within 5 seconds of starting is started again, up to 3 times. (buffybox's own `unl0kr-agent`, built with
  `-Dsystemd=true`, refuses to run while Plymouth is up, which is why this module has its own.)
- **Input.** The module adds USB HID (`usbhid`, `hid-generic`: the Deck controller in lizard mode, where the
  right trackpad is a mouse, USB keyboards and mice), `hid-multitouch` over `i2c-hid-acpi` with the AMD
  DesignWare I2C controller and `pinctrl-amd` (the Deck and Legion Go touchscreens), and `hid-lenovo-go` when
  the kernel has it; libinput's quirks and udev rules; and the XKB data, without which unl0kr cannot compile
  a keymap for a hardware keyboard.
- **The on-screen keyboard always shows.** unl0kr normally hides it when any keyboard device exists, and
  the Deck's controller in lizard mode presents one. The module sets `autohide=false` unless
  `/etc/unl0kr.conf` sets `autohide` itself.
- **Config.** `/etc/unl0kr.conf` is copied in as is; the module layers `/etc/boppos-unl0kr.conf`
  (`backend=drm`, dark theme) over it with `-C`. A `backend=` set in `/etc/unl0kr.conf` wins, and an unl0kr
  built without DRM keeps fbdev (with a build-time warning: Plymouth may then draw over it).

**Known limit:** unl0kr has no rotation setting. Both handhelds have portrait-native panels, so expect the
keyboard drawn sideways; touch still lands where it is drawn. Check this first on hardware.

## Testing

`tests/test_unl0kr_dracut.py` sources `module-setup.sh` with stub dracut helpers and runs the agent against a
fake ask-password directory (prompt answered, unl0kr failing, prompt answered elsewhere, stale prompts,
Plymouth handover).

### In a VM (docker or podman, no hardware)

`tools/vm-test-unl0kr` builds a container with a kernel, dracut, Plymouth (with the BoppOS theme), swtpm and
QEMU, plus unl0kr (built from the buffybox 3.6.0 tag, or `--unl0kr-pkg FILE` for your own package). It then
builds a real systemd initramfs with the module and boots it against small LUKS2 test disks. The container needs
no `--privileged` and touches nothing on the host's boot; `/dev/kvm` is passed in when present (without it each
boot takes minutes).

```bash
tools/vm-test-unl0kr
```

| Case | Checks |
|---|---|
| `tpm` | A TPM2-enrolled disk (emulated TPM) unlocks on its own; unl0kr never starts |
| `keyboard` | Without the TPM2 key unl0kr appears over the paused splash; a passphrase typed on the USB keyboard unlocks |
| `retry` | A wrong passphrase gets a second unl0kr prompt; the right one then unlocks |

`--portrait` uses an 800x1280 screen like the handhelds; `--interactive` boots one VM with a VNC screen on
`127.0.0.1:5900` so you can click the on-screen keyboard yourself (the VM has a USB tablet and a virtio
multitouch device). Serial logs and screenshots of each step land in `./vm-test-out/<case>/`. The VM cannot
cover the handhelds' own input drivers (i2c-hid, lizard mode, the Legion Go controllers) or amdgpu.

### On hardware

**1. Check the initramfs contents** (any machine with dracut, `unl0kr` and this package; nothing is booted):

```bash
sudo dracut --force --no-hostonly --add boppos-unl0kr --kver "$(uname -r)" /tmp/unl0kr-test.img
```

```bash
lsinitrd /tmp/unl0kr-test.img | grep -E 'unl0kr|boppos-unl0kr-agent|reply-password|X11/xkb/rules/evdev$|hid-multitouch|i2c-hid-acpi'
```

**2. Try the keyboard and agent on the running system.** Switch to a text console (Ctrl+Alt+F3, so the
compositor lets go of the display), log in, and start the agent once:

```bash
sudo BOPPOS_UNL0KR_ONESHOT=1 /usr/lib/dracut/modules.d/90boppos-unl0kr/boppos-unl0kr-agent
```

Then raise a prompt from an SSH session (the agent only looks once, so start the prompt first, then the
agent):

```bash
sudo systemd-ask-password --no-tty "unl0kr test"
```

What you type in unl0kr comes back on the SSH side. Try the touchscreen, the trackpad and a USB keyboard.

**3. A real unlock.** Build a test image with the module enabled (above), boot it, and make the TPM2 unlock
fail by removing the TPM2 key slot (you need the disk passphrase, and `bopp-tpm-refresh` puts it back).
First find the LUKS partition, the same way `bopp-tpm-refresh` does:

```bash
lsblk -l -p -o NAME,FSTYPE | grep crypto_LUKS
```

Then wipe its TPM2 slot (replace `/dev/nvme0n1p3` with the name printed above):

```bash
sudo systemd-cryptenroll --wipe-slot=tpm2 /dev/nvme0n1p3
```

Reboot: unl0kr should appear over a paused splash. Afterwards, re-enroll and reboot once more to confirm the
TPM2 path still unlocks without any prompt:

```bash
sudo bopp-tpm-refresh
```

The agent logs to the journal: `journalctl -b -u boppos-unl0kr-agent.service`.
