"""The Studio's keys across restarts, in the operating system's credential store (D-167).

Windows keeps them in its Credential Manager, macOS in the login keychain and Linux in the Secret Service, each
encrypted with the user's login, through the ``keyring`` package: one entry per key under the service
``podcast-automate`` with the key's kind as the user name. A system without such a store (keyring's fail backend,
or a store that refuses) leaves every key in the Studio's memory only, as before. Only ``pla studio`` opens the store
(``studio.make_server(..., vault=Vault())``); a Studio built without one never reads or writes it.
"""
from __future__ import annotations

from .logs import logger

SERVICE = "podcast-automate"
# Every key the Studio holds: OpenRouter, Google, Anthropic, Perplexity and CORE's free key.
KINDS = ("openrouter", "google", "anthropic", "perplexity", "core")


class Vault:
    """The credential store; ``backend`` is a keyring backend, the system's own when none is given."""

    def __init__(self, backend=None):
        self._backend = backend

    def backend(self):
        if self._backend is None:
            import keyring
            self._backend = keyring.get_keyring()
        return self._backend

    def available(self) -> bool:
        """Whether a store is there that keeps keys; keyring falls back to its fail backend when none is."""
        try:
            from keyring.backends import fail
            return not isinstance(self.backend(), fail.Keyring)
        except Exception:  # noqa: BLE001 - a store that cannot even be found keeps nothing
            return False

    def load(self) -> dict[str, str]:
        """Every stored key by kind. A key that cannot be read is left out, and the Studio asks for it as before."""
        if not self.available():
            return {}
        found = {}
        for kind in KINDS:
            try:
                value = self.backend().get_password(SERVICE, kind)
            except Exception as exc:  # noqa: BLE001 - the type alone, an error text might quote the entry
                logger("key_store").warning("Key %s nicht aus dem Tresor gelesen (%s).", kind, type(exc).__name__)
                continue
            if value:
                found[kind] = value
        return found

    def save(self, kind, value) -> bool:
        """Store one key, or remove it for an empty value; False when the store does not keep it (memory only)."""
        if kind not in KINDS:
            raise ValueError(f"unknown key kind: {kind}")
        if not self.available():
            return False
        try:
            if value:
                self.backend().set_password(SERVICE, kind, value)
            else:
                self.remove(kind)
            return True
        except Exception as exc:  # noqa: BLE001 - see load
            logger("key_store").warning("Key %s nicht im Tresor gespeichert (%s).", kind, type(exc).__name__)
            # An older entry must not come back after a restart in place of the key just entered.
            self.remove(kind, quiet=True)
            return False

    def remove(self, kind, *, quiet=False) -> None:
        from keyring.errors import PasswordDeleteError
        try:
            self.backend().delete_password(SERVICE, kind)
        except PasswordDeleteError:
            pass
        except Exception:  # noqa: BLE001 - quiet only while recovering from a failed save
            if not quiet:
                raise
