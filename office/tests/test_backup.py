from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import zipfile

from django.test import SimpleTestCase

from office.backup import _safe_member, create_backup, restore_backup


class BackupTests(SimpleTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "source.sqlite3"
        self.media = self.root / "media"
        self.media.mkdir()
        (self.media / "brand.png").write_bytes(b"brand-artifact")
        (self.root / "secrets.env").write_text("OPENAI_API_KEY=not-a-real-key")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executescript("""
                CREATE TABLE office_workspace (id INTEGER PRIMARY KEY, ai_mode TEXT, paused INTEGER);
                INSERT INTO office_workspace VALUES (1, 'openai', 0);
                CREATE TABLE office_routine (id INTEGER PRIMARY KEY, enabled INTEGER);
                INSERT INTO office_routine VALUES (1, 1);
                CREATE TABLE office_task (id INTEGER PRIMARY KEY, status TEXT);
                INSERT INTO office_task VALUES (1, 'queued');
                CREATE TABLE office_systemstate (id INTEGER PRIMARY KEY, paused INTEGER);
                INSERT INTO office_systemstate VALUES (1, 0);
                CREATE TABLE django_session (session_key TEXT PRIMARY KEY, session_data TEXT);
                INSERT INTO django_session VALUES ('active-browser-session-marker', 'signed-session-data-marker');
                CREATE TABLE office_owneraccesscode (id INTEGER PRIMARY KEY, digest TEXT);
                INSERT INTO office_owneraccesscode VALUES (1, 'temporary-access-digest-marker');
            """)
        self.archive = self.root / "backup.zip"

    def backup(self):
        return create_backup(self.archive, self.database, self.media)

    def test_consistent_backup_restores_records_and_artifacts_but_disables_execution(self):
        self.backup()
        result = restore_backup(self.archive, self.root / "laboratory")
        self.assertEqual((result / "media/brand.png").read_bytes(), b"brand-artifact")
        with closing(sqlite3.connect(result / "office.sqlite3")) as connection:
            self.assertEqual(connection.execute("SELECT ai_mode, paused FROM office_workspace").fetchone(), ("offline", 1))
            self.assertEqual(connection.execute("SELECT enabled FROM office_routine").fetchone(), (0,))
            self.assertEqual(connection.execute("SELECT status FROM office_task").fetchone(), ("paused",))
            self.assertEqual(connection.execute("SELECT paused FROM office_systemstate").fetchone(), (1,))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM django_session").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM office_owneraccesscode").fetchone()[0], 0)
        with zipfile.ZipFile(self.archive) as archive:
            self.assertNotIn("secrets.env", archive.namelist())
            self.assertFalse(json.loads(archive.read("manifest.json"))["secrets_included"])
        with closing(sqlite3.connect(self.database)) as source:
            self.assertEqual(source.execute("SELECT ai_mode, paused FROM office_workspace").fetchone(), ("openai", 0))

    def test_hash_tampering_is_rejected_without_creating_restore(self):
        self.backup()
        tampered = self.root / "tampered.zip"
        with zipfile.ZipFile(self.archive) as source, zipfile.ZipFile(tampered, "w") as output:
            for name in source.namelist():
                content = source.read(name)
                output.writestr(name, b"other-artifact" if name == "media/brand.png" else content)
        target = self.root / "laboratory"
        with self.assertRaises(ValueError):
            restore_backup(tampered, target)
        self.assertFalse(target.exists())

    def test_archive_removes_live_sessions_without_altering_source(self):
        self.backup()
        with zipfile.ZipFile(self.archive) as archive:
            snapshot = archive.read("office.sqlite3")
        self.assertNotIn(b"active-browser-session-marker", snapshot)
        self.assertNotIn(b"signed-session-data-marker", snapshot)
        self.assertNotIn(b"temporary-access-digest-marker", snapshot)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT session_key FROM django_session").fetchone(),
                             ("active-browser-session-marker",))
            self.assertEqual(connection.execute("SELECT digest FROM office_owneraccesscode").fetchone(),
                             ("temporary-access-digest-marker",))

    def test_existing_data_is_never_overwritten(self):
        self.backup()
        with self.assertRaises(ValueError):
            restore_backup(self.archive, self.root)
        self.assertTrue(self.database.exists())

    def test_zip_traversal_is_rejected(self):
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("../escape.txt", "forbidden")
        with self.assertRaises(ValueError):
            restore_backup(self.archive, self.root / "laboratory")
        self.assertFalse((self.root.parent / "escape.txt").exists())

    def test_windows_aliases_and_device_paths_are_rejected(self):
        for name in ['media/.. /outside', 'media/CON', 'media/nul.txt', 'media/a./logo.png',
                     'media//logo.png', 'media/./logo.png', 'C:/outside', '\\\\server\\share']:
            with self.subTest(name=name):
                self.assertFalse(_safe_member(name))

    def test_invalid_manifest_fails_before_extraction(self):
        with zipfile.ZipFile(self.archive, 'w') as archive:
            archive.writestr('manifest.json', '[]')
        with self.assertRaises(ValueError):
            restore_backup(self.archive, self.root / 'laboratory')
        self.assertFalse((self.root / 'laboratory').exists())

    def test_snapshot_includes_committed_wal_content(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("INSERT INTO office_task VALUES (2, 'completed')")
            connection.commit()
            self.backup()
        restored = restore_backup(self.archive, self.root / "laboratory")
        with closing(sqlite3.connect(restored / "office.sqlite3")) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM office_task").fetchone()[0], 2)
