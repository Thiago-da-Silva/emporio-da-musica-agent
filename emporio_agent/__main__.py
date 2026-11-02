"""Chat CLI.

    python -m emporio_agent                  # new conversation
    python -m emporio_agent --session ana    # resume a saved conversation
    python -m emporio_agent --debug          # show tool calls and results
"""

import argparse
import json
import os
import sys
import uuid

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from .agent import Agent, ToolCall, tool_call_summary

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(description="Empório da Música — assistente virtual")
    parser.add_argument("--session", help="session id to resume (history is saved in SQLite)")
    parser.add_argument("--debug", action="store_true", help="show tool calls and their results")
    parser.add_argument("--model", help="Gemini model (default: GEMINI_MODEL or gemini-3.5-flash)")
    args = parser.parse_args()

    if not os.getenv("GEMINI_API_KEY"):
        # load_dotenv runs on import of .agent; still missing means no key at all
        console.print("[red]GEMINI_API_KEY not set. Copy .env.example to .env and add your key.[/red]")
        sys.exit(1)

    def show_tool(call: ToolCall) -> None:
        console.print(f"[dim]  ⚙ {tool_call_summary(call)}[/dim]")
        if args.debug:
            result = json.dumps(call.result, ensure_ascii=False, indent=2)
            console.print(f"[dim]{result[:1500]}[/dim]")

    session = args.session or uuid.uuid4().hex[:8]
    agent = Agent(session_id=session, model=args.model, on_tool_call=show_tool if args.debug else None)

    console.print(Panel.fit(
        "[bold]Empório da Música[/bold] — Sua música começa aqui. 🎸\n"
        f"Sessão: [cyan]{session}[/cyan] · modelo: {agent.model}\n"
        "Digite sua mensagem. /sair para encerrar.",
        border_style="yellow"))
    if agent.history:
        console.print(f"[dim](conversa retomada com {len(agent.history)} mensagens no histórico)[/dim]")

    while True:
        try:
            message = console.input("\n[bold green]Você:[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not message:
            continue
        if message.lower() in {"/sair", "/exit", "sair"}:
            break
        with console.status("Lia está digitando..."):
            turn = agent.reply(message)
        console.print("\n[bold magenta]Lia:[/bold magenta]")
        console.print(Markdown(turn.reply))

    console.print(f"\n[dim]Até logo! Para retomar: python -m emporio_agent --session {session}[/dim]")


if __name__ == "__main__":
    main()
