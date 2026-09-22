"""Secret storage for provider credentials.

Values are encrypted with Fernet (symmetric, authenticated) using a key from
the environment and kept in their own table. The API never serialises a
secret value; the only readers are adapters at invocation time.
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ProviderSecret


log = logging.getLogger("bevro.secrets")


def load_or_create_key() -> str:
    """BEVRO_SECRET_KEY if set; otherwise a key generated once into BEVRO_SECRET_KEY_FILE.

    The generated file is the development convenience that lets a fresh clone
    run with no configuration. Set BEVRO_SECRET_KEY explicitly for anything
    shared, and keep it the same for the API and the worker.
    """
    settings = get_settings()
    if settings.secret_key:
        return settings.secret_key
    path = Path(settings.secret_key_file)
    try:
        if path.is_file():
            key = path.read_text(encoding="utf-8").strip()
            if key:
                return key
        path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key().decode()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(key + "\n")
        log.info("generated a new secret key at %s", path)
        return key
    except FileExistsError:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"no BEVRO_SECRET_KEY and cannot use {path}: {exc}") from exc


class SecretStore:
    def __init__(self, key: str | None = None) -> None:
        raw = key if key is not None else load_or_create_key()
        if not raw:
            raise RuntimeError("BEVRO_SECRET_KEY is not set; refusing to store secrets unencrypted")
        try:
            self._fernet = Fernet(raw.encode() if isinstance(raw, str) else raw)
        except ValueError as exc:
            raise RuntimeError("BEVRO_SECRET_KEY is not a valid Fernet key (44 url-safe base64 characters)") from exc

    def put(self, db: Session, provider_id: uuid.UUID, name: str, value: str) -> None:
        ciphertext = self._fernet.encrypt(value.encode("utf-8"))
        row = db.scalar(
            select(ProviderSecret).where(ProviderSecret.provider_id == provider_id, ProviderSecret.name == name)
        )
        if row is None:
            db.add(ProviderSecret(provider_id=provider_id, name=name, ciphertext=ciphertext))
        else:
            row.ciphertext = ciphertext

    def names(self, db: Session, provider_id: uuid.UUID) -> list[str]:
        rows = db.scalars(select(ProviderSecret.name).where(ProviderSecret.provider_id == provider_id)).all()
        return sorted(rows)

    def resolve(self, db: Session, provider_id: uuid.UUID) -> dict[str, str]:
        """Decrypt every secret for one provider. For adapter use only."""
        out: dict[str, str] = {}
        for row in db.scalars(select(ProviderSecret).where(ProviderSecret.provider_id == provider_id)):
            try:
                out[row.name] = self._fernet.decrypt(row.ciphertext).decode("utf-8")
            except InvalidToken:
                # Key rotated without re-encrypting; treat as absent rather than crash.
                continue
        return out

    def delete(self, db: Session, provider_id: uuid.UUID, name: str) -> bool:
        row = db.scalar(
            select(ProviderSecret).where(ProviderSecret.provider_id == provider_id, ProviderSecret.name == name)
        )
        if row is None:
            return False
        db.delete(row)
        return True
