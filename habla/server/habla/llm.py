"""One small transport for every LLM call, with two backends:

- "ollama" (default): POST /api/chat with format=<schema>, the shared options (same num_ctx on every call, or
  Ollama reloads the model) and keep_alive. Exactly the requests Habla always sent.
- "openai" (HABLA_LLM_URL set): POST {url}/chat/completions, e.g. a local Codex proxy. Structured output via
  response_format json_schema (strict). A server that rejects json_schema gets one retry with json_object and
  the schema in the system message, and that mode is kept for the rest of the process.

Operations: chat_json (one JSON object), stream_json (text chunks of a JSON object as it is written) and
chat_text (free text, used by the simulated learner in scripts/e2e.py).
"""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import AsyncIterator

import httpx

from . import config

log = logging.getLogger("habla.llm")

# Identical on every Ollama call, or Ollama reloads the model. num_predict (max_tokens) caps runaway
# generations: small models sometimes loop inside a JSON string until the context is full (minutes).
OLLAMA_OPTIONS = {"temperature": 0.4, "num_ctx": 8192, "num_predict": 300, "repeat_penalty": 1.1}
KEEP_ALIVE = "60m"


def strictify(schema: dict) -> dict:
    """A copy of a JSON schema that OpenAI strict mode accepts: every object gets additionalProperties: false and
    lists all its properties as required; properties that were optional become nullable."""
    s = copy.deepcopy(schema)

    def walk(node: object) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "object" or "properties" in node:
            props: dict = node.setdefault("properties", {})
            was = set(node.get("required", []))
            for name, p in props.items():
                walk(p)
                if name not in was and isinstance(p, dict):
                    t = p.get("type")
                    if isinstance(t, str):
                        p["type"] = [t, "null"]
                    elif isinstance(t, list) and "null" not in t:
                        p["type"] = [*t, "null"]
                    if isinstance(p.get("enum"), list) and None not in p["enum"]:
                        p["enum"] = [*p["enum"], None]
            node["required"] = list(props)
            node["additionalProperties"] = False
        if "items" in node:
            walk(node["items"])
        for key in ("anyOf", "oneOf", "allOf"):
            for sub in node.get(key, []) or []:
                walk(sub)

    walk(s)
    return s


def _schema_note(schema: dict) -> str:
    return "\n\nReturn only one JSON object that matches this JSON schema:\n" + json.dumps(schema, ensure_ascii=False)


def _with_schema_note(messages: list[dict], schema: dict) -> list[dict]:
    msgs = [dict(m) for m in messages]
    if msgs and msgs[0].get("role") == "system":
        msgs[0]["content"] = str(msgs[0].get("content", "")) + _schema_note(schema)
    else:
        msgs.insert(0, {"role": "system", "content": _schema_note(schema).strip()})
    return msgs


def _rejects_schema(status: int, body: str) -> bool:
    b = body.lower()
    return status == 400 and ("response_format" in b or "json_schema" in b)


def parse_json(text: str) -> dict:
    """The JSON object in a model answer; {} if there is none. Tolerates a ```json fence (json_object mode)."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`").removeprefix("json").strip()
    try:
        j = json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("{"), t.rfind("}")
        if a < 0 or b <= a:
            return {}
        try:
            j = json.loads(t[a : b + 1])
        except json.JSONDecodeError:
            return {}
    return j if isinstance(j, dict) else {}


class LLM:
    def __init__(self, backend: str, model: str, *, url: str = "", key: str = "", reasoning: str = "") -> None:
        self.backend = backend
        self.model = model
        self.reasoning = reasoning
        self.json_mode = "json_schema"  # becomes "json_object" if the server rejects json_schema
        if backend == "openai":
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            self.client = httpx.AsyncClient(base_url=url, headers=headers, timeout=httpx.Timeout(120, connect=5))
        else:
            self.client = httpx.AsyncClient(base_url=url or config.OLLAMA_URL, timeout=httpx.Timeout(120, connect=5))

    @classmethod
    def from_config(cls, ollama_model: str = "") -> LLM:
        if config.LLM_BACKEND == "openai":
            return cls("openai", config.LLM_MODEL, url=config.LLM_URL, key=config.LLM_KEY,
                       reasoning=config.LLM_REASONING)
        return cls("ollama", ollama_model)

    @property
    def label(self) -> str:
        return f"{self.backend}:{self.model}"

    async def aclose(self) -> None:
        await self.client.aclose()

    # ---------- request bodies ----------
    def _ollama_body(self, messages: list[dict], schema: dict | None, max_tokens: int, stream: bool,
                     options: dict | None = None) -> dict:
        body: dict = {"model": self.model, "messages": messages, "stream": stream, "think": False}
        if schema is not None:
            body["format"] = schema
        body["keep_alive"] = KEEP_ALIVE
        opts = dict(OLLAMA_OPTIONS) if options is None else dict(options)
        if max_tokens:
            opts["num_predict"] = max_tokens
        body["options"] = opts
        return body

    def _openai_body(self, messages: list[dict], schema: dict | None, max_tokens: int, stream: bool,
                     name: str) -> dict:
        body: dict = {"model": self.model, "stream": stream}
        if max_tokens:
            body["max_tokens"] = max_tokens
        if self.reasoning:
            body["reasoning_effort"] = self.reasoning
        if schema is None:
            body["messages"] = messages
        elif self.json_mode == "json_schema":
            body["messages"] = messages
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": name, "strict": True, "schema": strictify(schema)}}
        else:
            body["messages"] = _with_schema_note(messages, schema)
            body["response_format"] = {"type": "json_object"}
        return body

    def _fall_back(self, status: int, text: str, schema: dict | None) -> bool:
        """True (and json_object mode from now on) if the server rejected json_schema and we haven't retried."""
        if schema is None or self.json_mode != "json_schema" or not _rejects_schema(status, text):
            return False
        log.warning("LLM server rejected json_schema (%s); using json_object from now on", text[:200])
        self.json_mode = "json_object"
        return True

    # ---------- operations ----------
    async def _complete(self, messages: list[dict], schema: dict | None, max_tokens: int, timeout: float | None,
                        name: str, options: dict | None = None) -> str:
        if self.backend != "openai":
            r = await self.client.post("/api/chat", json=self._ollama_body(messages, schema, max_tokens, False, options),
                                       timeout=timeout)
            try:  # as before: an Ollama error answer counts as unusable output, not an exception
                j = r.json()
                return str(j["message"]["content"])
            except (json.JSONDecodeError, KeyError, TypeError):
                log.warning("unusable Ollama answer (%d): %s", r.status_code, r.text[:200])
                return ""
        for _ in range(2):
            r = await self.client.post("/chat/completions",
                                       json=self._openai_body(messages, schema, max_tokens, False, name),
                                       timeout=timeout)
            if r.status_code >= 400 and self._fall_back(r.status_code, r.text, schema):
                continue
            r.raise_for_status()
            try:
                return str(r.json()["choices"][0]["message"].get("content") or "")
            except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError):
                return ""
        raise RuntimeError("LLM server rejected both json_schema and json_object")

    async def chat_json(self, messages: list[dict], schema: dict, max_tokens: int = 300,
                        timeout: float | None = None, name: str = "reply") -> dict:
        """One JSON object. {} if the model's output is unusable; HTTP/connection errors raise."""
        return parse_json(await self._complete(messages, schema, max_tokens, timeout, name))

    async def chat_text(self, messages: list[dict], max_tokens: int = 0, timeout: float | None = None,
                        temperature: float = 0.9) -> str:
        """Free text (no schema). Ollama gets {temperature, num_ctx} only, as the e2e learner always did."""
        return (await self._complete(messages, None, max_tokens, timeout, "text",
                                     options={"temperature": temperature, "num_ctx": OLLAMA_OPTIONS["num_ctx"]})).strip()

    async def stream_json(self, messages: list[dict], schema: dict, max_tokens: int = 300,
                          name: str = "reply") -> AsyncIterator[str]:
        """Text chunks of one JSON object as the model writes it. The caller enforces its own deadline."""
        if self.backend != "openai":
            async with self.client.stream("POST", "/api/chat",
                                          json=self._ollama_body(messages, schema, max_tokens, True)) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    j = json.loads(line)
                    if j.get("error"):
                        raise RuntimeError(j["error"])
                    c = j.get("message", {}).get("content", "")
                    if c:
                        yield c
            return
        for _ in range(2):
            retry = False
            async with self.client.stream("POST", "/chat/completions",
                                          json=self._openai_body(messages, schema, max_tokens, True, name)) as r:
                if r.status_code >= 400:
                    text = (await r.aread()).decode("utf-8", "replace")
                    if self._fall_back(r.status_code, text, schema):
                        retry = True
                    else:
                        r.raise_for_status()
                else:
                    async for line in r.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            return
                        try:
                            j = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if j.get("error"):
                            raise RuntimeError(str(j["error"]))
                        choices = j.get("choices") or []
                        c = (choices[0].get("delta") or {}).get("content") if choices else None
                        if c:
                            yield c
                    return
            if not retry:
                return
        raise RuntimeError("LLM server rejected both json_schema and json_object")

    async def warmup(self) -> None:
        if self.backend == "openai":
            return  # nothing to load; the first real request opens the connection
        await self.client.post("/api/chat", json={
            "model": self.model, "messages": [{"role": "user", "content": "Hola"}], "stream": False,
            "think": False, "keep_alive": KEEP_ALIVE, "options": {**OLLAMA_OPTIONS, "num_predict": 1}})
