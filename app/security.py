"""Encryption for stored secrets (Looker API secrets, Cortex keys). Secrets never go back to the browser."""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    if settings.secret_key:
        return Fernet(settings.secret_key.encode())
    key_file = settings.data_dir / "secret.key"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if not key_file.exists():
        key_file.write_bytes(Fernet.generate_key())
        try:
            key_file.chmod(0o600)  # owner only (no effect on Windows)
        except OSError:
            pass
    return Fernet(key_file.read_bytes().strip())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode() if value else ""


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return ""
