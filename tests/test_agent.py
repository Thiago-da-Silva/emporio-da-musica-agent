"""Agent loop tests with a scripted fake model (no API calls)."""

from google.genai import types

from emporio_agent import agent as agent_module
from emporio_agent import storage


def call(name, **args):
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(
        role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))]))])


def text(message):
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(
        role="model", parts=[types.Part(text=message)]))])


def make_agent(monkeypatch, tmp_path, script, session="s1"):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "db.sqlite3")
    agent = agent_module.Agent(session_id=session)
    responses = iter(script)
    monkeypatch.setattr(agent, "_generate", lambda: next(responses))
    return agent


def test_tool_round_trip(monkeypatch, tmp_path):
    agent = make_agent(monkeypatch, tmp_path, [
        call("search_products", query="Takamine GD20", include_unavailable=True),
        text("O Takamine GD20 custa R$ 2.199,00."),
    ])
    turn = agent.reply("Quanto custa o Takamine GD20?")
    assert turn.reply == "O Takamine GD20 custa R$ 2.199,00."
    assert [c.name for c in turn.tool_calls] == ["search_products"]
    assert turn.tool_calls[0].result["produtos"][0]["product_id"] == 95
    # user, model(function_call), tool(response), model(text)
    assert [c.role for c in agent.history] == ["user", "model", "tool", "model"]


def test_float_ids_are_coerced_and_bad_args_reported(monkeypatch, tmp_path):
    agent = make_agent(monkeypatch, tmp_path, [
        call("get_product_details", product_id=95.0),
        call("get_store_policy", topic="horoscope"),
        text("ok"),
    ])
    turn = agent.reply("detalhes")
    assert turn.tool_calls[0].result["nome"].startswith("Takamine")
    assert "erro" in turn.tool_calls[1].result


def test_history_is_persisted_and_resumed(monkeypatch, tmp_path):
    agent = make_agent(monkeypatch, tmp_path, [text("Oi! Sou a Lia.")])
    agent.reply("oi")
    resumed = make_agent(monkeypatch, tmp_path, [])
    assert [c.role for c in resumed.history] == ["user", "model"]
    assert resumed.history[1].parts[0].text == "Oi! Sou a Lia."


def test_window_never_starts_with_tool_response(monkeypatch, tmp_path):
    agent = make_agent(monkeypatch, tmp_path, [])
    monkeypatch.setattr(agent_module, "MAX_HISTORY", 3)
    agent.history = [
        types.Content(role="user", parts=[types.Part(text="a")]),
        types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name="x"))]),
        types.Content(role="tool", parts=[types.Part.from_function_response(name="x", response={})]),
        types.Content(role="model", parts=[types.Part(text="b")]),
        types.Content(role="user", parts=[types.Part(text="c")]),
    ]
    window = agent._window()
    assert window[0].role == "user" and window[0].parts[0].text == "c"
