from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.api.llm import SYSTEM_PROMPT, LLMServiceError, OpenAIAnswerService


def test_openai_answer_service_builds_messages_with_required_sections() -> None:
    messages = OpenAIAnswerService._build_messages(
        query="federated learning",
        context_blocks=["[1] Paper A (paper:a): Relevant evidence."],
    )

    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == SYSTEM_PROMPT
    assert "1. Concise answer" in messages[1]["content"]
    assert "2. Evidence bullets" in messages[1]["content"]
    assert "3. Citations" in messages[1]["content"]
    assert "4. Suggested follow-up questions" in messages[1]["content"]


def test_openai_answer_service_retries_on_timeout_and_then_succeeds(monkeypatch) -> None:
    class FlakyCompletions:
        def __init__(self) -> None:
            self.calls = 0

        def create(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("simulated timeout")
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                "1. Concise answer\n"
                                "Done\n\n"
                                "2. Evidence bullets\n"
                                "- [1]\n\n"
                                "3. Citations\n"
                                "- [1]\n\n"
                                "4. Suggested follow-up questions\n"
                                "- next"
                            )
                        )
                    )
                ]
            )

    completions = FlakyCompletions()
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    service = OpenAIAnswerService(
        api_key="test-key",
        model="gpt-4o-mini",
        max_retries=2,
        backoff_seconds=0.0,
        stream=False,
    )

    monkeypatch.setattr(service, "_build_client", lambda: fake_client)
    monkeypatch.setattr("apps.api.llm.time.sleep", lambda _seconds: None)

    answer = service.generate_answer(
        query="What matters?",
        context_blocks=["[1] Context block"],
    )

    assert "1. Concise answer" in answer
    assert completions.calls == 2


def test_openai_answer_service_requires_api_key() -> None:
    with pytest.raises(LLMServiceError) as exc:
        OpenAIAnswerService(api_key="", model="gpt-4o-mini")

    assert exc.value.details.code == "missing_api_key"
