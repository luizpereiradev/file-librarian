"""The ``ollama`` provider's own API (``/api/chat``) and its handling of small models (#92).

Against the in-process fake server (``fake_openai``, loopback only); no test
reaches a real Ollama.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from file_librarian.config import ProviderConfig
from file_librarian.models import Attachment, CategoryRule, FileContext, Rules
from file_librarian.providers import ollama as ollama_module
from file_librarian.providers.base import FatalProviderError, ProviderError
from file_librarian.providers.ollama import (
    LOCAL_TIMEOUT,
    MAX_CONTEXT,
    MIN_CONTEXT,
    OllamaProvider,
    context_tokens,
    fit,
    match_category,
    native_root,
    repair_json,
)
from file_librarian.providers.prompt import response_schema, system_prompt
from tests.fake_openai import FakeOpenAI, Reply, completion, error

RULES = Rules(
    categories=(CategoryRule("Finance", "bills"), CategoryRule("Saúde"), CategoryRule("Other")),
    language="en",
)
CTX = FileContext(
    path=Path("/in/document(3).pdf"),
    sha256="0" * 64,
    size_bytes=1234,
    extension=".pdf",
    text="Electricity invoice, October.",
)
GOOD: dict[str, Any] = {
    "category": "Finance",
    "suggested_name": "Electricity invoice",
    "name_choice_reason": "The current name says nothing.",
    "name_choice": "proposed",
    "description": "Electricity invoice for October.",
    "tags": ["invoice"],
    "confidence": 0.93,
    "document_date": "2026-10-05",
}


def provider(server: FakeOpenAI, **kw: Any) -> OllamaProvider:
    data = {"name": "ollama", "model": "small:4b", "base_url": server.url} | kw
    return OllamaProvider(ProviderConfig.model_validate(data))


def chats(server: FakeOpenAI) -> list[dict[str, Any]]:
    return [s.body for s in server.requests if s.path == "/api/chat"]


# --- the request -----------------------------------------------------------------------


def test_classifies_through_ollamas_own_api(fake_openai: FakeOpenAI) -> None:
    fake_openai.script(completion(GOOD, tokens=(900, 80)))
    p = provider(fake_openai)
    answer = p.classify(CTX, RULES)
    assert answer.category == "Finance"
    [seen] = fake_openai.requests
    assert (seen.method, seen.path) == ("POST", "/api/chat")
    assert "Authorization" not in seen.headers
    body = seen.body
    assert body["model"] == "small:4b" and body["stream"] is False
    assert body["think"] is False
    assert body["format"] == response_schema(RULES)
    assert body["options"]["temperature"] == 0
    assert body["options"]["num_ctx"] == MIN_CONTEXT
    assert body["options"]["num_predict"] > 0
    system, user = body["messages"]
    assert system == {"role": "system", "content": system_prompt(RULES)}
    assert user["role"] == "user" and "Electricity invoice" in user["content"]
    assert (p.last_usage.input_tokens, p.last_usage.output_tokens) == (900, 80)
    assert not p.answers


def test_native_root_drops_v1() -> None:
    assert native_root("http://localhost:11434/v1") == "http://localhost:11434"
    assert native_root("http://localhost:11434/v1/") == "http://localhost:11434"
    assert native_root("http://box:11434") == "http://box:11434"


def test_images_go_as_base64(fake_openai: FakeOpenAI) -> None:
    fake_openai.script(completion(GOOD))
    image = FileContext(
        path=Path("/in/IMG_1.jpg"),
        sha256="1" * 64,
        size_bytes=10,
        extension=".jpg",
        attachment=Attachment("image/jpeg", b"\xff\xd8jpeg"),
    )
    p = provider(fake_openai, vision=True)
    assert p.attachments == frozenset({"image/jpeg"})
    p.classify(image, RULES)
    user = chats(fake_openai)[0]["messages"][1]
    assert user["images"] == ["/9hqcGVn"]


def test_timeout_defaults_to_local_speed() -> None:
    p = OllamaProvider(ProviderConfig(name="ollama", model="m"))
    assert p._chat_client._timeout == LOCAL_TIMEOUT
    set_by_user = OllamaProvider(ProviderConfig(name="ollama", model="m", timeout_seconds=12))
    assert set_by_user._chat_client._timeout == 12


# --- context length --------------------------------------------------------------------


def test_context_grows_with_the_prompt_up_to_a_cap() -> None:
    assert context_tokens(100) == MIN_CONTEXT
    assert MIN_CONTEXT < context_tokens(40_000) < MAX_CONTEXT
    assert context_tokens(40_000) % 2048 == 0
    assert context_tokens(10_000_000) == MAX_CONTEXT


def test_a_long_excerpt_is_cut_to_fit_the_context(fake_openai: FakeOpenAI) -> None:
    system = system_prompt(RULES)
    long = FileContext(
        path=Path("/in/a.txt"), sha256="2" * 64, size_bytes=1, extension=".txt", text="x" * 200_000
    )
    fitted = fit(long, system)
    assert fitted.text is not None and 50_000 < len(fitted.text) < 100_000
    assert fit(CTX, system) is CTX
    no_text = FileContext(path=Path("/in/b"), sha256="3" * 64, size_bytes=1, extension="")
    assert fit(no_text, system) is no_text
    fake_openai.script(completion(GOOD))
    provider(fake_openai).classify(long, RULES)
    assert chats(fake_openai)[0]["options"]["num_ctx"] == MAX_CONTEXT


# --- models without structured output or thinking control ------------------------------


def test_a_model_without_structured_output_gets_the_schema_in_the_prompt(
    fake_openai: FakeOpenAI,
) -> None:
    fake_openai.script(
        Reply(501, {"error": {"message": "structured output is unavailable"}}),
        completion(GOOD),
    )
    p = provider(fake_openai)
    assert p.classify(CTX, RULES).category == "Finance"
    p.classify(CTX, RULES)  # remembered: no second refusal
    first, second, third = chats(fake_openai)
    assert "format" in first
    assert "format" not in second and "format" not in third
    assert '"enum"' in second["messages"][0]["content"]  # the schema, in the instructions
    assert p.answers["unstructured"] == 2


def test_a_model_that_refuses_think_is_asked_without_it(fake_openai: FakeOpenAI) -> None:
    fake_openai.script(error(400, '"small:4b" does not support thinking'), completion(GOOD))
    p = provider(fake_openai)
    p.classify(CTX, RULES)
    first, second = chats(fake_openai)
    assert first["think"] is False and "think" not in second


def test_each_adaptation_happens_once(fake_openai: FakeOpenAI) -> None:
    fake_openai.script(error(400, "think is not supported, structured output is unavailable"))
    with pytest.raises(ProviderError, match="Ollama answered HTTP 400"):
        provider(fake_openai).classify(CTX, RULES)
    first, second, third = chats(fake_openai)
    assert "format" in first and "think" in first
    assert "format" not in second and "think" in second
    assert "format" not in third and "think" not in third


# --- errors ----------------------------------------------------------------------------


def test_model_not_pulled_is_fatal_and_says_how_to_pull_it(fake_openai: FakeOpenAI) -> None:
    fake_openai.script(error(404, "model 'small:4b' not found"))
    with pytest.raises(FatalProviderError, match=r"has no model 'small:4b': run `ollama pull"):
        provider(fake_openai).classify(CTX, RULES)


def test_temporary_errors_are_retryable(fake_openai: FakeOpenAI) -> None:
    fake_openai.script(error(503, "loading"))
    with pytest.raises(ProviderError, match="temporary error from Ollama") as caught:
        provider(fake_openai).classify(CTX, RULES)
    assert caught.value.retryable


def test_other_errors_fail_the_file(fake_openai: FakeOpenAI) -> None:
    fake_openai.script(error(400, "bad request"))
    with pytest.raises(ProviderError, match="Ollama answered HTTP 400: bad request") as caught:
        provider(fake_openai).classify(CTX, RULES)
    assert not caught.value.retryable and not isinstance(caught.value, FatalProviderError)


# --- repairs and the one retry ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "repaired"),
    [
        (json.dumps(GOOD), False),
        (f"```json\n{json.dumps(GOOD)}\n```", True),
        (f"<think>hmm</think>{json.dumps(GOOD)}", True),
        (f"Here is the answer:\n{json.dumps(GOOD)}\nHope it helps.", True),
        (json.dumps(GOOD)[:-1] + ",}", True),
        ('{"category": "Finance", "tags": ["a", "b",],}', True),
    ],
)
def test_repair_json(text: str, repaired: bool) -> None:
    data, was_repaired = repair_json(text)
    assert data["category"] == "Finance" and was_repaired is repaired


@pytest.mark.parametrize("text", ["no json here", "[1, 2]", "{broken", '{"a": }'])
def test_repair_json_gives_up(text: str) -> None:
    with pytest.raises(ValueError):
        repair_json(text)


def test_match_category() -> None:
    allowed = ["Finance", "Saúde", "Other"]
    assert match_category("Finance", allowed) == "Finance"
    assert match_category(" finance ", allowed) == "Finance"
    assert match_category("SAUDE", allowed) == "Saúde"
    assert match_category("Travel", allowed) is None
    assert match_category(3, allowed) is None


def test_a_near_valid_answer_is_repaired_without_asking_again(fake_openai: FakeOpenAI) -> None:
    fake_openai.script(completion("Sure!\n```json\n" + json.dumps(GOOD | {"category": "finance"})))
    p = provider(fake_openai)
    assert p.classify(CTX, RULES).category == "Finance"
    assert len(chats(fake_openai)) == 1
    assert p.answers == {"repaired": 1, "category_matched": 1}


def test_a_category_outside_the_list_is_asked_again_once(fake_openai: FakeOpenAI) -> None:
    fake_openai.script(
        completion(GOOD | {"category": "Bills"}, tokens=(500, 50)),
        completion(GOOD, tokens=(600, 40)),
    )
    p = provider(fake_openai)
    assert p.classify(CTX, RULES).category == "Finance"
    second = chats(fake_openai)[1]
    assert len(second["messages"]) == 4
    assert second["messages"][2]["role"] == "assistant"
    assert '"Bills"' in second["messages"][2]["content"]
    correction = second["messages"][3]["content"]
    assert "'Bills' is not in the list" in correction
    assert "Finance, Saúde, Other" in correction
    assert (p.last_usage.input_tokens, p.last_usage.output_tokens) == (1100, 90)
    assert p.answers == {"retried": 1}


@pytest.mark.parametrize(
    "bad",
    [
        completion("not json at all"),
        completion(""),
        completion(GOOD | {"confidence": "very"}),
        completion(json.dumps(GOOD)[:40], finish_reason="length"),
    ],
)
def test_two_bad_answers_fail_the_file(fake_openai: FakeOpenAI, bad: Reply) -> None:
    fake_openai.script(bad)
    p = provider(fake_openai)
    with pytest.raises(ProviderError, match="invalid answer") as caught:
        p.classify(CTX, RULES)
    assert not caught.value.retryable
    assert len(chats(fake_openai)) == 2
    assert p.answers["retried"] == 1 and p.answers["retry_failed"] == 1


def test_a_cut_off_answer_is_asked_again(fake_openai: FakeOpenAI) -> None:
    fake_openai.script(completion(json.dumps(GOOD)[:40], finish_reason="length"), completion(GOOD))
    assert provider(fake_openai).classify(CTX, RULES).category == "Finance"
    correction = chats(fake_openai)[1]["messages"][3]["content"]
    assert "not one JSON object" in correction


# --- setup checks ----------------------------------------------------------------------


def test_ollama_command_is_found_on_path_or_in_the_usual_folders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _n, path=None: None)
    assert ollama_module.ollama_command() is None
    monkeypatch.setattr(
        shutil,
        "which",
        lambda _n, path=None: "/opt/homebrew/bin/ollama" if path == "/opt/homebrew/bin" else None,
    )
    assert ollama_module.ollama_command() == "/opt/homebrew/bin/ollama"
    monkeypatch.setattr(shutil, "which", lambda _n, path=None: "/usr/bin/ollama")
    assert ollama_module.ollama_command() == "/usr/bin/ollama"
