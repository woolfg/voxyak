from __future__ import annotations

from voxyak.meeting_metadata import collect_meeting_metadata


def test_participant_prompt_asks_for_names() -> None:
    class FakeConsole:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        @staticmethod
        def print(*args, **kwargs) -> None:
            pass

        def input(self, prompt: str) -> str:
            self.prompts.append(prompt)
            return ""

    console = FakeConsole()

    assert collect_meeting_metadata(console) == {}
    assert "Speakers / participant names: " in console.prompts
    assert "Speakers / participants: " not in console.prompts
