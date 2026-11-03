"""
DataGatherer - secrets in the macOS Keychain

Tokens and API keys live ONLY in the Keychain (through the 'keyring' library, whose macOS
backend stores generic passwords in the login keychain). Never in config.json, the
database, the vault, a log or an API answer: the routes only ever say whether a secret is
there (has()). DataVault never stores a password: only OAuth tokens, client IDs and API keys.

Every item has the service 'nl.datavault.datagatherer.<source id>' and the setting key as
its user name, so 'Vergeet dit account' (Connector.forget) removes exactly those items.

Tests and test servers (DATAVAULT_TESTING=1) never reach the real Keychain: in that mode an
in-memory backend (MemoryKeyring) is installed on first use, and a real backend is refused.
"""
import os
import threading
from typing import Dict, Optional, Tuple

import keyring
from keyring.backend import KeyringBackend
from keyring.errors import KeyringError, PasswordDeleteError

SERVICE_PREFIX = "nl.datavault.datagatherer"


class SecretStoreError(Exception):
    """The Keychain could not be read or written (locked, or access refused)."""

    def __str__(self) -> str:
        return "De Sleutelhanger van macOS is niet bereikbaar (vergrendeld, of toegang geweigerd)"


class MemoryKeyring(KeyringBackend):
    """A keyring that only lives in memory: for tests and test servers."""
    priority = 1
    datavault_fake = True

    def __init__(self):
        super().__init__()
        self.items: Dict[Tuple[str, str], str] = {}
        self._lock = threading.Lock()

    def get_password(self, service, username):
        with self._lock:
            return self.items.get((service, username))

    def set_password(self, service, username, password):
        with self._lock:
            self.items[(service, username)] = password

    def delete_password(self, service, username):
        with self._lock:
            if (service, username) not in self.items:
                raise PasswordDeleteError("not found")
            del self.items[(service, username)]


_install_lock = threading.Lock()


def _backend():
    """The keyring backend; in test mode always a MemoryKeyring, never the real Keychain."""
    if os.environ.get("DATAVAULT_TESTING") == "1":
        with _install_lock:
            current = keyring.get_keyring()
            if not getattr(current, "datavault_fake", False):
                keyring.set_keyring(MemoryKeyring())
                current = keyring.get_keyring()
            if not getattr(current, "datavault_fake", False):
                raise RuntimeError("DATAVAULT_TESTING=1 but the keyring is a real Keychain; refusing")
            return current
    return keyring.get_keyring()


def service(source_id: str) -> str:
    return f"{SERVICE_PREFIX}.{source_id}"


def get(source_id: str, key: str) -> Optional[str]:
    try:
        return _backend().get_password(service(source_id), key)
    except KeyringError as e:
        raise SecretStoreError() from e


def set(source_id: str, key: str, value: str) -> None:  # noqa: A001 (keyring's own name)
    if not isinstance(value, str) or not value:
        raise ValueError("empty secret")
    try:
        _backend().set_password(service(source_id), key, value)
    except KeyringError as e:
        raise SecretStoreError() from e


def delete(source_id: str, key: str) -> bool:
    """Remove one item; False when it was not there."""
    try:
        _backend().delete_password(service(source_id), key)
        return True
    except PasswordDeleteError:
        return False
    except KeyringError as e:
        raise SecretStoreError() from e


class Scoped:
    """The Keychain items of one connector."""

    def __init__(self, source_id: str):
        self.source_id = source_id

    def get(self, key: str) -> Optional[str]:
        return get(self.source_id, key)

    def has(self, key: str) -> bool:
        return bool(get(self.source_id, key))

    def set(self, key: str, value: str) -> None:
        set(self.source_id, key, value)

    def delete(self, key: str) -> bool:
        return delete(self.source_id, key)
