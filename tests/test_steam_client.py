"""The Steam client download of the first-boot tour, against a fake CDN on localhost and a temporary home."""

import hashlib
import http.server
import io
import lzma
import os
import struct
import tarfile
import threading
import zipfile
import zlib

import pytest
from boppos import steam_client as sc


def vz(data: bytes) -> bytes:
    """Valve's VZ wrapper, as the real CDN serves it."""
    filt = {"id": lzma.FILTER_LZMA1, "dict_size": 1 << 16, "lc": 3, "lp": 0, "pb": 2}
    props = lzma._encode_filter_properties(filt)
    raw = lzma.compress(data, format=lzma.FORMAT_RAW, filters=[filt])
    return b"VZa" + b"\0\0\0\0" + props + raw + struct.pack("<II", zlib.crc32(data), len(data)) + b"zv"


def zipped(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


PACKAGES = {
    "bins_ubuntu12": {"ubuntu12_32/": b"", "ubuntu12_32/steamui.so": b"UI" * 1000, "ubuntu12_32/steam": b"ELF",
                      "ubuntu12_64/steamui.so": b"UI64"},
    "public_all": {"public/steambootstrapper_english.txt": b"hello" * 5000},
    "resources_all": {"graphics/x.tga": os.urandom(4000)},
}


class CDN:
    """A fake client-update host: the manifest and the packages it names, with a switch to break one."""

    def __init__(self, plain=("resources_all",)):
        self.files: dict[str, bytes] = {}
        self.requests: list[str] = []
        self.broken: dict[str, str] = {}  # name -> "404" | "short" | "corrupt"
        lines = ['"ubuntu12"', "{", '\t"version"\t\t"1792618285"']
        for key, files in PACKAGES.items():
            z = zipped(files)
            if key in plain:
                name = f"{key}.zip.{hashlib.sha1(z).hexdigest()}"
                self.files[name] = z
                lines += [f'\t"{key}"', "\t{", f'\t\t"file"\t\t"{name}"', f'\t\t"size"\t\t"{len(z)}"',
                          f'\t\t"sha2"\t\t"{hashlib.sha256(z).hexdigest()}"', "\t}"]
            else:
                data = vz(z)
                name = f"{key}.zip.vz.{hashlib.sha1(data).hexdigest()}_{len(data)}"
                self.files[name] = data
                lines += [f'\t"{key}"', "\t{", f'\t\t"file"\t\t"{key}.zip.0000"', '\t\t"size"\t\t"1"',
                          f'\t\t"sha2"\t\t"{"0" * 64}"', f'\t\t"zipvz"\t\t"{name}"',
                          f'\t\t"sha2vz"\t\t"{hashlib.sha256(data).hexdigest()}"', "\t}"]
        lines += ["}"]
        self.files[sc.MANIFEST_NAME] = ("\n".join(lines) + "\n").encode()
        cdn = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_GET(self):
                name = self.path.lstrip("/")
                cdn.requests.append(name)
                key = next((k for k in cdn.broken if name.startswith(k)), None)
                data = cdn.files.get(name)
                if data is None or (key and cdn.broken[key] == "404"):
                    self.send_error(404)
                    return
                if key and cdn.broken[key] == "corrupt":
                    data = bytes([data[0] ^ 1]) + data[1:]
                if key and cdn.broken[key] == "short":
                    data = data[: len(data) // 2]
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()

    def package(self, key):
        return next(n for n in self.files if n.startswith(key + "."))

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def cdn():
    c = CDN()
    yield c
    c.close()


@pytest.fixture
def bootstrap(tmp_path):
    path = tmp_path / "bootstraplinux_ubuntu12_32.tar.xz"
    with tarfile.open(path, "w:xz") as tar:
        for name, data, mode in (("steam.sh", b"#!/bin/sh\n", 0o755), ("ubuntu12_32/steam", b"ELF", 0o755)):
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            tar.addfile(info, io.BytesIO(data))
    return path


def run(home, cdn, bootstrap, events=None, cancel=None):
    return sc.install(home, (events if events is not None else []).append, cancel, cdn.url, bootstrap, timeout=5)


def test_parse_manifest_prefers_vz_and_reads_plain_zips(cdn):
    m = sc.parse_manifest(cdn.files[sc.MANIFEST_NAME])
    assert m.version == "1792618285"
    by = {p.name.split(".")[0]: p for p in m.packages}
    assert by["bins_ubuntu12"].vz and by["bins_ubuntu12"].name == cdn.package("bins_ubuntu12")
    assert not by["resources_all"].vz
    assert m.total == sum(len(cdn.files[p.name]) for p in m.packages)


@pytest.mark.parametrize("bad", [
    b'"ubuntu12" { "version" "1" "x" { "file" "../evil" "size" "1" "sha2" "' + b"a" * 64 + b'" } }',
    b'"ubuntu12" { "version" "1" }',
    b'"other" { }',
    b'"ubuntu12" { "x" { "file" "a.zip" "size" "1" "sha2" "' + b"a" * 64 + b'" } }',
    b'"ubuntu12" { "version" "1" ',
])
def test_parse_manifest_refuses_what_it_cannot_trust(bad):
    with pytest.raises(sc.SteamError):
        sc.parse_manifest(bad)


def test_vz_roundtrip_and_checksum(tmp_path):
    src, dst = tmp_path / "p.vz", tmp_path / "p.zip"
    src.write_bytes(vz(b"payload" * 100))
    sc.unvz(src, dst)
    assert dst.read_bytes() == b"payload" * 100
    data = bytearray(src.read_bytes())
    data[-7] ^= 1  # the CRC
    src.write_bytes(bytes(data))
    with pytest.raises(sc.ChecksumError):
        sc.unvz(src, dst)


def test_install_downloads_unpacks_and_reports_progress(tmp_path, cdn, bootstrap):
    home = tmp_path / "home"
    home.mkdir()
    events = []
    assert run(home, cdn, bootstrap, events) == "1792618285"
    steam = home / ".local/share/Steam"
    assert sc.installed(home)
    assert (steam / "ubuntu12_32/steamui.so").read_bytes() == b"UI" * 1000
    assert (steam / "ubuntu12_64/steamui.so").read_bytes() == b"UI64"
    assert (steam / "steam.sh").exists() and (steam / "public/steambootstrapper_english.txt").exists()
    assert (steam / "package" / f"{sc.MANIFEST_NAME}.manifest").read_bytes() == cdn.files[sc.MANIFEST_NAME]
    for key in PACKAGES:
        assert (steam / "package" / cdn.package(key)).is_file()
    assert not (steam / "package/.boppos-unpack").exists()
    assert not list(steam.rglob("*.part")) and not list(steam.rglob("*.boppos-tmp"))
    assert oct(steam.stat().st_mode & 0o777) == "0o700"
    assert all(p.stat().st_uid == os.getuid() for p in steam.rglob("*"))  # the user's, not root's
    assert (steam / "ubuntu12_32/steam").stat().st_mode & 0o111  # an executable stays executable
    phases = [e["phase"] for e in events]
    assert phases[0] == "manifest" and phases[-1] == "done"
    assert {"check", "download", "unpack"} <= set(phases)
    downloads = [e for e in events if e["phase"] == "download"]
    assert downloads[-1]["done"] == downloads[-1]["total"] > 0
    assert [e["done"] for e in downloads] == sorted(e["done"] for e in downloads)


def test_nothing_to_do_when_steam_is_there(tmp_path, cdn, bootstrap):
    ui = tmp_path / ".local/share/Steam/ubuntu12_64/steamui.so"
    ui.parent.mkdir(parents=True)
    ui.write_bytes(b"")
    events = []
    assert run(tmp_path, cdn, bootstrap, events) == ""
    assert cdn.requests == [] and events == [{"phase": "done", "already": True}]


def test_offline_leaves_the_home_untouched(tmp_path, cdn, bootstrap):
    cdn.broken[sc.MANIFEST_NAME] = "404"
    with pytest.raises(sc.SteamError):
        run(tmp_path, cdn, bootstrap)
    assert not (tmp_path / ".local/share/Steam").exists()


@pytest.mark.parametrize("how", ["404", "short", "corrupt"])
def test_a_failed_download_leaves_no_steamui(tmp_path, cdn, bootstrap, how):
    cdn.broken["resources_all"] = how  # downloaded after bins_ubuntu12, so that one is stored already
    with pytest.raises(sc.SteamError):
        run(tmp_path, cdn, bootstrap)
    steam = tmp_path / ".local/share/Steam"
    assert not sc.installed(tmp_path)
    assert not list(steam.rglob("steamui.so"))
    assert not list(steam.rglob("*.part"))
    assert (steam / "package" / cdn.package("bins_ubuntu12")).is_file()  # kept for the next try (or for Steam)


def test_a_failed_unpack_leaves_no_steamui(tmp_path, cdn, bootstrap, monkeypatch):
    real = sc.unpack_package

    def unpack(pkg_file, p, *a, **kw):
        if p.name.startswith("resources_all"):
            raise sc.SteamError("disk full")
        return real(pkg_file, p, *a, **kw)

    monkeypatch.setattr(sc, "unpack_package", unpack)
    with pytest.raises(sc.SteamError):
        run(tmp_path, cdn, bootstrap)
    assert not list((tmp_path / ".local/share/Steam").rglob("steamui.so"))


def test_a_second_try_downloads_only_what_is_missing(tmp_path, cdn, bootstrap):
    cdn.broken["resources_all"] = "404"
    with pytest.raises(sc.SteamError):
        run(tmp_path, cdn, bootstrap)
    del cdn.broken["resources_all"]
    cdn.requests.clear()
    run(tmp_path, cdn, bootstrap)
    assert cdn.requests == [sc.MANIFEST_NAME, cdn.package("resources_all")]
    assert sc.installed(tmp_path)


def test_the_installers_preload_is_reused(tmp_path, cdn, bootstrap):
    """fisherman stores every package but unpacks nothing: only the unpacking is left to do."""
    pkg = tmp_path / ".local/share/Steam/package"
    pkg.mkdir(parents=True)
    for key in PACKAGES:
        (pkg / cdn.package(key)).write_bytes(cdn.files[cdn.package(key)])
    run(tmp_path, cdn, bootstrap)
    assert cdn.requests == [sc.MANIFEST_NAME]
    assert sc.installed(tmp_path)


def test_cancel_stops_without_steamui(tmp_path, cdn, bootstrap):
    cancel = threading.Event()

    def emit(ev):
        if ev["phase"] == "download":
            cancel.set()

    with pytest.raises(sc.Cancelled):
        sc.install(tmp_path, emit, cancel, cdn.url, bootstrap, timeout=5)
    assert not sc.installed(tmp_path)


def test_unsafe_zip_paths_are_refused(tmp_path):
    z = tmp_path / "evil.zip"
    z.write_bytes(zipped({"../../outside": b"x"}))
    p = sc.Package("evil.zip", z.stat().st_size, "0" * 64, False)
    dest = tmp_path / "Steam"
    dest.mkdir()
    with pytest.raises(sc.SteamError):
        sc.unpack_package(z, p, dest, tmp_path)
    assert not (tmp_path.parent / "outside").exists()


def test_no_bootstrap_is_an_error_before_any_download(tmp_path, cdn):
    with pytest.raises(sc.SteamError, match="bootstrap"):
        sc.install(tmp_path, cdn=cdn.url, bootstrap=tmp_path / "missing.tar.xz")
    assert cdn.requests == []


def test_backslash_paths_from_windows_made_zips_unpack_into_folders(tmp_path):
    """tenfoot_images_all on the real CDN has entries like 'tenfoot\\resource\\images/'."""
    z = tmp_path / "win.zip"
    z.write_bytes(zipped({"tenfoot\\resource\\images/": b"", "tenfoot\\resource\\images\\a.tga": b"img",
                          "bin\\steamui.so": b"ui"}))
    p = sc.Package("win.zip", z.stat().st_size, "0" * 64, False)
    dest = tmp_path / "Steam"
    dest.mkdir()
    later = sc.unpack_package(z, p, dest, tmp_path)
    assert (dest / "tenfoot/resource/images/a.tga").read_bytes() == b"img"
    assert later == [(z, "bin\\steamui.so")]  # steamui.so is still left for last
    for bad in ("..\\..\\x", "C:\\x", "\\x"):
        assert sc._member_path(dest, bad) is None
