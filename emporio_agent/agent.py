"""Agent loop: Gemini with manual function calling.

We drive the loop ourselves instead of using the SDK's automatic function
calling so that every tool call can be logged (CLI --debug, evals) and
arguments can be validated before running any code.

The provider is isolated in this module: switching to another LLM (e.g.
Claude Haiku) means rewriting `_generate` and the history format only.
"""

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from pydantic import ValidationError, validate_call

from . import clock, storage
from .prompt import build_system_prompt
from .schema import declaration_for
from .tools import TOOLS, TOOLS_BY_NAME

load_dotenv()
log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.5-flash"
DEFAULT_FALLBACK_MODEL = "gemini-3.5-flash-lite"  # empty string disables the fallback
MAX_TOOL_ROUNDS = 6      # safety net against tool-calling loops
MAX_HISTORY = 40         # contents sent to the model (DB keeps everything)
RETRYABLE = {429, 500, 503}
FALLBACK_REPLY = ("Desculpe, tive um probleminha técnico agora. "
                  "Pode repetir sua mensagem em instantes? 🙏")


@dataclass
class ToolCall:
    name: str
    args: dict
    result: dict


@dataclass
class Turn:
    reply: str
    tool_calls: list[ToolCall] = field(default_factory=list)


def run_tool(name: str, args: dict) -> dict:
    func = TOOLS_BY_NAME.get(name)
    if not func:
        return {"erro": f"Ferramenta desconhecida: {name}"}
    try:
        # validate_call coerces e.g. 95.0 -> 95 and rejects invalid enums
        return validate_call(func)(**args)
    except ValidationError as exc:
        return {"erro": f"Argumentos inválidos: {exc.errors(include_url=False)}"}
    except Exception as exc:  # a tool bug must not crash the conversation
        return {"erro": f"Falha ao executar {name}: {exc}"}


class Agent:
    def __init__(self, session_id: str = "default", model: str | None = None,
                 persist: bool = True, on_tool_call: Callable[[ToolCall], None] | None = None):
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.model = model or os.getenv("GEMINI_MODEL") or DEFAULT_MODEL
        self.fallback_model = os.getenv("GEMINI_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL)
        self.session_id = session_id
        self.on_tool_call = on_tool_call
        self.tools = [types.Tool(function_declarations=[declaration_for(t) for t in TOOLS])]
        self.conn = storage.connect() if persist else None
        self.history: list[types.Content] = []
        if self.conn:
            self.history = [types.Content.model_validate_json(c)
                            for c in storage.load_messages(self.conn, session_id)]

    def _window(self) -> list[types.Content]:
        """Last MAX_HISTORY contents, starting at a customer text message so a
        function response is never sent without its function call."""
        window = self.history[-MAX_HISTORY:]
        for i, content in enumerate(window):
            if content.role == "user" and any(p.text for p in content.parts or []):
                return window[i:]
        return window

    def _generate(self) -> types.GenerateContentResponse:
        config = types.GenerateContentConfig(
            system_instruction=build_system_prompt(clock.now()),
            tools=self.tools,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.3,
        )
        # Free-tier models are often overloaded (503) or rate limited (429):
        # alternate between the main and the fallback model, backing off a bit.
        models = [self.model, self.fallback_model] if self.fallback_model else [self.model]
        for attempt in range(4):
            model = models[attempt % len(models)]
            try:
                return self.client.models.generate_content(
                    model=model, contents=self._window(), config=config)
            except errors.APIError as exc:
                if exc.code not in RETRYABLE or attempt == 3:
                    raise
                log.info("Gemini API %s on %s, retrying (attempt %d)", exc.code, model, attempt + 1)
                time.sleep(3 * attempt)

    def reply(self, message: str) -> Turn:
        start = len(self.history)
        self.history.append(types.Content(role="user", parts=[types.Part(text=message)]))
        turn = Turn(reply="")
        try:
            for _ in range(MAX_TOOL_ROUNDS + 1):
                response = self._generate()
                candidate = response.candidates[0] if response.candidates else None
                if not candidate or not candidate.content or not candidate.content.parts:
                    break
                self.history.append(candidate.content)
                calls = response.function_calls or []
                if not calls:
                    turn.reply = (response.text or "").strip()
                    break
                parts = []
                for call in calls:
                    args = dict(call.args or {})
                    result = run_tool(call.name, args)
                    tool_call = ToolCall(call.name, args, result)
                    turn.tool_calls.append(tool_call)
                    if self.on_tool_call:
                        self.on_tool_call(tool_call)
                    parts.append(types.Part.from_function_response(name=call.name, response=result))
                self.history.append(types.Content(role="user", parts=parts))
        except errors.APIError as exc:
            log.warning("Gemini API error: %s %s", exc.code, exc.message)
            # Roll back the incomplete turn so the history stays consistent
            del self.history[start:]
            return Turn(reply=FALLBACK_REPLY, tool_calls=turn.tool_calls)

        if not turn.reply:
            turn.reply = FALLBACK_REPLY
            self.history.append(types.Content(role="model", parts=[types.Part(text=FALLBACK_REPLY)]))
        if self.conn:
            storage.append_messages(self.conn, self.session_id, [
                c.model_dump_json(exclude_none=True) for c in self.history[start:]])
        return turn


def tool_call_summary(call: ToolCall) -> str:
    args = json.dumps(call.args, ensure_ascii=False)
    return f"{call.name}({args})"
