PREFIX ?= /usr
SYSCONFDIR ?= /etc

all:
	@echo "Nothing to build. Run 'make install' to install."

install-tools:
	install -d $(DESTDIR)$(PREFIX)/bin
	find bin -maxdepth 1 -type f -exec install -m 755 {} $(DESTDIR)$(PREFIX)/bin/ \;
	
	install -d $(DESTDIR)$(PREFIX)/lib/boppos
	find lib/boppos -maxdepth 1 -type f -exec install -m 755 {} $(DESTDIR)$(PREFIX)/lib/boppos/ \;
	
	install -d $(DESTDIR)$(PREFIX)/lib/boppos/py/boppos
	install -m 644 lib/boppos/py/boppos/*.py $(DESTDIR)$(PREFIX)/lib/boppos/py/boppos/
	install -d $(DESTDIR)$(PREFIX)/lib/boppos/py/boppos/ui
	install -m 644 lib/boppos/py/boppos/ui/*.py lib/boppos/py/boppos/ui/*.css $(DESTDIR)$(PREFIX)/lib/boppos/py/boppos/ui/
	
	install -d $(DESTDIR)$(PREFIX)/share/applications
	install -m 644 share/applications/* $(DESTDIR)$(PREFIX)/share/applications/
	
	install -d $(DESTDIR)$(PREFIX)/share/boppos
	install -m 644 share/boppos/* $(DESTDIR)$(PREFIX)/share/boppos/
	chmod 755 $(DESTDIR)$(PREFIX)/share/boppos/*.sh || true
	
	install -d $(DESTDIR)$(PREFIX)/lib/systemd/system
	install -m 644 systemd/system/* $(DESTDIR)$(PREFIX)/lib/systemd/system/
	
	install -d $(DESTDIR)$(PREFIX)/lib/systemd/user
	install -m 644 systemd/user/* $(DESTDIR)$(PREFIX)/lib/systemd/user/
	
	install -d $(DESTDIR)$(PREFIX)/lib/systemd/user-preset
	install -m 644 systemd/user-preset/* $(DESTDIR)$(PREFIX)/lib/systemd/user-preset/
	
	install -d $(DESTDIR)$(PREFIX)/lib/bopp-kargs.d
	install -m 644 kargs.d/*.conf $(DESTDIR)$(PREFIX)/lib/bopp-kargs.d/
	
	install -d $(DESTDIR)$(PREFIX)/libexec/boppos
	find libexec/boppos -maxdepth 1 -type f -exec install -m 755 {} $(DESTDIR)$(PREFIX)/libexec/boppos/ \;
	
	install -d $(DESTDIR)$(PREFIX)/share/polkit-1/rules.d
	install -m 644 polkit/rules.d/* $(DESTDIR)$(PREFIX)/share/polkit-1/rules.d/
	
	install -d $(DESTDIR)$(PREFIX)/share/polkit-1/actions
	install -m 644 polkit/actions/* $(DESTDIR)$(PREFIX)/share/polkit-1/actions/
	
	install -d $(DESTDIR)$(SYSCONFDIR)/xdg/autostart
	install -m 644 autostart/* $(DESTDIR)$(SYSCONFDIR)/xdg/autostart/

install-artwork:
	install -d $(DESTDIR)$(PREFIX)/share/icons
	cp -r artwork/icons/* $(DESTDIR)$(PREFIX)/share/icons/
	find $(DESTDIR)$(PREFIX)/share/icons -type d -exec chmod 755 {} \;
	find $(DESTDIR)$(PREFIX)/share/icons -type f -exec chmod 644 {} \;

# Boot artwork: the Plymouth splash and the GRUB theme (activation is up to the image: see docs/boot-theme.md)
install-boot-theme:
	install -d $(DESTDIR)$(PREFIX)/share/plymouth/themes/boppos
	install -m 644 boot/plymouth/boppos/* $(DESTDIR)$(PREFIX)/share/plymouth/themes/boppos/
	install -d $(DESTDIR)$(PREFIX)/share/grub/themes/boppos
	install -m 644 boot/grub/boppos/* $(DESTDIR)$(PREFIX)/share/grub/themes/boppos/

install: install-tools install-artwork install-boot-theme
