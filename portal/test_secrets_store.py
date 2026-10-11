"""Isolated file-vault tests. No operating-system Keychain is accessed."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from secrets_store import FileSecrets, KeychainSecrets, SecretStoreError, default_secret_store


class FileSecretTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # macOS /var aliases /private/var; intentionally pass a symlink-free path.
        self.directory = Path(self.temp.name).resolve() / 'vault'
        self.vault = FileSecrets(self.directory)

    def tearDown(self):
        self.temp.cleanup()

    def test_persistence_atomic_replacement_and_exact_permissions(self):
        self.assertIsNone(self.vault.get('nextemby'))
        self.vault.set('nextemby', 'TEST-ONLY-OLD')
        old_inode = (self.directory / 'nextemby').stat().st_ino
        self.vault.set('nextemby', 'TEST-ONLY-NEW')
        self.assertNotEqual((self.directory / 'nextemby').stat().st_ino, old_inode)
        self.assertEqual(FileSecrets(self.directory).get('nextemby'), 'TEST-ONLY-NEW')
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.directory / 'nextemby').stat().st_mode), 0o600)
        self.assertEqual([file.name for file in self.directory.iterdir()], ['nextemby'])

    def test_concurrent_ensure_returns_one_persistent_encryption_key(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            values = list(executor.map(lambda _: FileSecrets(self.directory).ensure('card-encryption'), range(24)))
        self.assertEqual(len(set(values)), 1)
        self.assertEqual(self.vault.ensure('card-encryption'), values[0])

    def test_refuses_weak_directory_and_file_permissions(self):
        self.directory.chmod(0o750)
        with self.assertRaises(SecretStoreError):
            FileSecrets(self.directory)
        self.directory.chmod(0o700)
        self.vault.set('bridge', 'TEST-ONLY')
        (self.directory / 'bridge').chmod(0o640)
        for operation in (lambda: self.vault.get('bridge'), lambda: self.vault.set('bridge', 'NEW-TEST-ONLY')):
            with self.assertRaises(SecretStoreError):
                operation()

    def test_refuses_symbolic_links_in_directory_and_secret_paths(self):
        alias = self.directory.parent / 'alias'
        alias.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(SecretStoreError):
            FileSecrets(alias)
        with self.assertRaises(SecretStoreError):
            FileSecrets(alias / 'nested')
        self.vault.set('bridge', 'TEST-ONLY')
        (self.directory / 'nextemby').symlink_to(self.directory / 'bridge')
        for operation in (lambda: self.vault.get('nextemby'), lambda: self.vault.set('nextemby', 'NEW-TEST-ONLY')):
            with self.assertRaises(SecretStoreError):
                operation()
        self.assertEqual(self.vault.get('bridge'), 'TEST-ONLY')

    def test_refuses_hardlinks_and_invalid_names_without_leaking_values(self):
        self.vault.set('bridge', 'TEST-ONLY-SECRET')
        os.link(self.directory / 'bridge', self.directory / 'nextemby')
        with self.assertRaises(SecretStoreError):
            self.vault.get('bridge')
        for name in ('../bridge', '/bridge', 'bridge/child', 'DO-NOT-EXPOSE'):
            with self.assertRaises(SecretStoreError) as raised:
                self.vault.set(name, 'TEST-ONLY-SECRET')
            self.assertNotIn('TEST-ONLY-SECRET', str(raised.exception))
            self.assertNotIn(name, str(raised.exception))

    def test_failed_replace_preserves_original_and_cleans_temporary_file(self):
        self.vault.set('bridge', 'TEST-ONLY-ORIGINAL')
        with patch('secrets_store.os.replace', side_effect=OSError('DO-NOT-EXPOSE')):
            with self.assertRaises(SecretStoreError) as raised:
                self.vault.set('bridge', 'TEST-ONLY-NEW')
        self.assertNotIn('DO-NOT-EXPOSE', str(raised.exception))
        self.assertEqual(self.vault.get('bridge'), 'TEST-ONLY-ORIGINAL')
        self.assertEqual([file.name for file in self.directory.iterdir()], ['bridge'])

    def test_backend_selection_is_explicit_and_mac_default_is_preserved(self):
        with patch.dict(os.environ, {'MEDIADOCK_SECRETS_DIR': str(self.directory)}):
            self.assertIsInstance(default_secret_store(), FileSecrets)
        with patch.dict(os.environ, {'MEDIADOCK_SECRETS_DIR': ''}):
            with patch('secrets_store.sys.platform', 'darwin'):
                self.assertIsInstance(default_secret_store(), KeychainSecrets)
            with patch('secrets_store.sys.platform', 'linux'):
                with self.assertRaises(SecretStoreError):
                    default_secret_store()


if __name__ == '__main__':
    unittest.main()
