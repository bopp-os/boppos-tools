PREFIX ?= /usr
SYSCONFDIR ?= /etc

all:
	@echo "Nothing to build. Run 'make install' to install."

install-tools:
	install -d $(DESTDIR)$(PREFIX)/bin
	install -m 755 bin/* $(DESTDIR)$(PREFIX)/bin/
	
	install -d $(DESTDIR)$(PREFIX)/lib/boppos
	install -m 755 lib/boppos/* $(DESTDIR)$(PREFIX)/lib/boppos/
	
	install -d $(DESTDIR)$(PREFIX)/share/boppos
	install -m 644 share/boppos/* $(DESTDIR)$(PREFIX)/share/boppos/
	chmod 755 $(DESTDIR)$(PREFIX)/share/boppos/*.sh || true
	
	install -d $(DESTDIR)$(PREFIX)/lib/systemd/system
	install -m 644 systemd/system/* $(DESTDIR)$(PREFIX)/lib/systemd/system/
	
	install -d $(DESTDIR)$(PREFIX)/lib/systemd/user
	install -m 644 systemd/user/* $(DESTDIR)$(PREFIX)/lib/systemd/user/
	
	install -d $(DESTDIR)$(PREFIX)/share/polkit-1/rules.d
	install -m 644 polkit/* $(DESTDIR)$(PREFIX)/share/polkit-1/rules.d/
	
	install -d $(DESTDIR)$(SYSCONFDIR)/xdg/autostart
	install -m 644 autostart/* $(DESTDIR)$(SYSCONFDIR)/xdg/autostart/

install-artwork:
	install -d $(DESTDIR)$(PREFIX)/share/icons
	cp -r artwork/icons/* $(DESTDIR)$(PREFIX)/share/icons/
	find $(DESTDIR)$(PREFIX)/share/icons -type d -exec chmod 755 {} \;
	find $(DESTDIR)$(PREFIX)/share/icons -type f -exec chmod 644 {} \;

install: install-tools install-artwork
