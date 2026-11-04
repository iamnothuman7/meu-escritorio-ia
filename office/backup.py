"""Consistent, verified local backups. Credentials and configuration are never included."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
import zipfile

from django.conf import settings


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_database(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("O banco do backup falhou na verificação de integridade.")
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("O banco contém referências inconsistentes.")


def create_backup(output_path=None, database_path=None, media_root=None) -> Path:
    database = Path(database_path or settings.DATABASES["default"]["NAME"]).resolve()
    media = Path(media_root or settings.MEDIA_ROOT).resolve()
    if not database.is_file():
        raise ValueError("Banco local não encontrado; execute as migrações primeiro.")
    if output_path is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        output_path = Path(settings.DATA_DIR) / "backups" / f"nabi-office-{stamp}.zip"
    output = Path(output_path).resolve()
    if output == database or output.is_dir():
        raise ValueError("Destino de backup inválido.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nabi-backup-") as temporary:
        staging = Path(temporary)
        snapshot = staging / "office.sqlite3"
        # SQLite's online backup API includes committed WAL data without copying live files.
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(snapshot)) as destination:
                source.backup(destination)
        _validate_database(snapshot)
        files = {"office.sqlite3": snapshot}
        if media.exists():
            for path in sorted(media.rglob("*")):
                if path.is_symlink():
                    raise ValueError("O diretório de arquivos contém link simbólico.")
                if path.is_file():
                    name = "media/" + path.relative_to(media).as_posix()
                    copy = staging / name
                    copy.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, copy)
                    files[name] = copy
        manifest = {
            "format": "nabi-office-backup",
            "version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "secrets_included": False,
            "files": {name: {"sha256": _digest(path), "size": path.stat().st_size}
                      for name, path in files.items()},
        }
        handle, temporary_archive = tempfile.mkstemp(prefix=".nabi-", suffix=".zip", dir=output.parent)
        os.close(handle)
        try:
            with zipfile.ZipFile(temporary_archive, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                for name, path in files.items():
                    archive.write(path, name)
            os.replace(temporary_archive, output)
        finally:
            Path(temporary_archive).unlink(missing_ok=True)
    return output


def _safe_member(name: str) -> bool:
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        return False
    parts = PurePosixPath(name)
    if parts.is_absolute():
        return False
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    for part in name.split('/'):
        if not part or part in {'.', '..'} or part != part.rstrip(' .') or any(ord(c) < 32 for c in part):
            return False
        if part.split('.')[0].upper() in reserved:
            return False
    return True


def _disable_execution(database: Path) -> None:
    with closing(sqlite3.connect(database)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        updates = {
            "office_workspace": "UPDATE office_workspace SET ai_mode='offline', paused=1",
            "office_routine": "UPDATE office_routine SET enabled=0",
            "office_systemstate": "UPDATE office_systemstate SET paused=1",
            "office_task": "UPDATE office_task SET status='paused' WHERE status IN ('queued','running')",
            "django_session": "DELETE FROM django_session",
        }
        for table, statement in updates.items():
            if table in tables:
                connection.execute(statement)
        if "office_systemstate" in tables:
            connection.execute("INSERT OR IGNORE INTO office_systemstate (id, paused) VALUES (1, 1)")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(office_systemstate)")}
            if "worker_heartbeat" in columns:
                connection.execute("UPDATE office_systemstate SET worker_heartbeat=NULL")
        # A restored worker must obtain a fresh lease on its own machine.
        if "office_workerlease" in tables:
            connection.execute("DELETE FROM office_workerlease")
        connection.commit()


def restore_backup(archive_path, target_dir) -> Path:
    """Restore only to an empty laboratory directory, after every hash is verified."""
    source = Path(archive_path).resolve()
    target = Path(target_dir).resolve()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError("A restauração exige uma pasta de laboratório vazia.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".nabi-restore-", dir=target.parent) as temporary:
        staging = Path(temporary)
        with zipfile.ZipFile(source) as archive:
            names = archive.namelist()
            if len(names) != len({name.casefold() for name in names}) or any(not _safe_member(name) for name in names):
                raise ValueError("O pacote contém caminhos inválidos ou duplicados.")
            if "manifest.json" not in names or archive.getinfo("manifest.json").file_size > 2_000_000:
                raise ValueError("Manifesto de backup ausente ou inválido.")
            manifest = json.loads(archive.read("manifest.json"))
            if not isinstance(manifest, dict):
                raise ValueError("Manifesto de backup inválido.")
            if manifest.get("format") != "nabi-office-backup" or manifest.get("version") != 1:
                raise ValueError("Formato de backup não reconhecido.")
            records = manifest.get("files", {})
            if not isinstance(records, dict) or "office.sqlite3" not in records:
                raise ValueError("O manifesto não contém o banco.")
            if set(names) != set(records) | {"manifest.json"}:
                raise ValueError("O pacote não corresponde ao manifesto.")
            for name, expected in records.items():
                if not isinstance(expected, dict) or not isinstance(expected.get("size"), int) or expected["size"] < 0 or not isinstance(expected.get("sha256"), str):
                    raise ValueError("Metadados de arquivo inválidos no manifesto.")
                if name != "office.sqlite3" and not name.startswith("media/"):
                    raise ValueError("O pacote contém arquivo fora dos dados permitidos.")
                item = archive.getinfo(name)
                if item.file_size != expected.get("size"):
                    raise ValueError("O tamanho de um arquivo não corresponde ao manifesto.")
                destination = staging / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as stream, destination.open("wb") as output:
                    shutil.copyfileobj(stream, output)
                if _digest(destination) != expected.get("sha256"):
                    raise ValueError("Hash inválido: o pacote está danificado ou foi alterado.")
        _validate_database(staging / "office.sqlite3")
        _disable_execution(staging / "office.sqlite3")
        _validate_database(staging / "office.sqlite3")
        (staging / "restoration.json").write_text(json.dumps({
            "restored_at": datetime.now(timezone.utc).isoformat(),
            "execution_disabled": True,
            "source_created_at": manifest.get("created_at"),
        }, indent=2), encoding="utf-8")
        target.mkdir(exist_ok=True)
        # All archive validation and database edits finished before copying to the destination.
        for path in staging.iterdir():
            shutil.move(str(path), str(target / path.name))
    return target
