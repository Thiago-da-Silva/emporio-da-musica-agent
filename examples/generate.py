"""Records the example conversations in examples/*.md by running the real agent.

    python -m examples.generate            # all scenarios
    python -m examples.generate 04         # only scenarios whose file starts with "04"

Replies are not edited; tool calls are shown so the reader can see when the
agent looked up data and when it looked up policies.
"""

import json
import sys
import time
from pathlib import Path

from emporio_agent import clock
from emporio_agent.agent import Agent

PAUSE_SECONDS = 6  # free tier rate limits
OUT_DIR = Path(__file__).parent

SCENARIOS = {
    "01_catalog_search": (
        "Catalog search with price filter and a follow-up recommendation",
        ["Oi! Quais opções de violões disponíveis custando até R$1000?",
         "Sou iniciante e prefiro corda de nylon. Qual desses você indicaria?"],
    ),
    "02_store_info_and_out_of_scope": (
        "General store information, accessories (not sold) and an unrelated question",
        ["Qual o endereço da loja e que horas vocês abrem?",
         "Vocês vendem cordas e amplificador pra guitarra?",
         "Ah, e você sabe qual a capital da França?"],
    ),
    "03_price_promotion_payment": (
        "Price lookup, active promotion and the PIX discount not stacking with it",
        ["Quanto custa o Takamine GD20?",
         "E o Crafter HT-100? Vi que tá em promoção. Se eu pagar no PIX ganho mais 5% em cima?",
         "Em quantas vezes sem juros eu consigo parcelar ele?"],
    ),
    "04_return_policy_order": (
        "Return request: identity check, order lookup and policy deadlines applied to the order",
        ["Me arrependi da minha compra, posso devolver meu pedido?",
         "É o pedido 7",
         "leticia.rocha@jmail.com",
         "E se o ukulele tiver algum defeito, ainda tenho alguma opção?"],
    ),
    "05_delayed_order_complaint": (
        "Order privacy check, delayed delivery and complaint ticket",
        ["Meu pedido 8 ainda não chegou! O e-mail é ana@gmail.com",
         "Ops, errei. É anacarol.ferreira@coldmail.com",
         "Isso é um absurdo, quero registrar uma reclamação. Meu nome é Ana Carolina Ferreira, "
         "pode me retornar nesse mesmo e-mail."],
    ),
}


def record(name: str, title: str, messages: list[str]) -> str:
    agent = Agent(session_id=f"example-{name}", persist=False)
    lines = [
        f"# {title}",
        "",
        f"_Recorded on {clock.now():%Y-%m-%d %H:%M} (store time) with `{agent.model}`. "
        "Agent replies are unedited; tool calls are shown as `🔧`._",
        "",
    ]
    for message in messages:
        turn = agent.reply(message)
        lines += [f"**Cliente:** {message}", ""]
        for call in turn.tool_calls:
            args = json.dumps(call.args, ensure_ascii=False)
            lines.append(f"> 🔧 `{call.name}({args})`  ")
        if turn.tool_calls:
            lines.append("")
        lines += ["**Lia:**", "", turn.reply, "", "---", ""]
        time.sleep(PAUSE_SECONDS)
    return "\n".join(lines)


def main() -> None:
    prefixes = sys.argv[1:]
    for name, (title, messages) in SCENARIOS.items():
        if prefixes and not any(name.startswith(p) for p in prefixes):
            continue
        print(f"recording {name}...")
        (OUT_DIR / f"{name}.md").write_text(record(name, title, messages), encoding="utf-8")


if __name__ == "__main__":
    main()
