import unittest

from keyring.backends import fail
from keyring.errors import PasswordDeleteError, PasswordSetError

from podcast_automate.key_store import KINDS, SERVICE, Vault
from podcast_automate.logs import LOGGER


class MemoryKeyring:
    """A keyring backend in memory; a plain class, so keyring never registers it as a backend of the process."""

    def __init__(self, refuse_set=False, refuse_get=()):
        self.entries, self.refuse_set, self.refuse_get = {}, refuse_set, set(refuse_get)

    def get_password(self, service, username):
        if username in self.refuse_get:
            raise RuntimeError("locked store")
        return self.entries.get((service, username))

    def set_password(self, service, username, password):
        if self.refuse_set:
            raise PasswordSetError("refused")
        self.entries[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.entries:
            raise PasswordDeleteError("missing")
        del self.entries[(service, username)]


class VaultTests(unittest.TestCase):
    """D-167: the Studio's keys in the system's credential store, one entry per kind, never the system's own here."""

    def test_a_key_is_stored_loaded_and_removed_under_its_kind(self):
        backend = MemoryKeyring()
        vault = Vault(backend)
        self.assertTrue(vault.available())
        self.assertEqual(KINDS, ("openrouter", "google", "anthropic", "perplexity", "core"))
        self.assertTrue(vault.save("core", "core-key-1"))
        self.assertTrue(vault.save("openrouter", "sk-or-key-1"))
        self.assertEqual(backend.entries, {(SERVICE, "core"): "core-key-1", (SERVICE, "openrouter"): "sk-or-key-1"})
        self.assertEqual(vault.load(), {"openrouter": "sk-or-key-1", "core": "core-key-1"})
        # An empty value removes the entry; removing one that is not there is no error.
        self.assertTrue(vault.save("core", ""))
        self.assertTrue(vault.save("google", ""))
        self.assertEqual(vault.load(), {"openrouter": "sk-or-key-1"})
        with self.assertRaises(ValueError):
            vault.save("openai", "sk-key")

    def test_a_system_without_a_store_keeps_nothing(self):
        vault = Vault(fail.Keyring())
        self.assertFalse(vault.available())
        self.assertEqual(vault.load(), {})
        self.assertFalse(vault.save("core", "core-key-1"))

    def test_a_refusing_store_leaves_no_older_key_behind_and_never_logs_one(self):
        backend = MemoryKeyring()
        vault = Vault(backend)
        vault.save("google", "AIza-old-key")
        backend.refuse_set = True
        with self.assertLogs(LOGGER + ".key_store", "WARNING") as logged:
            self.assertFalse(vault.save("google", "AIza-new-key"))
        # The older key must not come back after a restart in place of the one just entered.
        self.assertEqual(backend.entries, {})
        backend.refuse_set, backend.refuse_get = False, {"anthropic"}
        vault.save("core", "core-key-1")
        backend.entries[(SERVICE, "anthropic")] = "sk-ant-key-1"
        with self.assertLogs(LOGGER + ".key_store", "WARNING") as unreadable:
            self.assertEqual(vault.load(), {"core": "core-key-1"})
        lines = "\n".join(logged.output + unreadable.output)
        self.assertIn("PasswordSetError", lines)
        for secret in ("AIza-old-key", "AIza-new-key", "sk-ant-key-1", "locked store"):
            self.assertNotIn(secret, lines)


if __name__ == "__main__":
    unittest.main()
