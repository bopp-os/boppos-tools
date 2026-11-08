#!/bin/bash
# boppos-unl0kr: unlock LUKS with unl0kr's on-screen keyboard (touchscreen, trackpad, mouse or a physical
# keyboard) when TPM2 auto-unlock fails. Only included when listed in add_dracutmodules; see
# docs/unl0kr-unlock.md in boppos-tools.
# dracut sources this file and sets initdir, moddir, systemdsystemunitdir, SYSTEMCTL and friends.
# shellcheck disable=SC2154

# called by dracut
check() {
    # A missing unl0kr makes dracut stop with "module 'boppos-unl0kr' cannot be installed" when the module was
    # asked for, instead of building an initramfs without a way to type the passphrase on a handheld.
    require_binaries unl0kr || return 1
    [[ -x ${dracutsysrootdir-}/usr/lib/systemd/systemd-reply-password ]] || return 1

    # 255: never picked up on its own, only when listed in add_dracutmodules (0 would put it in every initramfs).
    return 255
}

# called by dracut
depends() {
    # drm: unl0kr draws with the DRM backend (see install()); hwdb + udev rules: the ID_INPUT_* properties
    # unl0kr uses to tell touchscreens, touchpads, mice and keyboards apart.
    echo bash systemd systemd-udevd hwdb crypt systemd-cryptsetup drm
    return 0
}

# called by dracut
installkernel() {
    # USB HID: the Steam Deck controller in lizard mode (trackpad = mouse, plus its keyboard interface),
    # the Legion Go controllers and touchpad, any USB keyboard or mouse.
    # I2C HID: the Steam Deck (LCD and OLED) and Legion Go touchscreens are i2c-hid-acpi devices driven by
    # hid-multitouch, behind the AMD DesignWare I2C controller with GPIO interrupts from pinctrl-amd.
    # Every name is optional: built-in drivers and ones a kernel does not have are skipped.
    hostonly='' instmods \
        evdev \
        usbhid hid-generic hid-multitouch \
        i2c-hid i2c-hid-acpi \
        i2c-designware-core i2c-designware-platform pinctrl-amd \
        hid-lenovo hid-lenovo-go
}

# called by dracut
install() {
    inst_binary unl0kr
    inst_multiple -o /usr/share/unl0kr/unl0kr.conf /usr/share/unl0kr/unl0kr.conf.d/*.conf \
        /etc/unl0kr.conf /etc/unl0kr.conf.d/*.conf
    inst_multiple /usr/lib/systemd/systemd-reply-password sleep rm mkdir systemctl

    # Hardware keyboards go through xkbcommon, which compiles the keymap from the XKB data at runtime.
    # unl0kr cannot load a keymap without it, so ship the whole tree (a few MB, it compresses well).
    if [[ -d ${dracutsysrootdir-}/usr/share/X11/xkb ]]; then
        inst_dir /usr/share/X11/xkb
        local _f
        while IFS= read -r -d '' _f; do
            inst_simple "${_f#"${dracutsysrootdir-}"}"
        done < <(find "${dracutsysrootdir-}/usr/share/X11/xkb" -type f -print0)
    else
        dwarn "boppos-unl0kr: /usr/share/X11/xkb is missing, physical keyboards will not work in unl0kr"
    fi

    # libinput: device quirks (touchpad sizes, palm detection) and its udev helpers.
    inst_multiple -o /usr/share/libinput/*.quirks \
        "${udevdir:-/usr/lib/udev}"/libinput-device-group \
        "${udevdir:-/usr/lib/udev}"/libinput-fuzz-extract \
        "${udevdir:-/usr/lib/udev}"/libinput-fuzz-to-zero
    inst_rules 80-libinput-device-groups.rules 90-libinput-fuzz-override.rules

    # Our defaults, layered over /etc/unl0kr.conf with -C. The DRM backend because Plymouth hands the display
    # over by dropping its DRM master (plymouth deactivate), which the fbdev emulation does not notice; a dark
    # theme to match the black splash; the on-screen keyboard always shown, since unl0kr hides it whenever any
    # keyboard device exists, and handheld controllers (the Deck in lizard mode) present one. A backend or
    # autohide set in /etc/unl0kr.conf wins, and an unl0kr built without the DRM backend (not linked to
    # libdrm) keeps its fbdev default.
    local _unl0kr
    _unl0kr=$(find_binary unl0kr)
    {
        echo "[general]"
        if grep -qs '^[[:space:]]*backend[[:space:]]*=' "${dracutsysrootdir-}/etc/unl0kr.conf"; then
            :
        elif LC_ALL=C grep -qa 'libdrm\.so' "${dracutsysrootdir-}${_unl0kr}"; then
            echo "backend=drm"
        else
            dwarn "boppos-unl0kr: unl0kr was built without the DRM backend, using fbdev (Plymouth may draw over it)"
        fi
        echo "[keyboard]"
        if ! grep -qs '^[[:space:]]*autohide[[:space:]]*=' "${dracutsysrootdir-}/etc/unl0kr.conf"; then
            echo "autohide=false"
        fi
        echo "[theme]"
        echo "default=breezy-dark"
    } > "${initdir}/etc/boppos-unl0kr.conf"

    inst_script "$moddir/boppos-unl0kr-agent" /usr/lib/boppos-unl0kr/boppos-unl0kr-agent
    inst_simple "$moddir/boppos-unl0kr-agent.path" "$systemdsystemunitdir/boppos-unl0kr-agent.path"
    inst_simple "$moddir/boppos-unl0kr-agent.service" "$systemdsystemunitdir/boppos-unl0kr-agent.service"
    $SYSTEMCTL -q --root "$initdir" enable boppos-unl0kr-agent.path
    # Plymouth's agent would show its own password screen, which stays up after unl0kr answers. Only the .path is
    # masked: the agent starts the .service itself when unl0kr fails.
    $SYSTEMCTL -q --root "$initdir" mask systemd-ask-password-plymouth.path
}
