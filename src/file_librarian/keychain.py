"""The macOS Keychain, through ``/usr/bin/security``.

Scheduled runs start from launchd, which does not see your shell's
environment or the repository's ``.env``. Keeping the API key in the login
Keychain lets them find it without a secret in any text file. Each provider
that takes a key has its own account under the same service.

The secret is passed on standard input, never as an argument, so it does not
show up in the process list. Nothing here logs or prints a secret.
"""

from __future__ import annotations

import subprocess

SECURITY = "/usr/bin/security"
SERVICE = "file-librarian"
ACCOUNT = "anthropic-api-key"
OPENAI_COMPAT_ACCOUNT = "openai-compat-api-key"
"""One account per provider, so an ``openai-compat`` key never replaces the Anthropic one."""
_NOT_FOUND = 44
_TIMEOUT = 10


class KeychainError(RuntimeError):
    """The Keychain could not be read or written."""


def _security(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        # Fixed binary and arguments; the secret only ever travels on stdin.
        return subprocess.run(  # noqa: S603
            [SECURITY, *args],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise KeychainError(f"cannot run {SECURITY}: {exc}") from exc


def read(account: str = ACCOUNT) -> str | None:
    """The stored secret, or ``None`` if there is none."""
    result = _security("find-generic-password", "-s", SERVICE, "-a", account, "-w")
    if result.returncode == _NOT_FOUND:
        return None
    if result.returncode != 0:
        raise KeychainError(f"Keychain read failed (code {result.returncode})")
    value = result.stdout.strip()
    return value or None


def write(value: str, account: str = ACCOUNT) -> None:
    """Store or replace the secret."""
    if not value or "\n" in value:
        raise ValueError("the secret must be a single non-empty line")
    result = _security(
        "add-generic-password", "-U", "-s", SERVICE, "-a", account, "-w",
        stdin=f"{value}\n{value}\n",
    )  # fmt: skip
    if result.returncode != 0:
        raise KeychainError(f"Keychain write failed (code {result.returncode})")


def delete(account: str = ACCOUNT) -> bool:
    """Remove the secret. ``False`` if there was none."""
    result = _security("delete-generic-password", "-s", SERVICE, "-a", account)
    if result.returncode == _NOT_FOUND:
        return False
    if result.returncode != 0:
        raise KeychainError(f"Keychain delete failed (code {result.returncode})")
    return True
