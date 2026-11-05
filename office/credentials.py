"""Owner-scoped provider secrets, stored outside the database and media tree."""
import json
import os
from pathlib import Path
import re
import tempfile

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

from .chatgpt_plan import PlanError, _protect_directory, _session_lock


API_MODES = ("openai", "claude")
MAX_KEY_SIZE = 2048
MAX_RECORD_SIZE = 8192


class CredentialError(ValueError):
    pass


def _location(owner_id, mode):
    if type(owner_id) is not int or owner_id < 1 or mode not in API_MODES:
        raise CredentialError("Conta ou provedor inválido para esta credencial.")
    directory = Path(settings.DATA_DIR).resolve() / "provider-credentials"
    if (directory.is_symlink() or directory.is_relative_to(Path(settings.BASE_DIR).resolve())
            or directory.is_relative_to(Path(settings.MEDIA_ROOT).resolve())):
        raise CredentialError("A pasta de credenciais precisa ficar fora do código e da mídia.")
    return directory, directory / f"{owner_id}-{mode}.enc"


def _read_private(path, maximum):
    if path.is_symlink():
        raise CredentialError("O arquivo de credencial não pode ser um link simbólico.")
    with path.open("rb") as stream:
        if os.name != "nt" and os.fstat(stream.fileno()).st_mode & 0o077:
            raise CredentialError("O arquivo de credencial precisa de permissões privadas.")
        value = stream.read(maximum + 1)
    if len(value) > maximum:
        raise CredentialError("O arquivo de credencial está inválido.")
    return value


def _write_private(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix=".provider-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _cipher(directory, *, create=False):
    master = directory / "master.key"
    try:
        key = _read_private(master, 128)
    except FileNotFoundError:
        if not create:
            raise CredentialError("A chave de proteção do cofre está indisponível.") from None
        key = Fernet.generate_key()
        _write_private(master, key)
    return Fernet(key)


def _normalize_key(key):
    value = key.strip() if isinstance(key, str) else ""
    if not re.fullmatch(r"[!-~]{16," + str(MAX_KEY_SIZE) + r"}", value):
        raise CredentialError("Informe uma chave de API válida, sem espaços internos.")
    return value


def save_api_key(owner_id, mode, key):
    directory, path = _location(owner_id, mode)
    value = _normalize_key(key)
    try:
        _protect_directory(directory)
        with _session_lock(directory):
            payload = json.dumps({"owner_id": owner_id, "mode": mode, "key": value}).encode("utf-8")
            _write_private(path, _cipher(directory, create=True).encrypt(payload))
    except CredentialError:
        raise
    except (OSError, PlanError, ValueError):
        raise CredentialError("Não foi possível guardar a credencial com segurança.") from None


def load_api_key(owner_id, mode):
    directory, path = _location(owner_id, mode)
    try:
        try:
            encrypted = _read_private(path, MAX_RECORD_SIZE)
        except FileNotFoundError:
            return None
        payload = json.loads(_cipher(directory).decrypt(encrypted))
        if not isinstance(payload, dict) or payload.get("owner_id") != owner_id or payload.get("mode") != mode:
            raise CredentialError("A credencial pertence a outra conta ou provedor.")
        return _normalize_key(payload.get("key"))
    except CredentialError:
        raise
    except (OSError, InvalidToken, ValueError):
        raise CredentialError("Não foi possível ler a credencial protegida desta conta.") from None


def has_api_key(owner_id, mode):
    try:
        return bool(load_api_key(owner_id, mode))
    except CredentialError:
        return False


def delete_api_key(owner_id, mode):
    directory, path = _location(owner_id, mode)
    if not directory.is_dir():
        return
    try:
        with _session_lock(directory):
            path.unlink(missing_ok=True)
    except OSError:
        raise CredentialError("Não foi possível remover a credencial desta conta.") from None
