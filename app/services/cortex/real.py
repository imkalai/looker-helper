"""Adapter for the corporate Cortex LLM proxy.

TODO: plug in the Cortex client code here once it's shared. Only `chat()` needs implementing:
it receives OpenAI-style messages and must return the model's text reply.
Configuration (endpoint, key, model, timeout, headers, proxy, CA bundle) comes from
Settings > AI assistant, stored in the app_settings table under the key "cortex".
"""

from app.services.cortex.base import CortexClient, CortexError


class RealCortex(CortexClient):
    name = "cortex"

    def __init__(self, config: dict):
        self.endpoint = config.get("endpoint", "")
        self.auth_method = config.get("auth_method", "api_key")
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "default")
        self.timeout = int(config.get("timeout", 60))
        self.retries = int(config.get("retries", 2))
        self.extra_headers = config.get("headers", {})
        self.proxy = config.get("proxy") or None
        self.ca_bundle = config.get("ca_bundle") or None

    def chat(self, messages: list[dict], *, temperature: float = 0.0, max_tokens: int = 4000) -> str:
        if not self.endpoint:
            raise CortexError("Cortex isn't set up yet. An administrator can add the endpoint in Settings › AI assistant.")
        # Replace this with the corporate Cortex client call, e.g.:
        #   client = CortexClient(base_url=self.endpoint, api_key=self.api_key, timeout=self.timeout, ...)
        #   return client.chat(model=self.model, messages=messages, temperature=temperature, max_tokens=max_tokens).text
        raise CortexError("The Cortex client hasn't been connected yet. Set LH_CORTEX_MODE=mock until it's added in app/services/cortex/real.py.")
