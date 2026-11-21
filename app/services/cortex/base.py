from abc import ABC, abstractmethod


class CortexError(Exception):
    """Cortex couldn't answer. The message can be shown to the user."""


class CortexClient(ABC):
    """Chat-style LLM access. Messages are dicts with "role" (system | user | assistant) and "content"."""

    name: str = "cortex"

    @abstractmethod
    def chat(self, messages: list[dict], *, temperature: float = 0.0, max_tokens: int = 4000) -> str: ...

    def health(self) -> tuple[bool, str]:
        try:
            self.chat([{"role": "system", "content": "TASK: ping"}, {"role": "user", "content": "Reply with OK."}], max_tokens=5)
            return True, "Connected"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
