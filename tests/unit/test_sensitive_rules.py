"""OAuth client files and one-line JSON credentials are sensitive.

A client file as a cloud console names it (``client_secret_<id>.json``) was
filed like any document: its name matched no rule, and its content is a single
line of JSON, which the line-anchored assignment pattern never matched.
"""

from __future__ import annotations

import json

import pytest

from file_librarian.privacy import scrub
from file_librarian.sensitive import find_secrets, sensitive_reason

# Built at run time so nothing that looks like a real secret sits in the source.
CLIENT_SECRET = "GOCSPX-" + "aB3" * 9
OPAQUE = "zQ7" * 8


@pytest.mark.parametrize(
    "name",
    [
        "client_secret_1234567890-abcdefg.apps.googleusercontent.com.json",
        "client_secret_1234567890-abcdefg.apps.googleusercontent.com (1).json",
        "client_secret.json",
        "client-secret-web.json",
        "Client_Secret_copy.JSON",
        "token.json",
        "tokens.json",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
        ".htpasswd",
        "kubeconfig",
        "staging.kubeconfig",
        "terraform.tfstate",
        "prod.tfvars",
    ],
)
def test_credential_stores_are_sensitive_by_name(name: str) -> None:
    assert sensitive_reason(name) == "a credential file"


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("google client secret.txt", "client secret"),
        ("ClientSecrets-backup.zip", "client secrets"),
        ("stripe api key.txt", "api key"),
        ("API Keys 2026.xlsx", "api keys"),
        ("github-access-token.txt", "access token"),
        ("refresh_token_old.txt", "refresh token"),
        ("aws secret key.csv", "secret key"),
    ],
)
def test_names_that_say_they_hold_a_key_are_sensitive(name: str, reason: str) -> None:
    assert sensitive_reason(name) == reason


@pytest.mark.parametrize(
    "name",
    [
        "tokenizer.json",  # "token" inside a word
        "secretary-notes.pdf",
        "clients-2026.xlsx",
        "apikeyboard-layout.png",  # one word, not "api key"
        "the-secret-garden.epub",  # "secret" alone says nothing
        "keynote-access.pdf",
        "package.json",
        "tsconfig.json",
        "API Keys Manager.dmg",  # installers are never sensitive by name
    ],
)
def test_ordinary_names_stay_ordinary(name: str) -> None:
    assert sensitive_reason(name) is None


def test_allow_names_still_lets_one_through() -> None:
    assert sensitive_reason("client_secret_demo.json", ["client_secret_demo.json"]) is None


def test_a_one_line_oauth_client_file_is_caught_by_its_content() -> None:
    text = json.dumps(
        {
            "installed": {
                "client_id": "1234567890-abcdefg.apps.googleusercontent.com",
                "project_id": "example-project",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "client_secret": CLIENT_SECRET,
                "redirect_uris": ["http://localhost"],
            }
        },
        separators=(",", ":"),
    )
    assert "\n" not in text
    found = find_secrets(text)
    assert "Google OAuth client secret" in found
    assert "password or secret in JSON" in found


@pytest.mark.parametrize(
    "key",
    [
        "client_secret", "clientSecret", "password", "db_password", "api_key", "apiKey",
        "access_token", "refresh_token", "private_key", "secret",
    ],
)  # fmt: skip
@pytest.mark.parametrize("quote", ['"', "'"])
def test_quoted_keys_are_caught_anywhere_on_a_line(key: str, quote: str) -> None:
    text = f"{{{quote}name{quote}:{quote}x{quote},{quote}{key}{quote}: {quote}{OPAQUE}{quote}}}"
    assert "password or secret in JSON" in find_secrets(text)


@pytest.mark.parametrize(
    "text",
    [
        '{"client_id":"1234567890-abcdefg.apps.googleusercontent.com"}',
        '{"token_type":"Bearer","expires_in":3599}',
        '{"password":""}',
        '{"password":"short"}',  # under eight characters: a placeholder, not a secret
        '{"secret":null,"api_key":false}',
        '{"description":"how to rotate a client secret safely"}',
        '{"password_policy":{"min_length":12}}',
    ],
)
def test_json_without_a_secret_value_is_not_flagged(text: str) -> None:
    assert find_secrets(text) == []


def test_the_scrubber_removes_the_value_of_a_json_secret() -> None:
    text = f'config {{"client_secret":"{CLIENT_SECRET}"}} end'
    cleaned = scrub(text)
    assert CLIENT_SECRET not in cleaned
    assert "GOCSPX" not in cleaned
