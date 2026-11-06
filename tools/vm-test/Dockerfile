# Test environment for the boppos-unl0kr dracut module: a kernel, dracut, Plymouth, swtpm and QEMU, plus unl0kr.
# Built and run by tools/vm-test-unl0kr; nothing here touches the host's boot.
FROM docker.io/archlinux/archlinux:latest

RUN pacman -Syu --noconfirm --needed \
        linux dracut systemd cryptsetup tpm2-tss tpm2-tools plymouth \
        libinput libxkbcommon xkeyboard-config libdrm libinih \
        qemu-base qemu-hw-display-virtio-gpu qemu-hw-display-virtio-gpu-pci qemu-hw-display-virtio-vga \
        swtpm python make git meson ninja gcc pkgconf \
    && pacman -Scc --noconfirm

# unl0kr: a package file from bopp-os/pkgbuilds when one is given (tools/vm-test-unl0kr --unl0kr-pkg FILE copies
# it to pkg/), otherwise built from the buffybox release tag with the DRM and fbdev backends.
ARG BUFFYBOX_TAG=3.6.0
COPY pkg/ /tmp/pkg/
RUN set -e; \
    if ls /tmp/pkg/*.pkg.tar.* > /dev/null 2>&1; then \
        pacman -U --noconfirm /tmp/pkg/*.pkg.tar.*; \
    else \
        git clone --depth 1 --recurse-submodules --shallow-submodules --branch "$BUFFYBOX_TAG" \
            https://gitlab.postmarketos.org/postmarketOS/buffybox.git /tmp/buffybox; \
        cd /tmp/buffybox; \
        meson setup build --prefix=/usr --sysconfdir=/etc -Dman=false -Dlvgl_backends=framebuffer,drm; \
        meson compile -C build; \
        meson install -C build --tags unl0kr; \
    fi; \
    rm -rf /tmp/buffybox /tmp/pkg; \
    test -x /usr/bin/unl0kr
