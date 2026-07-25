import asyncio
import hashlib
import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from diary.archives import ArchiveError, ArchiveService
from diary.maintenance import MaintenanceGate
from diary.storage import DiaryStorage, atomic_write_json, atomic_write_text


def populate(storage: DiaryStorage, title: str = "original") -> None:
    atomic_write_text(storage.diary_path("2025-07-25"), f"# {title}\n")
    atomic_write_json(storage.metadata_path("2025-07-25"), {"date": "2025-07-25", "title": title, "events": []})
    atomic_write_json(storage.continuity_path, {"previous_summary": title})


class ArchiveTests(unittest.TestCase):
    def test_export_manifest_checksums_and_never_nests_archives(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); populate(storage)
            service = ArchiveService(storage, {"persona_name": "Anon", "owner_ids": ["private"], "livingmemory_db_path": "private"})
            first = asyncio.run(service.export())
            second = asyncio.run(service.export())
            manifest = service.verify(second)
            self.assertEqual(manifest["settings_restore_policy"], "exported_safe_settings_are_not_restored")
            names = {item["path"] for item in manifest["files"]}
            self.assertIn("diaries/2025-07-25.md", names)
            self.assertIn("settings.json", names)
            self.assertNotIn("owner_ids", json.loads(zipfile.ZipFile(second).read("settings.json")))
            self.assertFalse(any(name.startswith("archive_exports/") or name.startswith("pre_restore_snapshots/") for name in names))
            self.assertTrue(first.is_file())

    def test_verify_rejects_corruption_traversal_and_bomb_headers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); storage = DiaryStorage(root); populate(storage)
            service = ArchiveService(storage); good = asyncio.run(service.export())
            corrupted = root / "corrupted.zip"; corrupted.write_bytes(good.read_bytes()[:-12])
            with self.assertRaises(ArchiveError): service.verify(corrupted)
            invalid = root / "invalid.zip"; invalid.write_bytes(b"not a zip archive")
            with self.assertRaises(ArchiveError): service.verify(invalid)
            checksum = root / "checksum.zip"; payload = b"actual"
            with zipfile.ZipFile(checksum, "w") as archive:
                archive.writestr("manifest.json", json.dumps({"format_version": 1, "files": [{"path": "metadata/2025-07-25.json", "size": len(payload), "sha256": "0" * 64}]}))
                archive.writestr("metadata/2025-07-25.json", payload)
            with self.assertRaises(ArchiveError): service.verify(checksum)
            traversal = root / "traversal.zip"
            with zipfile.ZipFile(traversal, "w") as archive:
                archive.writestr("manifest.json", json.dumps({"format_version": 1, "files": []}))
                archive.writestr("../escape.json", "x")
            with self.assertRaises(ArchiveError): service.verify(traversal)
            backslash = root / "backslash.zip"
            with zipfile.ZipFile(backslash, "w") as archive:
                archive.writestr("manifest.json", json.dumps({"format_version": 1, "files": []}))
                archive.writestr("metadata\\escape.json", "x")
            with self.assertRaises(ArchiveError): service.verify(backslash)
            absolute = root / "absolute.zip"
            with zipfile.ZipFile(absolute, "w") as archive:
                archive.writestr("manifest.json", json.dumps({"format_version": 1, "files": []}))
                archive.writestr("/metadata/escape.json", "x")
            with self.assertRaises(ArchiveError): service.verify(absolute)
            bomb = root / "bomb.zip"; payload = b"0" * (2 * 1024 * 1024)
            with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", json.dumps({"format_version": 1, "files": [{"path": "metadata/bomb.json", "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}]}))
                archive.writestr("metadata/bomb.json", payload)
            with self.assertRaises(ArchiveError): service.verify(bomb)
            drive = root / "drive.zip"
            with zipfile.ZipFile(drive, "w") as archive:
                archive.writestr("manifest.json", json.dumps({"format_version": 1, "files": []}))
                archive.writestr("C:/escape.json", "x")
            with self.assertRaises(ArchiveError): service.verify(drive)
            symlink = root / "symlink.zip"; payload = b"target"
            with zipfile.ZipFile(symlink, "w") as archive:
                archive.writestr("manifest.json", json.dumps({"format_version": 1, "files": [{"path": "metadata/link.json", "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}]}))
                link = zipfile.ZipInfo("metadata/link.json"); link.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(link, payload)
            with self.assertRaises(ArchiveError): service.verify(symlink)
            reserved = root / "reserved.zip"
            with zipfile.ZipFile(reserved, "w") as archive:
                archive.writestr("manifest.json", json.dumps({"format_version": 1, "files": []}))
                archive.writestr("corrections/2025-07-25/CON.json", "x")
            with self.assertRaises(ArchiveError): service.verify(reserved)
            bad_shape = root / "bad-shape.zip"
            with zipfile.ZipFile(bad_shape, "w") as archive:
                archive.writestr("manifest.json", json.dumps({"format_version": 1, "files": []}))
                archive.writestr("diaries/2025-07-25.json", "x")
            with self.assertRaises(ArchiveError): service.verify(bad_shape)
            case_duplicate = root / "case-duplicate.zip"
            with zipfile.ZipFile(case_duplicate, "w") as archive:
                archive.writestr("manifest.json", json.dumps({"format_version": 1, "files": []}))
                archive.writestr("corrections/2025-07-25/a.json", "x")
                archive.writestr("corrections/2025-07-25/A.json", "x")
            with self.assertRaises(ArchiveError): service.verify(case_duplicate)

    def test_dry_run_and_failed_restore_keep_current_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); storage = DiaryStorage(root); populate(storage, "archive")
            service = ArchiveService(storage); archive = asyncio.run(service.export())
            populate(storage, "current")
            dry = asyncio.run(service.restore(archive, dry_run=True))
            self.assertTrue(dry["dry_run"])
            self.assertEqual(storage.load_metadata("2025-07-25")["title"], "current")
            original_apply, calls = service._apply_selected, 0

            def fail_once(source):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError("simulated disk full")
                return original_apply(source)

            service._apply_selected = fail_once
            with self.assertRaises(ArchiveError): asyncio.run(service.restore(archive))
            self.assertEqual(storage.load_metadata("2025-07-25")["title"], "current")
            self.assertTrue(list((root / "pre_restore_snapshots").glob("*.zip")))

    def test_restore_uses_the_global_maintenance_gate(self):
        async def check():
            gate = MaintenanceGate()
            entered = asyncio.Event(); release = asyncio.Event(); writer_entered = asyncio.Event()

            async def restore():
                async with gate.restore():
                    entered.set(); await release.wait()

            async def writer():
                async with gate.operation():
                    writer_entered.set()

            task = asyncio.create_task(restore()); await entered.wait()
            writer_task = asyncio.create_task(writer())
            await asyncio.sleep(0)
            self.assertTrue(gate.restoring)
            self.assertFalse(writer_entered.is_set())
            release.set(); await task; await writer_task
            self.assertTrue(writer_entered.is_set())
            self.assertFalse(gate.restoring)

        asyncio.run(check())

    def test_export_waits_for_the_shared_gate_for_a_coherent_snapshot(self):
        async def check():
            with tempfile.TemporaryDirectory() as temp:
                storage = DiaryStorage(Path(temp)); populate(storage)
                gate = MaintenanceGate(); service = ArchiveService(storage, gate=gate)
                entered = asyncio.Event(); release = asyncio.Event()

                async def writer():
                    async with gate.operation():
                        entered.set(); await release.wait()

                writer_task = asyncio.create_task(writer()); await entered.wait()
                export_task = asyncio.create_task(service.export())
                await asyncio.sleep(0)
                self.assertFalse(export_task.done())
                release.set(); archive = await export_task; await writer_task
                self.assertTrue(archive.is_file())

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
