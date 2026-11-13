"""Canonical E2A conversation <-> provider wire formats.

E2A agents speak one vendor-neutral shape; each provider SDK speaks its own. All
translation lives here, as plain dict functions (every SDK accepts dicts and returns
pydantic objects, which `as_dict` flattens), so adapters stay thin and the four
mappings can be read side by side.

Canonical conversation
    {"role": "system" | "user" | "assistant" | "tool", "content": str,
     "tool_calls": [{"id", "name", "arguments": dict}],      # assistant only
     "tool_call_id": str}                                     # tool only
Canonical tool declaration (BaseLLMAgentService._compile_tool_schemas)
    {"name", "description", "parameters": <JSON schema>}
Canonical result (what _dispatch_upstream_call returns to the E2A base class)
    {"text": str, "tool_calls": [...], "metadata": {"tokens_used", "input_tokens",
     "output_tokens", "stop_reason", "provider_request_id", "model"}}
"""
import json
from typing import Any, Dict, List, Optional, Tuple

Msg = Dict[str, Any]


def as_dict(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    for attr in ("model_dump", "to_dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn(mode="json") if attr == "model_dump" else fn()
            except TypeError:
                return fn()
    return dict(vars(obj))


def _args(value: Any) -> Dict[str, Any]:
    """Tool arguments arrive as a dict or a JSON string depending on the provider.
    Never string-match serialized input: parse it."""
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {"_raw": value}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def split_system(messages: List[Msg]) -> Tuple[str, List[Msg]]:
    system = "\n\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
    return system, [m for m in messages if m.get("role") != "system"]


def result(text: str = "", tool_calls: Optional[list] = None, *, input_tokens: int = 0,
           output_tokens: int = 0, stop_reason: Optional[str] = None, request_id: Optional[str] = None,
           model: Optional[str] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"text": text or "", "metadata": {
        "tokens_used": int(input_tokens or 0) + int(output_tokens or 0), "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0), "stop_reason": stop_reason,
        "provider_request_id": request_id, "model": model}}
    if tool_calls:
        out["tool_calls"] = tool_calls
    return out


# ----------------------------------------------------------------------- Anthropic Messages
def tools_to_anthropic(tools: Optional[List[Msg]], strict: bool = False) -> List[Msg]:
    out = []
    for t in tools or []:
        tool = {"name": t["name"], "description": t.get("description", ""), "input_schema": t["parameters"]}
        if strict:
            tool["strict"] = True
        out.append(tool)
    return out


def messages_to_anthropic(messages: List[Msg], cache_system: bool = False) -> Tuple[Any, List[Msg]]:
    system, rest = split_system(messages)
    wire: List[Msg] = []
    for m in rest:
        role = m.get("role")
        if role == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": str(m.get("content", ""))}
            if wire and wire[-1]["role"] == "user" and isinstance(wire[-1]["content"], list) and \
                    all(b.get("type") == "tool_result" for b in wire[-1]["content"]):
                wire[-1]["content"].append(block)        # parallel results go back in ONE user message
            else:
                wire.append({"role": "user", "content": [block]})
        elif role == "assistant" and m.get("tool_calls"):
            blocks: List[Msg] = [{"type": "text", "text": m["content"]}] if m.get("content") else []
            blocks += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c.get("arguments", {})}
                       for c in m["tool_calls"]]
            wire.append({"role": "assistant", "content": blocks})
        else:
            wire.append({"role": "assistant" if role == "assistant" else "user", "content": str(m.get("content", ""))})
    system_param: Any = system or None
    if system and cache_system:
        system_param = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    return system_param, wire


def anthropic_to_result(message: Any, request_id: Optional[str] = None) -> Dict[str, Any]:
    m = as_dict(message)
    text, calls = [], []
    for block in m.get("content") or []:
        if block.get("type") == "text":
            text.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            calls.append({"id": block["id"], "name": block["name"], "arguments": _args(block.get("input"))})
    usage = m.get("usage") or {}
    return result("".join(text), calls, input_tokens=usage.get("input_tokens", 0),
                  output_tokens=usage.get("output_tokens", 0), stop_reason=m.get("stop_reason"),
                  request_id=request_id, model=m.get("model"))


# ----------------------------------------------------------------------- OpenAI Responses API
def tools_to_openai_responses(tools: Optional[List[Msg]]) -> List[Msg]:
    return [{"type": "function", "name": t["name"], "description": t.get("description", ""),
             "parameters": t["parameters"], "strict": False} for t in tools or []]


def messages_to_openai_responses(messages: List[Msg]) -> Tuple[Optional[str], List[Msg]]:
    system, rest = split_system(messages)
    items: List[Msg] = []
    for m in rest:
        role = m.get("role")
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": m["tool_call_id"],
                          "output": str(m.get("content", ""))})
        elif role == "assistant" and m.get("tool_calls"):
            if m.get("content"):
                items.append({"role": "assistant", "content": m["content"]})
            items += [{"type": "function_call", "call_id": c["id"], "name": c["name"],
                       "arguments": json.dumps(c.get("arguments", {}))} for c in m["tool_calls"]]
        else:
            items.append({"role": "assistant" if role == "assistant" else "user", "content": str(m.get("content", ""))})
    return system or None, items


def openai_responses_to_result(response: Any, request_id: Optional[str] = None) -> Dict[str, Any]:
    r = as_dict(response)
    text, calls = [], []
    for item in r.get("output") or []:
        kind = item.get("type")
        if kind == "message":
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    text.append(part.get("text", ""))
                elif part.get("type") == "refusal":
                    return result(part.get("refusal", ""), stop_reason="refusal", request_id=request_id,
                                  model=r.get("model"))
        elif kind == "function_call":
            calls.append({"id": item.get("call_id") or item.get("id"), "name": item["name"],
                          "arguments": _args(item.get("arguments"))})
    usage = r.get("usage") or {}
    stop = "tool_use" if calls else ("max_tokens" if (r.get("incomplete_details") or {}).get("reason") ==
                                     "max_output_tokens" else "end_turn")
    return result("".join(text), calls, input_tokens=usage.get("input_tokens", 0),
                  output_tokens=usage.get("output_tokens", 0), stop_reason=stop, request_id=request_id,
                  model=r.get("model"))


# ----------------------------------------------------------------------- OpenAI Chat Completions
def tools_to_openai_chat(tools: Optional[List[Msg]]) -> List[Msg]:
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                              "parameters": t["parameters"]}} for t in tools or []]


def messages_to_openai_chat(messages: List[Msg]) -> List[Msg]:
    wire: List[Msg] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            wire.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": str(m.get("content", ""))})
        elif role == "assistant" and m.get("tool_calls"):
            wire.append({"role": "assistant", "content": m.get("content") or None, "tool_calls": [
                {"id": c["id"], "type": "function",
                 "function": {"name": c["name"], "arguments": json.dumps(c.get("arguments", {}))}}
                for c in m["tool_calls"]]})
        else:
            wire.append({"role": role if role in ("system", "assistant") else "user",
                         "content": str(m.get("content", ""))})
    return wire


def openai_chat_to_result(completion: Any, request_id: Optional[str] = None) -> Dict[str, Any]:
    c = as_dict(completion)
    choice = (c.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    calls = [{"id": t.get("id"), "name": t["function"]["name"], "arguments": _args(t["function"].get("arguments"))}
             for t in msg.get("tool_calls") or []]
    usage = c.get("usage") or {}
    finish = choice.get("finish_reason")
    stop = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens",
            "content_filter": "refusal"}.get(finish, finish)
    if msg.get("refusal"):
        return result(msg["refusal"], stop_reason="refusal", request_id=request_id, model=c.get("model"))
    return result(msg.get("content") or "", calls, input_tokens=usage.get("prompt_tokens", 0),
                  output_tokens=usage.get("completion_tokens", 0), stop_reason=stop, request_id=request_id,
                  model=c.get("model"))


# ----------------------------------------------------------------------- Gemini (google-genai)
def tools_to_gemini(tools: Optional[List[Msg]]) -> List[Msg]:
    if not tools:
        return []
    return [{"function_declarations": [{"name": t["name"], "description": t.get("description", ""),
                                        "parameters_json_schema": t["parameters"]} for t in tools]}]


def messages_to_gemini(messages: List[Msg]) -> Tuple[Optional[str], List[Msg]]:
    system, rest = split_system(messages)
    names: Dict[str, str] = {}            # Gemini answers a function by name, not by call id
    contents: List[Msg] = []
    for m in rest:
        role = m.get("role")
        if role == "tool":
            part = {"function_response": {"name": names.get(m["tool_call_id"], m["tool_call_id"]),
                                          "response": {"result": m.get("content", "")}}}
            if contents and contents[-1]["role"] == "user" and "function_response" in contents[-1]["parts"][0]:
                contents[-1]["parts"].append(part)
            else:
                contents.append({"role": "user", "parts": [part]})
        elif role == "assistant":
            parts: List[Msg] = [{"text": m["content"]}] if m.get("content") else []
            for c in m.get("tool_calls") or []:
                names[c["id"]] = c["name"]
                parts.append({"function_call": {"name": c["name"], "args": c.get("arguments", {})}})
            contents.append({"role": "model", "parts": parts})
        else:
            contents.append({"role": "user", "parts": [{"text": str(m.get("content", ""))}]})
    return system or None, contents


def gemini_to_result(response: Any, request_id: Optional[str] = None) -> Dict[str, Any]:
    r = as_dict(response)
    cand = (r.get("candidates") or [{}])[0]
    text, calls = [], []
    for i, part in enumerate(((cand.get("content") or {}).get("parts")) or []):
        if part.get("thought"):
            continue                                  # thought summaries are not the answer
        if part.get("text"):
            text.append(part["text"])
        fc = part.get("function_call")
        if fc:
            calls.append({"id": fc.get("id") or f"call_{i}_{fc['name']}", "name": fc["name"],
                          "arguments": _args(fc.get("args"))})
    usage = r.get("usage_metadata") or {}
    finish = cand.get("finish_reason")
    stop = "tool_use" if calls else {"STOP": "end_turn", "MAX_TOKENS": "max_tokens", "SAFETY": "refusal",
                                     "PROHIBITED_CONTENT": "refusal", "BLOCKLIST": "refusal"}.get(finish, finish)
    if (r.get("prompt_feedback") or {}).get("block_reason"):
        stop = "refusal"
    return result("".join(text), calls, input_tokens=usage.get("prompt_token_count") or 0,
                  output_tokens=(usage.get("candidates_token_count") or 0) + (usage.get("thoughts_token_count") or 0),
                  stop_reason=stop, request_id=request_id or r.get("response_id"), model=r.get("model_version"))
