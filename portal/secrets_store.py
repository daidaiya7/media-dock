"""Platform credential storage. Errors never contain credential values."""
import base64
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import secrets
import stat
import sys

SERVICE = 'com.mediadock.carddesk'


class SecretStoreError(Exception):
    """A sanitized configuration or storage failure."""


class FileSecrets:
    """Private, owner-only files for a single Linux service account.

    Credential names match the Keychain names, without an extension. A directory
    lock serializes creation and replacement; openat/O_NOFOLLOW prevents following
    symlinks in the configured path or credential files.
    """
    def __init__(self, directory):
        self.directory = Path(directory)
        if not self.directory.is_absolute() or '..' in self.directory.parts:
            raise SecretStoreError('凭据目录必须是绝对路径且不能包含上级目录')
        with self._directory():
            pass

    @staticmethod
    def _name(name):
        if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', name):
            raise SecretStoreError('凭据名称无效')
        return name

    @contextmanager
    def _directory(self):
        descriptor = None
        try:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            descriptor = os.open('/', flags)
            for part in self.directory.parts[1:]:
                try:
                    child = os.open(part, flags, dir_fd=descriptor)
                except FileNotFoundError:
                    try:
                        os.mkdir(part, 0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                    child = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            info = os.fstat(descriptor)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise SecretStoreError('凭据目录必须归服务用户所有，权限为 0700')
            yield descriptor
        except OSError:
            raise SecretStoreError('凭据存储不可用，请检查目录归属和权限') from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _check_file(info):
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise SecretStoreError('凭据文件必须是服务用户独有的普通文件，权限为 0600')

    def _read(self, directory_fd, name):
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        except FileNotFoundError:
            return None
        try:
            self._check_file(os.fstat(descriptor))
            with os.fdopen(descriptor, 'rb', closefd=False) as source:
                raw = source.read(8193)
            try:
                value = raw.decode('utf-8')
            except UnicodeError:
                raise SecretStoreError('凭据文件格式无效') from None
            self._value(value)
            return value
        finally:
            os.close(descriptor)

    @staticmethod
    def _value(value):
        try:
            raw = value.encode('utf-8') if isinstance(value, str) else b''
        except UnicodeError:
            raise SecretStoreError('凭据格式无效') from None
        if (not raw or len(raw) > 8192 or any(ord(character) < 32 for character in value)):
            raise SecretStoreError('凭据格式无效')
        return raw

    def _write(self, directory_fd, name, value):
        raw = self._value(value)
        # Refuse unsafe pre-existing targets instead of silently replacing them.
        try:
            self._check_file(os.stat(name, dir_fd=directory_fd, follow_symlinks=False))
        except FileNotFoundError:
            pass
        temporary = '.tmp-' + secrets.token_hex(16)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory_fd)
        try:
            with os.fdopen(descriptor, 'wb') as target:
                target.write(raw)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            os.fsync(directory_fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass

    def get(self, name):
        name = self._name(name)
        with self._directory() as directory_fd:
            return self._read(directory_fd, name)

    def set(self, name, value):
        name = self._name(name)
        with self._directory() as directory_fd:
            fcntl.flock(directory_fd, fcntl.LOCK_EX)
            self._write(directory_fd, name, value)

    def ensure(self, name):
        name = self._name(name)
        with self._directory() as directory_fd:
            fcntl.flock(directory_fd, fcntl.LOCK_EX)
            value = self._read(directory_fd, name)
            if value is None:
                value = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
                self._write(directory_fd, name, value)
            return value

    def delete(self, name):
        name = self._name(name)
        with self._directory() as directory_fd:
            fcntl.flock(directory_fd, fcntl.LOCK_EX)
            try:
                self._check_file(os.stat(name, dir_fd=directory_fd, follow_symlinks=False))
            except FileNotFoundError:
                return
            os.unlink(name, dir_fd=directory_fd)
            os.fsync(directory_fd)


def default_secret_store():
    directory = os.environ.get('MEDIADOCK_SECRETS_DIR')
    if directory:
        return FileSecrets(directory)
    if sys.platform != 'darwin':
        raise SecretStoreError('服务器必须配置 MEDIADOCK_SECRETS_DIR 凭据目录')
    return KeychainSecrets()

class KeychainSecrets:
    def get(self, name):
        import keyring
        return keyring.get_password(SERVICE, name)

    def set(self, name, value):
        import keyring
        keyring.set_password(SERVICE, name, value)

    def ensure(self, name):
        value = self.get(name)
        if not value:
            value = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
            self.set(name, value)
        return value

    def delete(self, name):
        import keyring
        try:
            keyring.delete_password(SERVICE, name)
        except keyring.errors.PasswordDeleteError:
            if self.get(name) is not None:
                raise SecretStoreError('无法清除凭据，请重试') from None

class MemorySecrets:
    """仅供隔离测试；正式启动不使用内存凭证后端。"""
    def __init__(self):
        self.values = {}
    def get(self, name):
        return self.values.get(name)
    def set(self, name, value):
        self.values[name] = value
    def ensure(self, name):
        if not self.get(name):
            self.set(name, base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
        return self.get(name)

    def delete(self, name):
        self.values.pop(name, None)
