# Maintainer: BoppOS Team
pkgbase=boppos-tools
pkgname=('boppos-tools' 'boppos-artwork')
pkgver=1.0.0
pkgrel=1
arch=('any')
url="https://github.com/bopp-os/boppos-tools"
license=('GPL3')
makedepends=('git' 'make')
source=("git+https://github.com/bopp-os/boppos-tools.git")
sha256sums=('SKIP')

build() {
  # Nothing to build, but required by makepkg
  true
}

package_boppos-tools() {
  pkgdesc="Custom tools and utilities for BoppOS"
  depends=('bash' 'python' 'boppos-artwork')

  cd "${srcdir}/${pkgbase}"
  make DESTDIR="${pkgdir}" PREFIX=/usr SYSCONFDIR=/etc install-tools
}

package_boppos-artwork() {
  pkgdesc="Artwork and icons for BoppOS"
  depends=('hicolor-icon-theme')

  cd "${srcdir}/${pkgbase}"
  make DESTDIR="${pkgdir}" PREFIX=/usr SYSCONFDIR=/etc install-artwork
}
