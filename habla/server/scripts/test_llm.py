"""Unit checks for habla/llm.py with a mocked HTTP transport (no server, no LLM): request bodies for both
backends, strictify, SSE streaming and the json_schema -> json_object fallback.

  uv run python scripts/test_llm.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["HABLA_DB"] = os.path.join(tempfile.mkdtemp(prefix="habla-test-llm-"), "scratch.db")
os.environ["HABLA_NO_ENV"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx  # noqa: E402

from habla.llm import LLM, OLLAMA_OPTIONS, parse_json, strictify  # noqa: E402
from habla.tutor import CHAT_SCHEMA, CHECK_SCHEMA  # noqa: E402

SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string", "enum": ["x", "y"]},
                                           "c": {"type": "array", "items": {"type": "object",
                                                                            "properties": {"d": {"type": "integer"}}}}},
          "required": ["a"]}


def test_strictify() -> None:
    s = strictify(SCHEMA)
    assert s["additionalProperties"] is False and s["required"] == ["a", "b", "c"]
    assert s["properties"]["a"]["type"] == "string"
    assert s["properties"]["b"]["type"] == ["string", "null"] and None in s["properties"]["b"]["enum"]
    item = s["properties"]["c"]["items"]
    assert item["additionalProperties"] is False and item["required"] == ["d"]
    assert item["properties"]["d"]["type"] == ["integer", "null"]
    assert "additionalProperties" not in SCHEMA, "strictify must not change its input"
    c = strictify(CHECK_SCHEMA)
    assert c["properties"]["explanations"]["items"]["additionalProperties"] is False
    assert c["properties"]["explanations"]["items"]["properties"]["type"]["type"] == "string"  # required: not nullable


def test_parse_json() -> None:
    assert parse_json('{"a": "1"}') == {"a": "1"}
    assert parse_json('```json\n{"a": "1"}\n```') == {"a": "1"}
    assert parse_json('Sure: {"a": "1"} ok') == {"a": "1"}
    assert parse_json("nope") == {} and parse_json("[1]") == {}


def mock(llm: LLM, handler) -> list[dict]:
    seen: list[dict] = []

    def wrapped(req: httpx.Request) -> httpx.Response:
        seen.append({"path": req.url.path, "body": json.loads(req.content), "auth": req.headers.get("authorization")})
        return handler(req, len(seen))

    llm.client = httpx.AsyncClient(base_url=llm.client.base_url, headers=llm.client.headers,
                                   transport=httpx.MockTransport(wrapped))
    return seen


async def test_ollama() -> None:
    llm = LLM("ollama", "gemma4:e4b", url="http://ollama.test")
    seen = mock(llm, lambda req, n: httpx.Response(200, json={"message": {"content": '{"reply_en": "Hi"}'}}))
    assert await llm.chat_json([{"role": "user", "content": "Hola"}], SCHEMA, 300) == {"reply_en": "Hi"}
    b = seen[0]["body"]
    assert seen[0]["path"] == "/api/chat"
    assert b["format"] == SCHEMA and b["keep_alive"] == "60m" and b["think"] is False and b["stream"] is False
    assert b["options"] == OLLAMA_OPTIONS
    await llm.chat_text([{"role": "user", "content": "x"}])
    assert seen[1]["body"]["options"] == {"temperature": 0.9, "num_ctx": 8192} and "format" not in seen[1]["body"]

    lines = "\n".join(json.dumps({"message": {"content": c}}) for c in ['{"re', 'ply": "Hola"}']) + "\n"
    llm2 = LLM("ollama", "gemma4:e4b", url="http://ollama.test")
    mock(llm2, lambda req, n: httpx.Response(200, text=lines))
    assert "".join([c async for c in llm2.stream_json([], CHAT_SCHEMA)]) == '{"reply": "Hola"}'
    err = LLM("ollama", "m", url="http://ollama.test")
    mock(err, lambda req, n: httpx.Response(404, json={"error": "model not found"}))
    assert await err.chat_json([], SCHEMA) == {}


def sse(chunks: list[str]) -> str:
    out = [": keep-alive", ""]
    for c in chunks:
        out += ["data: " + json.dumps({"choices": [{"delta": {"content": c}}]}), ""]
    out += ["data: " + json.dumps({"choices": []}), "", "data: [DONE]", ""]
    return "\n".join(out)


async def test_openai() -> None:
    llm = LLM("openai", "gpt-6.1-terra", url="http://proxy.test/v1", key="k", reasoning="none")
    seen = mock(llm, lambda req, n: httpx.Response(200, json={"choices": [{"message": {"content": '{"a": "1"}'}}]}))
    assert await llm.chat_json([{"role": "system", "content": "S"}], SCHEMA, 120, name="check") == {"a": "1"}
    s = seen[0]
    assert s["path"] == "/v1/chat/completions" and s["auth"] == "Bearer k"
    b = s["body"]
    assert b["model"] == "gpt-6.1-terra" and b["max_tokens"] == 120 and b["reasoning_effort"] == "none"
    assert b["response_format"]["type"] == "json_schema"
    assert b["response_format"]["json_schema"]["strict"] is True and b["response_format"]["json_schema"]["name"] == "check"
    assert b["response_format"]["json_schema"]["schema"] == strictify(SCHEMA)
    assert "options" not in b and "format" not in b

    noreason = LLM("openai", "gpt-6-luna", url="http://proxy.test/v1", key="k", reasoning="")
    seen2 = mock(noreason, lambda req, n: httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]}))
    await noreason.chat_json([], SCHEMA)
    assert "reasoning_effort" not in seen2[0]["body"]

    st = LLM("openai", "gpt-6.1-terra", url="http://proxy.test/v1", key="k", reasoning="none")
    seen3 = mock(st, lambda req, n: httpx.Response(200, text=sse(['{"reply"', ': "Hola"}'])))
    assert "".join([c async for c in st.stream_json([], CHAT_SCHEMA)]) == '{"reply": "Hola"}'
    assert seen3[0]["body"]["stream"] is True


async def test_fallback() -> None:
    def handler(req: httpx.Request, n: int) -> httpx.Response:
        if json.loads(req.content)["response_format"]["type"] == "json_schema":
            return httpx.Response(400, json={"error": {"message": "response_format json_schema is not supported"}})
        return httpx.Response(200, text=sse(['{"reply": "Hola"}']))

    llm = LLM("openai", "m", url="http://proxy.test/v1", key="k", reasoning="none")
    seen = mock(llm, handler)
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    assert "".join([c async for c in llm.stream_json(msgs, CHAT_SCHEMA)]) == '{"reply": "Hola"}'
    assert len(seen) == 2 and llm.json_mode == "json_object"
    b = seen[1]["body"]
    assert b["response_format"] == {"type": "json_object"}
    assert b["messages"][0]["content"].startswith("S\n\n") and '"learner_en"' in b["messages"][0]["content"]
    assert msgs[0]["content"] == "S", "the caller's messages must not change"
    assert "".join([c async for c in llm.stream_json(msgs, CHAT_SCHEMA)]) == '{"reply": "Hola"}'
    assert len(seen) == 3, "json_object is remembered: no second json_schema attempt"

    other = LLM("openai", "m", url="http://proxy.test/v1", key="k")
    mock(other, lambda req, n: httpx.Response(400, json={"error": {"message": "bad model"}}))
    try:
        await other.chat_json([], SCHEMA)
        raise AssertionError("an unrelated 400 must raise")
    except httpx.HTTPStatusError:
        pass
    assert other.json_mode == "json_schema"


async def main() -> None:
    test_strictify()
    test_parse_json()
    await test_ollama()
    await test_openai()
    await test_fallback()
    print("test_llm: all passed")


if __name__ == "__main__":
    asyncio.run(main())
