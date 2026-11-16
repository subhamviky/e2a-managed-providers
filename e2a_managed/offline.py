"""Offline wire replay: run the REAL provider SDKs with no account and no network.

No production access exists, so every managed adapter is exercised like this:

    adapter (our code) -> provider SDK (real: request building, auth signing, parsing)
        -> MockTransport (here): decodes the provider's wire request back to the
           canonical conversation, asks a responder, encodes the provider's wire response

The responder is any callable(messages) -> str | {"text", "tool_calls"}, or a project's
existing SimulatedLLMService. That is how the three projects' deterministic rules drive
a full Anthropic / OpenAI / Gemini / OGX round trip in the example runners: the
rules supply the content; everything between the agent and the HTTP boundary is the
production code path. Recorded requests (`ReplayTransport.requests`) let tests assert
on what would have been sent.

Wire shapes follow each provider's public API reference. They are fixtures, not a
contract: if a provider changes its wire format, the SDK upgrade plus these encoders
are what to update (pin SDK versions; see docs/TESTING.md).
"""
import asyncio
import json
import uuid
from typing import Any, Callable, Dict, List, Optional, Union

import httpx

from . import codec

Responder = Callable[[List[Dict[str, Any]]], Union[str, Dict[str, Any]]]


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    out = []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        if isinstance(block.get("text"), str):
            out.append(block["text"])
        elif isinstance(block.get("content"), (str, list)):
            out.append(_text(block["content"]))
    return "".join(out)


def simulated(sim_llm: Any) -> Responder:
    """Use an E2A SimulatedLLMService's rules as the responder, through its public
    process_inference() (its own sanitising, quota and accounting run as usual)."""
    def respond(messages):
        coro = sim_llm.process_inference(messages, tenant_id="replay")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            out = asyncio.run(coro)
        else:                    # inside a running loop (the E2A bridge loop): run on a private loop
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(1) as pool:
                out = pool.submit(asyncio.run, coro).result()
        if out.get("status") == "FAILED":
            raise RuntimeError(out.get("error"))
        return {"text": out.get("text", ""), "tool_calls": out.get("tool_calls") or []}
    return respond


# ------------------------------------------------------------------ request decoders
def decode_anthropic(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    msgs: List[Dict[str, Any]] = []
    system = body.get("system")
    if system:
        msgs.append({"role": "system", "content": _text(system) if not isinstance(system, str) else system})
    for m in body.get("messages", []):
        content = m.get("content")
        if isinstance(content, list) and any(b.get("type") == "tool_result" for b in content):
            for b in content:
                msgs.append({"role": "tool", "tool_call_id": b.get("tool_use_id"), "content": _text(b.get("content"))})
        elif isinstance(content, list) and m["role"] == "assistant":
            calls = [{"id": b["id"], "name": b["name"], "arguments": b.get("input", {})}
                     for b in content if b.get("type") == "tool_use"]
            msgs.append({"role": "assistant", "content": _text([b for b in content if b.get("type") == "text"]),
                         **({"tool_calls": calls} if calls else {})})
        else:
            msgs.append({"role": m["role"], "content": _text(content)})
    return msgs


def decode_openai_responses(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    msgs: List[Dict[str, Any]] = [{"role": "system", "content": body["instructions"]}] if body.get("instructions") else []
    items = body.get("input")
    if isinstance(items, str):
        return msgs + [{"role": "user", "content": items}]
    for it in items or []:
        if it.get("type") == "function_call_output":
            msgs.append({"role": "tool", "tool_call_id": it["call_id"], "content": it.get("output", "")})
        elif it.get("type") == "function_call":
            call = {"id": it["call_id"], "name": it["name"], "arguments": json.loads(it.get("arguments") or "{}")}
            if msgs and msgs[-1]["role"] == "assistant":
                msgs[-1].setdefault("tool_calls", []).append(call)
            else:
                msgs.append({"role": "assistant", "content": "", "tool_calls": [call]})
        else:
            msgs.append({"role": it.get("role", "user"), "content": _text(it.get("content"))})
    return msgs


def decode_openai_chat(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    msgs = []
    for m in body.get("messages", []):
        if m["role"] == "tool":
            msgs.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": _text(m.get("content"))})
        elif m.get("tool_calls"):
            msgs.append({"role": "assistant", "content": _text(m.get("content")), "tool_calls": [
                {"id": t["id"], "name": t["function"]["name"], "arguments": json.loads(t["function"]["arguments"])}
                for t in m["tool_calls"]]})
        else:
            msgs.append({"role": m["role"], "content": _text(m.get("content"))})
    return msgs


def decode_gemini(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    msgs: List[Dict[str, Any]] = []
    sys_inst = body.get("systemInstruction") or body.get("system_instruction")
    if sys_inst:
        msgs.append({"role": "system", "content": "".join(p.get("text", "") for p in sys_inst.get("parts", []))})
    for c in body.get("contents", []):
        parts = c.get("parts", [])
        responses = [p.get("functionResponse") or p.get("function_response") for p in parts]
        if any(responses):
            for r in filter(None, responses):
                resp = r.get("response")
                if isinstance(resp, dict) and set(resp) == {"result"}:
                    resp = resp["result"]                          # the codec's {"result": ...} envelope
                msgs.append({"role": "tool", "tool_call_id": r["name"],
                             "content": resp if isinstance(resp, str) else json.dumps(resp)})
            continue
        calls = [p.get("functionCall") or p.get("function_call") for p in parts]
        text = "".join(p.get("text", "") for p in parts)
        role = "assistant" if c.get("role") == "model" else "user"
        msg = {"role": role, "content": text}
        if any(calls):
            msg["tool_calls"] = [{"id": f["name"], "name": f["name"], "arguments": f.get("args", {})}
                                 for f in filter(None, calls)]
        msgs.append(msg)
    return msgs


# ------------------------------------------------------------------ response encoders
def _answer(responder: Responder, messages) -> Dict[str, Any]:
    """The responder's answer: text, or a dict with text / tool_calls / stop_reason, or
    {"error_status": 429, "retry_after": 1} to answer the HTTP request with that status (the
    shape E2A's port contract kit, check_llm_service, uses)."""
    out = responder(messages)
    if isinstance(out, str):
        return {"text": out, "tool_calls": []}
    if out.get("error_status"):
        return {"error_status": int(out["error_status"]), "retry_after": out.get("retry_after")}
    return {"text": out.get("text", ""), "tool_calls": out.get("tool_calls") or [], "stop_reason": out.get("stop_reason")}


_ERROR_STATUS = {400: "INVALID_ARGUMENT", 401: "UNAUTHENTICATED", 403: "PERMISSION_DENIED", 404: "NOT_FOUND",
                 429: "RESOURCE_EXHAUSTED", 500: "INTERNAL", 503: "UNAVAILABLE", 529: "UNAVAILABLE"}
_ERROR_TYPE = {400: "invalid_request_error", 401: "authentication_error", 403: "permission_error",
               404: "not_found_error", 429: "rate_limit_error", 500: "api_error", 503: "overloaded_error",
               529: "overloaded_error"}


def error_response(answer: Dict[str, Any]) -> tuple:
    """An HTTP error every provider SDK understands: Anthropic and OpenAI read error.type and
    error.message, Google reads error.code and error.status; all honour Retry-After."""
    status = answer["error_status"]
    headers = {"content-type": "application/json", "x-should-retry": "true"}
    if answer.get("retry_after") is not None:
        headers["retry-after"] = str(answer["retry_after"])
    body = {"type": "error", "error": {"type": _ERROR_TYPE.get(status, "api_error"), "code": status,
                                       "status": _ERROR_STATUS.get(status, "UNKNOWN"),
                                       "message": f"replayed HTTP {status}"}}
    return status, headers, json.dumps(body).encode()


def _tokens(messages, text) -> tuple:
    return sum(len(str(m.get("content", ""))) for m in messages) // 4 + 1, len(text) // 4 + 1


def encode_anthropic(body, answer, messages) -> Dict[str, Any]:
    inp, out = _tokens(messages, answer["text"])
    content = ([{"type": "text", "text": answer["text"]}] if answer["text"] else []) + [
        {"type": "tool_use", "id": c.get("id") or f"toolu_{uuid.uuid4().hex[:12]}", "name": c["name"],
         "input": c.get("arguments", {})} for c in answer["tool_calls"]]
    return {"id": f"msg_{uuid.uuid4().hex[:16]}", "type": "message", "role": "assistant", "model": body.get("model"),
            "content": content, "stop_reason": answer.get("stop_reason") or
            ("tool_use" if answer["tool_calls"] else "end_turn"), "stop_sequence": None,
            "usage": {"input_tokens": inp, "output_tokens": out}}


def encode_openai_responses(body, answer, messages) -> Dict[str, Any]:
    inp, out = _tokens(messages, answer["text"])
    output: List[Dict[str, Any]] = []
    if answer.get("stop_reason") == "refusal":
        output.append({"type": "message", "id": f"msg_{uuid.uuid4().hex[:8]}", "role": "assistant", "status": "completed",
                       "content": [{"type": "refusal", "refusal": answer["text"]}]})
    elif answer["text"]:
        output.append({"type": "message", "id": f"msg_{uuid.uuid4().hex[:8]}", "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text", "text": answer["text"], "annotations": []}]})
    for c in answer["tool_calls"]:
        output.append({"type": "function_call", "id": f"fc_{uuid.uuid4().hex[:8]}",
                       "call_id": c.get("id") or f"call_{uuid.uuid4().hex[:8]}", "name": c["name"],
                       "arguments": json.dumps(c.get("arguments", {})), "status": "completed"})
    return {"id": f"resp_{uuid.uuid4().hex[:16]}", "object": "response", "created_at": 0, "model": body.get("model"),
            "status": "incomplete" if answer.get("stop_reason") == "max_tokens" else "completed",
            "incomplete_details": {"reason": "max_output_tokens"} if answer.get("stop_reason") == "max_tokens" else None,
            "output": output, "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
            "usage": {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out,
                      "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}


def encode_openai_chat(body, answer, messages) -> Dict[str, Any]:
    inp, out = _tokens(messages, answer["text"])
    calls = [{"id": c.get("id") or f"call_{uuid.uuid4().hex[:8]}", "type": "function",
              "function": {"name": c["name"], "arguments": json.dumps(c.get("arguments", {}))}}
             for c in answer["tool_calls"]]
    refused = answer.get("stop_reason") == "refusal"
    msg: Dict[str, Any] = {"role": "assistant", "content": None if refused else (answer["text"] or None)}
    if refused:
        msg["refusal"] = answer["text"] or "refused"
    if calls:
        msg["tool_calls"] = calls
    finish = ("tool_calls" if calls else "content_filter" if refused else
              "length" if answer.get("stop_reason") == "max_tokens" else "stop")
    return {"id": f"chatcmpl-{uuid.uuid4().hex[:12]}", "object": "chat.completion", "created": 0,
            "model": body.get("model"), "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": inp + out}}


def encode_gemini(body, answer, messages) -> Dict[str, Any]:
    inp, out = _tokens(messages, answer["text"])
    parts = ([{"text": answer["text"]}] if answer["text"] else []) + [
        {"functionCall": {"name": c["name"], "args": c.get("arguments", {})}} for c in answer["tool_calls"]]
    finish = {"refusal": "SAFETY", "max_tokens": "MAX_TOKENS"}.get(answer.get("stop_reason"), "STOP")
    return {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish, "index": 0}],
            "usageMetadata": {"promptTokenCount": inp, "candidatesTokenCount": out, "totalTokenCount": inp + out},
            "modelVersion": "replay", "responseId": uuid.uuid4().hex[:12]}


# ------------------------------------------------------------------ SSE (streaming) encoders
def _sse(events: List[tuple]) -> bytes:
    return "".join((f"event: {e}\n" if e else "") + f"data: {json.dumps(d) if not isinstance(d, str) else d}\n\n"
                   for e, d in events).encode()


def sse_anthropic(body, answer, messages) -> bytes:
    msg = encode_anthropic(body, {**answer, "tool_calls": []}, messages)
    start = dict(msg, content=[], stop_reason=None, usage={"input_tokens": msg["usage"]["input_tokens"], "output_tokens": 0})
    chunks = [answer["text"][i:i + 16] for i in range(0, len(answer["text"]), 16)] or [""]
    return _sse([("message_start", {"type": "message_start", "message": start}),
                 ("content_block_start", {"type": "content_block_start", "index": 0,
                                          "content_block": {"type": "text", "text": ""}})]
                + [("content_block_delta", {"type": "content_block_delta", "index": 0,
                                            "delta": {"type": "text_delta", "text": c}}) for c in chunks]
                + [("content_block_stop", {"type": "content_block_stop", "index": 0}),
                   ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                      "usage": {"output_tokens": msg["usage"]["output_tokens"]}}),
                   ("message_stop", {"type": "message_stop"})])


def sse_openai_responses(body, answer, messages) -> bytes:
    final = encode_openai_responses(body, {**answer, "tool_calls": []}, messages)
    chunks = [answer["text"][i:i + 16] for i in range(0, len(answer["text"]), 16)]
    events = [("response.created", {"type": "response.created", "sequence_number": 0,
                                    "response": dict(final, status="in_progress", output=[])})]
    events += [("response.output_text.delta", {"type": "response.output_text.delta", "sequence_number": i + 1,
                                               "item_id": "msg", "output_index": 0, "content_index": 0, "delta": c,
                                               "logprobs": []}) for i, c in enumerate(chunks)]
    events.append(("response.completed", {"type": "response.completed", "sequence_number": len(chunks) + 1,
                                          "response": final}))
    return _sse(events)


def sse_openai_chat(body, answer, messages) -> bytes:
    cid = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    chunks = [answer["text"][i:i + 16] for i in range(0, len(answer["text"]), 16)]
    events = [(None, {"id": cid, "object": "chat.completion.chunk", "created": 0, "model": body.get("model"),
                      "choices": [{"index": 0, "delta": {"content": c}, "finish_reason": None}]}) for c in chunks]
    events.append((None, {"id": cid, "object": "chat.completion.chunk", "created": 0, "model": body.get("model"),
                          "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}))
    events.append((None, "[DONE]"))
    return _sse(events)


def sse_gemini(body, answer, messages) -> bytes:
    chunks = [answer["text"][i:i + 16] for i in range(0, len(answer["text"]), 16)] or [""]
    return _sse([(None, {"candidates": [{"content": {"role": "model", "parts": [{"text": c}]}, "index": 0}]})
                 for c in chunks] + [(None, encode_gemini(body, {**answer, "text": "", "tool_calls": []}, messages))])


WIRE = {
    "anthropic": (decode_anthropic, encode_anthropic, sse_anthropic),
    "openai_responses": (decode_openai_responses, encode_openai_responses, sse_openai_responses),
    "openai_chat": (decode_openai_chat, encode_openai_chat, sse_openai_chat),
    "gemini": (decode_gemini, encode_gemini, sse_gemini),
}


class ReplayTransport:
    """Builds an httpx / httpx2 MockTransport answering one provider's model endpoint.
    Non-model routes (embeddings, search, guardrails...) can be added with `route()`."""

    def __init__(self, wire: str, responder: Responder) -> None:
        self.decode, self.encode, self.encode_sse = WIRE[wire]
        self.responder = responder
        self.requests: List[Dict[str, Any]] = []
        self.routes: List[tuple] = []

    def route(self, path_contains: str, handler: Callable[[Dict[str, Any]], Dict[str, Any]]) -> "ReplayTransport":
        self.routes.append((path_contains, handler))
        return self

    def _handle(self, method: str, url: str, headers: Dict[str, str], raw: bytes):
        body = json.loads(raw or b"{}") if raw else {}
        self.requests.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        for needle, handler in self.routes:
            if needle in url:
                return 200, {"content-type": "application/json"}, json.dumps(handler(body)).encode()
        messages = self.decode(body)
        answer = _answer(self.responder, messages)
        if answer.get("error_status"):
            return error_response(answer)
        streaming = body.get("stream") is True or "stream" in url.rsplit("/", 1)[-1].lower() or "alt=sse" in url
        if streaming:
            return 200, {"content-type": "text/event-stream"}, self.encode_sse(body, answer, messages)
        return 200, {"content-type": "application/json", "request-id": f"req_{uuid.uuid4().hex[:12]}"}, \
            json.dumps(self.encode(body, answer, messages)).encode()

    def httpx(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            status, headers, content = self._handle(request.method, str(request.url), dict(request.headers),
                                                    request.read())
            return httpx.Response(status, headers=headers, content=content)
        return httpx.MockTransport(handler)

    def httpx2(self):
        import httpx2

        def handler(request):
            status, headers, content = self._handle(request.method, str(request.url), dict(request.headers),
                                                    request.read())
            return httpx2.Response(status, headers=headers, content=content)
        return httpx2.MockTransport(handler)


# ------------------------------------------------------------------ offline clients per provider
WIRE_OF_PROVIDER = {"anthropic": "anthropic", "openai": "openai_responses", "google": "gemini", "ogx": "openai_chat"}


def offline_client(provider: str, transport: ReplayTransport, config: Optional[Dict[str, Any]] = None):
    """The provider's REAL async SDK client, wired to the replay transport. Credentials are
    syntactically valid fakes, so signing and auth code paths still run."""
    config = config or {}
    if provider == "anthropic":
        import httpx2
        from anthropic import AsyncAnthropicAWS, AsyncAnthropicBedrockMantle
        common = dict(aws_region=config.get("aws_region", "us-east-1"), aws_access_key="AKIAOFFLINEREPLAY000",
                      aws_secret_key="offline-replay-secret", max_retries=0,
                      http_client=httpx2.AsyncClient(transport=transport.httpx2()))
        if config.get("platform") == "claude_platform_aws":
            return AsyncAnthropicAWS(workspace_id="wrkspc_offline", **common)
        return AsyncAnthropicBedrockMantle(**common)
    if provider == "openai":
        import httpx2
        from openai import AsyncOpenAI
        return AsyncOpenAI(base_url="https://offline-replay.openai.azure.com/openai/v1", api_key="offline-key",
                           max_retries=0, http_client=httpx2.AsyncClient(transport=transport.httpx2()))
    if provider == "google":
        from google import genai
        from google.genai import types
        from google.oauth2.credentials import Credentials
        return genai.Client(vertexai=True, project="offline-replay", location="us-central1",
                            credentials=Credentials(token="offline-replay-token"),
                            http_options=types.HttpOptions(httpx_async_client=httpx.AsyncClient(transport=transport.httpx()),
                                                           httpx_client=httpx.Client(transport=transport.httpx())))
    if provider == "ogx":
        import httpx2
        from openai import AsyncOpenAI
        return AsyncOpenAI(base_url="http://ogx.offline:8321/v1", api_key="unused", max_retries=0,
                           http_client=httpx2.AsyncClient(transport=transport.httpx2()))
    raise ValueError(provider)


def offline_llm_service(profile: str, responder: Responder, **config: Any):
    """(llm_service, transport): the profile's bound model adapter, offline.
    Example: offline_llm_service("openai_azure", simulated(build_o2c_llm()))."""
    from .bindings import bindings_from_profile
    b = bindings_from_profile(profile, overrides=config)
    wire = WIRE_OF_PROVIDER[b.profile.provider]
    transport = ReplayTransport(wire, responder)
    platform = getattr(_import_cls(b.profile.bindings["llm_service"]), "platform", "")
    client = offline_client(b.profile.provider, transport, {**b.profile.config, **config, "platform": platform})
    cls = _import_cls(b.profile.bindings["llm_service"])
    return cls({**b.profile.config, **config}, client=client), transport


def _import_cls(target: str):
    import importlib
    module, _, attr = target.partition(":")
    return getattr(importlib.import_module(module), attr)
