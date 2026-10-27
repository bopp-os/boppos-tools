# BoppOS boot artwork (`boppos-boot-theme`)

A black screen, the white BoppOS mark breathing slowly in the middle, and the small horizontal wordmark near
the bottom. One package, two themes.

| Installed to | What |
|---|---|
| `/usr/share/plymouth/themes/boppos/` | Plymouth splash (`boppos.plymouth`, `boppos.script`, `mark.png`, `lockup.png`) |
| `/usr/share/grub/themes/boppos/` | GRUB theme (`theme.txt`, `mark.png`, `lockup.png`) |

The logo SVGs themselves (`boppos-logo-{horizontal,stacked}-{white,black}.svg`) are in `boppos-artwork`. The PNGs
in `boot/` are rendered from them by `tools/render-boot-art` (needs `rsvg-convert`) and committed, so packaging
needs no extra tools.

**Neither theme has been run on a real boot yet.** The static checks in `tests/test_boot_theme.py` catch missing
files, unbalanced braces and undefined callbacks, not how it looks. Please run the preview below first.

## Plymouth

The package only installs the theme. Selecting it is the image's job, because the initramfs has to be built
with it. The image already installs `plymouth` and its dracut config already adds the `plymouth` module, so in the
image build, **after** `boppos-boot-theme` is installed and **before** the initramfs is generated:

```bash
plymouth-set-default-theme boppos      # writes Theme=boppos to /etc/plymouth/plymouthd.conf
```

(`cachyos-plymouth-theme` can stay installed; it just stops being the selected one.)

### Preview without rebooting

From a text console (Ctrl+Alt+F3, log in), with the graphical session stopped so Plymouth can use the display:

```bash
sudo systemctl isolate multi-user.target
sudo plymouth-set-default-theme boppos
sudo plymouthd --debug --debug-file=/tmp/plymouth.log
sudo plymouth --show-splash
sudo plymouth ask-for-password --prompt="Enter disk passphrase"   # the passphrase dialog; type some characters
sudo plymouth display-message --text="Waiting for the disk"
sleep 10
sudo plymouth --quit
sudo systemctl isolate graphical.target
```

Check: pure black background, the mark centred and gently pulsing, the small wordmark at the bottom, dots appearing
as you type, the message text. If the screen stays black, look at `/tmp/plymouth.log` for a script error.
To get back to the previous theme: `sudo plymouth-set-default-theme <old theme>`.

## GRUB

GRUB cannot read `/usr`, so a theme has to be copied to the boot partition and referenced from GRUB's
configuration. How that is done depends on how this image installs its bootloader, which is set up by
`bootc install` (the image repository does not configure GRUB itself). Please check on a running system:

```bash
ls /boot /boot/grub* 2>&1 | head -30
sudo bootupctl status 2>&1 | head
grep -nE 'theme|user\.cfg|blscfg' /boot/grub*/grub.cfg 2>&1 | head
```

- On a conventional GRUB (Arch style) it is: copy `/usr/share/grub/themes/boppos` to `/boot/grub/themes/boppos`,
  set `GRUB_THEME="/boot/grub/themes/boppos/theme.txt"` in `/etc/default/grub`, run
  `grub-mkconfig -o /boot/grub/grub.cfg`.
- On a bootupd-managed (Fedora style) GRUB the generated `grub.cfg` is fixed and a user override file is read
  instead; the same copy applies, and the theme is switched on with `set theme=...` in that override.

Until that is known, nothing here touches the bootloader.
