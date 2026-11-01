"""Tests for ingest/uncompress.py: putting compressed-only files back at their
own path, the way to roll back to an image from before compression."""

from __future__ import annotations

import gzip
import logging
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from toon.config import Settings
from toon.constants import ENCODED_SEGMENT
from toon.ingest import precompress, uncompress
from toon.ingest.store import ContentStore, version_ref


def text(size: int, seed: str = "lorem ipsum dolor ") -> bytes:
    raw = seed.encode()
    return (raw * (size // len(raw) + 1))[:size]


def publish(store: ContentStore, files: dict[str, bytes], site_id: uuid.UUID | None = None) -> Path:
    """Writes a version the real way, compressed; returns the version directory."""
    site_id = site_id or uuid.uuid4()
    version_id = uuid.uuid4()
    with store.write_version(site_id, version_id) as writer:
        for rel_path, data in files.items():
            with writer.open_file(rel_path) as handle:
                handle.write(data)
        precompress.encode_version(writer)
    return store.root / version_ref(site_id, version_id)


def stored(version_dir: Path) -> set[str]:
    return {path.relative_to(version_dir).as_posix() for path in version_dir.rglob("*") if path.is_file()}


def gzip_copy(version_dir: Path, rel_path: str) -> Path:
    return version_dir / ENCODED_SEGMENT / precompress.GZIP / rel_path


def brotli_copy(version_dir: Path, rel_path: str) -> Path:
    return version_dir / ENCODED_SEGMENT / precompress.BROTLI / rel_path


FILES = {
    "index.html": text(5000, "<p>hello</p>"),
    "assets/css/app.css": text(8000, "a { color: red }\n"),
    "sub dir/één bestand ☃.txt": text(3000, "tëkst ☃ "),
    "deep/a/b/c/data.json": text(4000, '{"k": 1}'),
    "tiny.txt": b"small",
    "logo.png": b"\x89PNG" + os.urandom(3000),
}
COMPRESSED = ["index.html", "assets/css/app.css", "sub dir/één bestand ☃.txt", "deep/a/b/c/data.json"]


@pytest.fixture
def store(tmp_path: Path) -> ContentStore:
    return ContentStore(tmp_path)


def corrupt_middle(copy: Path) -> None:
    data = bytearray(copy.read_bytes())
    middle = len(data) // 2
    for offset in range(middle, middle + 16):
        data[offset] ^= 0xFF
    copy.write_bytes(bytes(data))


def make_settings(content_root: Path, **overrides) -> Settings:
    return Settings(
        db_url="postgresql+asyncpg://ongebruikt:ongebruikt@localhost:5432/ongebruikt",
        content_root=content_root,
        oidc_issuer="https://idp.example",
        oidc_client_id="plak-client",
        oidc_client_private_jwk="{}",
        oidc_required_acr="urn:acr:hoog",
        session_secret="sessie-geheim-van-minstens-32-bytes!",
        audit_pepper="audit-pepper-van-minstens-32-bytes!!",
        audit_ip_key="a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        content_base_url="https://plak.example",
        environment="dev",
        **overrides,
    )


def fake_free(monkeypatch: pytest.MonkeyPatch, free: int) -> None:
    monkeypatch.setattr(
        uncompress.shutil, "disk_usage", lambda _: SimpleNamespace(total=0, used=0, free=free)
    )


class TestRestoreAll:
    def test_every_file_is_back_byte_identical_and_encoded_is_gone(self, store: ContentStore):
        version_dir = publish(store, FILES)
        assert not (version_dir / "index.html").exists()
        assert (version_dir / ENCODED_SEGMENT).is_dir()

        outcome = uncompress.restore_all(store.root, 0)

        assert outcome == uncompress.Outcome(restored=4, failed=0)
        for rel_path, data in FILES.items():
            assert (version_dir / rel_path).read_bytes() == data
        assert not (version_dir / ENCODED_SEGMENT).exists()
        assert stored(version_dir) == set(FILES)

    def test_the_store_finds_the_restored_files(self, store: ContentStore):
        version_dir = publish(store, FILES)
        ref = version_dir.relative_to(store.root).as_posix()
        assert store.file_path(ref, "index.html") is None

        uncompress.restore_all(store.root, 0)

        for rel_path, data in FILES.items():
            found = store.file_path(ref, rel_path)
            assert found is not None
            assert found.read_bytes() == data
            assert store.encoded_path(ref, rel_path, "gzip") is None

    def test_a_gzip_only_file_without_brotli_is_restored(self, store: ContentStore):
        data = text(6000, "x")
        version_dir = publish(store, {"a.txt": data})
        brotli_copy(version_dir, "a.txt").unlink()

        outcome = uncompress.restore_all(store.root, 0)

        assert outcome.restored == 1
        assert (version_dir / "a.txt").read_bytes() == data
        assert not (version_dir / ENCODED_SEGMENT).exists()

    def test_a_second_run_restores_nothing(self, store: ContentStore):
        version_dir = publish(store, FILES)
        uncompress.restore_all(store.root, 0)

        assert uncompress.restore_all(store.root, 0) == uncompress.Outcome(restored=0, failed=0)
        for rel_path, data in FILES.items():
            assert (version_dir / rel_path).read_bytes() == data

    def test_several_sites_and_versions_are_all_restored(self, store: ContentStore):
        site = uuid.uuid4()
        live = publish(store, FILES, site)
        preview = publish(store, {"index.html": text(3000, "preview "), "x/y.css": text(3000, "b{}")}, site)
        other = publish(store, {"o.js": text(4000, "let a;")})

        outcome = uncompress.restore_all(store.root, 0)

        assert outcome == uncompress.Outcome(restored=4 + 2 + 1, failed=0)
        assert (live / "index.html").read_bytes() == FILES["index.html"]
        assert (preview / "x/y.css").read_bytes() == text(3000, "b{}")
        assert (other / "o.js").read_bytes() == text(4000, "let a;")
        for version_dir in (live, preview, other):
            assert not (version_dir / ENCODED_SEGMENT).exists()

    def test_entries_at_the_root_that_are_not_site_ids_are_skipped(self, store: ContentStore):
        version_dir = publish(store, {"a.txt": text(3000)})
        for name in ("_reclaimed", "lost+found", "not-a-site"):
            decoy = store.root / name / "some" / ENCODED_SEGMENT / precompress.GZIP
            decoy.mkdir(parents=True)
            (decoy / "f.txt").write_bytes(gzip.compress(text(3000)))
        (store.root / "_tmp" / "work").mkdir()
        (store.root / str(uuid.uuid4())).write_bytes(b"a file named like a site id")

        outcome = uncompress.restore_all(store.root, 0)

        assert outcome.restored == 1
        assert (version_dir / "a.txt").exists()
        for name in ("_reclaimed", "lost+found", "not-a-site"):
            assert (store.root / name / "some" / ENCODED_SEGMENT / precompress.GZIP / "f.txt").exists()
            assert not (store.root / name / "some" / "f.txt").exists()

    def test_a_symlinked_site_directory_is_not_followed(self, store: ContentStore, tmp_path: Path):
        outside = tmp_path / "outside"
        copy_dir = outside / "v" / ENCODED_SEGMENT / precompress.GZIP
        copy_dir.mkdir(parents=True)
        (copy_dir / "f.txt").write_bytes(gzip.compress(text(3000)))
        (store.root / str(uuid.uuid4())).symlink_to(outside, target_is_directory=True)

        assert uncompress.restore_all(store.root, 0) == uncompress.Outcome(restored=0, failed=0)
        assert not (outside / "v" / "f.txt").exists()

    def test_a_version_without_encoded_is_untouched(self, store: ContentStore):
        version_dir = publish(store, {"a.txt": b"tiny", "b.png": b"\x89PNG" + os.urandom(2000)})
        before = {rel: (version_dir / rel).read_bytes() for rel in stored(version_dir)}
        assert not (version_dir / ENCODED_SEGMENT).exists()

        assert uncompress.restore_all(store.root, 0) == uncompress.Outcome(restored=0, failed=0)
        assert {rel: (version_dir / rel).read_bytes() for rel in stored(version_dir)} == before

    def test_a_plain_file_already_present_keeps_its_copies(self, store: ContentStore):
        version_dir = publish(store, {"a.css": text(3000, "a{}"), "b.css": text(3000, "b{}")})
        (version_dir / "a.css").write_bytes(b"the plain file wins")

        outcome = uncompress.restore_all(store.root, 0)

        assert outcome.restored == 1
        assert (version_dir / "a.css").read_bytes() == b"the plain file wins"
        assert gzip_copy(version_dir, "a.css").exists()
        assert brotli_copy(version_dir, "a.css").exists()
        assert (version_dir / "b.css").read_bytes() == text(3000, "b{}")
        assert not gzip_copy(version_dir, "b.css").exists()
        assert not brotli_copy(version_dir, "b.css").exists()


class TestRoom:
    def test_without_room_nothing_is_restored(self, store: ContentStore, monkeypatch: pytest.MonkeyPatch):
        version_dir = publish(store, FILES)
        needed = sum(len(FILES[rel]) for rel in COMPRESSED)
        fake_free(monkeypatch, needed + 1000)

        outcome = uncompress.restore_all(store.root, 2000)

        assert outcome == uncompress.Outcome(restored=0, failed=0, needed=needed, available=needed - 1000)
        for rel_path in COMPRESSED:
            assert not (version_dir / rel_path).exists()
            assert gzip_copy(version_dir, rel_path).exists()

    def test_exactly_enough_room_is_enough(self, store: ContentStore, monkeypatch: pytest.MonkeyPatch):
        version_dir = publish(store, {"a.txt": text(3000)})
        fake_free(monkeypatch, 3000)

        assert uncompress.restore_all(store.root, 0).restored == 1
        assert (version_dir / "a.txt").exists()

    def test_a_huge_reserve_refuses(self, store: ContentStore):
        publish(store, {"a.txt": text(3000)})

        outcome = uncompress.restore_all(store.root, 2**62)

        assert outcome.restored == 0
        assert outcome.needed == 3000
        assert outcome.available is not None
        assert outcome.available < 0

    def test_with_nothing_pending_a_full_volume_is_no_refusal(
        self, store: ContentStore, monkeypatch: pytest.MonkeyPatch
    ):
        fake_free(monkeypatch, 0)

        assert uncompress.restore_all(store.root, 10**9) == uncompress.Outcome(restored=0, failed=0)


class TestDamagedCopies:
    @staticmethod
    def assert_failed_cleanly(version_dir: Path, rel_path: str, caplog: pytest.LogCaptureFixture) -> None:
        assert not (version_dir / rel_path).exists()
        assert gzip_copy(version_dir, rel_path).exists()
        assert brotli_copy(version_dir, rel_path).exists()
        assert not list((version_dir / ENCODED_SEGMENT).glob(".unpacking-*"))
        errors = [record for record in caplog.records if record.levelno == logging.ERROR]
        assert len(errors) == 1
        assert str(version_dir / rel_path) in errors[0].getMessage()

    def test_a_truncated_copy_fails_and_the_others_are_restored(
        self, store: ContentStore, caplog: pytest.LogCaptureFixture
    ):
        files = {"a.css": text(30000, "a{}\n"), "b.css": text(30000, "b{}\n"), "c.css": text(30000, "c{}\n")}
        version_dir = publish(store, files)
        copy = gzip_copy(version_dir, "b.css")
        copy.write_bytes(copy.read_bytes()[:-30])

        with caplog.at_level(logging.ERROR, logger=uncompress.__name__):
            outcome = uncompress.restore_all(store.root, 0)

        assert outcome == uncompress.Outcome(restored=2, failed=1)
        self.assert_failed_cleanly(version_dir, "b.css", caplog)
        assert (version_dir / "a.css").read_bytes() == files["a.css"]
        assert (version_dir / "c.css").read_bytes() == files["c.css"]

    def test_a_corrupt_copy_fails_and_the_others_are_restored(
        self, store: ContentStore, caplog: pytest.LogCaptureFixture
    ):
        files = {"a.css": text(30000, "a{}\n"), "b.css": os.urandom(2000).hex().encode()}
        version_dir = publish(store, files)
        corrupt_middle(gzip_copy(version_dir, "b.css"))

        with caplog.at_level(logging.ERROR, logger=uncompress.__name__):
            outcome = uncompress.restore_all(store.root, 0)

        assert outcome == uncompress.Outcome(restored=1, failed=1)
        self.assert_failed_cleanly(version_dir, "b.css", caplog)
        assert (version_dir / "a.css").read_bytes() == files["a.css"]

    def test_a_failed_copy_is_retried_by_the_next_run(self, store: ContentStore):
        version_dir = publish(store, {"b.css": text(30000, "b{}\n")})
        copy = gzip_copy(version_dir, "b.css")
        good = copy.read_bytes()
        copy.write_bytes(good[:-30])
        assert uncompress.restore_all(store.root, 0).failed == 1

        copy.write_bytes(good)

        assert uncompress.restore_all(store.root, 0) == uncompress.Outcome(restored=1, failed=0)
        assert (version_dir / "b.css").read_bytes() == text(30000, "b{}\n")


class TestUnpackedSize:
    def test_reads_the_isize_trailer(self, tmp_path: Path):
        copy = tmp_path / "a.gz"
        copy.write_bytes(gzip.compress(b"x" * 1234))
        assert uncompress._unpacked_size(copy) == 1234

    @pytest.mark.parametrize("size", [0, 1, 3])
    def test_a_copy_shorter_than_four_bytes_is_zero(self, tmp_path: Path, size: int):
        copy = tmp_path / "a.gz"
        copy.write_bytes(b"\x1f\x8b\x08"[:size])
        assert uncompress._unpacked_size(copy) == 0

    def test_a_tiny_copy_then_fails_cleanly(self, store: ContentStore, caplog: pytest.LogCaptureFixture):
        version_dir = publish(store, {"a.css": text(3000), "b.css": text(3000, "b")})
        copy = gzip_copy(version_dir, "a.css")
        copy.write_bytes(b"\x1f\x8b")

        with caplog.at_level(logging.ERROR, logger=uncompress.__name__):
            outcome = uncompress.restore_all(store.root, 0)

        assert outcome == uncompress.Outcome(restored=1, failed=1)
        assert not (version_dir / "a.css").exists()
        assert copy.exists()
        assert not list((version_dir / ENCODED_SEGMENT).glob(".unpacking-*"))
        assert (version_dir / "b.css").read_bytes() == text(3000, "b")


class TestRun:
    def test_with_the_setting_on_nothing_happens(self, store: ContentStore, caplog: pytest.LogCaptureFixture):
        version_dir = publish(store, FILES)
        before = stored(version_dir)

        with caplog.at_level(logging.INFO, logger=uncompress.__name__):
            assert uncompress.run(make_settings(store.root, store_compressed=True)) is None

        assert stored(version_dir) == before
        assert not (version_dir / "index.html").exists()
        assert caplog.records == []

    def test_with_the_setting_off_it_restores_and_logs_info(
        self, store: ContentStore, caplog: pytest.LogCaptureFixture
    ):
        version_dir = publish(store, FILES)
        settings = make_settings(store.root, store_compressed=False, storage_min_free_bytes=0)

        with caplog.at_level(logging.INFO, logger=uncompress.__name__):
            outcome = uncompress.run(settings)

        assert outcome == uncompress.Outcome(restored=4, failed=0)
        assert (version_dir / "index.html").read_bytes() == FILES["index.html"]
        assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
            (logging.INFO, "Put 4 files back at their own path; no version keeps a file compressed only.")
        ]

    def test_with_nothing_to_do_it_still_says_so(self, store: ContentStore, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.INFO, logger=uncompress.__name__):
            outcome = uncompress.run(make_settings(store.root, store_compressed=False, storage_min_free_bytes=0))

        assert outcome == uncompress.Outcome(restored=0, failed=0)
        assert [r.levelno for r in caplog.records] == [logging.INFO]

    def test_a_refusal_is_an_error_and_leaves_everything(
        self, store: ContentStore, caplog: pytest.LogCaptureFixture
    ):
        version_dir = publish(store, {"a.txt": text(3000)})
        settings = make_settings(store.root, store_compressed=False, storage_min_free_bytes=2**62)

        with caplog.at_level(logging.INFO, logger=uncompress.__name__):
            outcome = uncompress.run(settings)

        assert outcome is not None
        assert outcome.needed == 3000
        assert not (version_dir / "a.txt").exists()
        assert [r.levelno for r in caplog.records] == [logging.ERROR]
        message = caplog.records[0].getMessage()
        assert message.startswith("Not putting files back")
        assert "3000 bytes" in message

    def test_failures_are_an_error(self, store: ContentStore, caplog: pytest.LogCaptureFixture):
        version_dir = publish(store, {"a.css": text(30000, "a{}\n"), "b.css": text(30000, "b{}\n")})
        copy = gzip_copy(version_dir, "b.css")
        copy.write_bytes(copy.read_bytes()[:-30])
        settings = make_settings(store.root, store_compressed=False, storage_min_free_bytes=0)

        with caplog.at_level(logging.INFO, logger=uncompress.__name__):
            outcome = uncompress.run(settings)

        assert outcome == uncompress.Outcome(restored=1, failed=1)
        summary = [r for r in caplog.records if r.getMessage().startswith("Put ")]
        assert [r.levelno for r in summary] == [logging.ERROR]
        assert "Put 1 files back, 1 could not be" in summary[0].getMessage()
        assert not [r for r in caplog.records if r.levelno == logging.INFO]


class TestMain:
    def test_loads_the_settings_and_returns_zero(self, store: ContentStore, monkeypatch: pytest.MonkeyPatch):
        version_dir = publish(store, {"a.txt": text(3000)})
        settings = make_settings(store.root, store_compressed=False, storage_min_free_bytes=0)
        monkeypatch.setattr(uncompress, "load_settings", lambda: settings)

        assert uncompress.main() == 0
        assert (version_dir / "a.txt").read_bytes() == text(3000)

    def test_returns_zero_even_when_it_refuses(self, store: ContentStore, monkeypatch: pytest.MonkeyPatch):
        version_dir = publish(store, {"a.txt": text(3000)})
        settings = make_settings(store.root, store_compressed=False, storage_min_free_bytes=2**62)
        monkeypatch.setattr(uncompress, "load_settings", lambda: settings)

        assert uncompress.main() == 0
        assert not (version_dir / "a.txt").exists()
