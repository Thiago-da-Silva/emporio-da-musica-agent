"""Store policy manual, split into sections the agent can request by topic.

The manual has ~10 short sections, so instead of embeddings + vector search
we split it on its own headings and expose a fixed list of topics. The LLM
picks the topic; retrieval is exact and deterministic.
"""

import re
from functools import lru_cache

from .data import DATA_DIR

# topic -> section number in data/policies.md
POLICY_TOPICS = {
    "store_info": "1",        # about, address, phone, e-mail, what we sell
    "opening_hours": "2",
    "payment": "3",           # payment methods, PIX discount, installments
    "returns_exchanges": "4",
    "shipping_delivery": "5",
    "promotions": "6",
    "warranty": "8",
    "privacy": "9",
}


@lru_cache(maxsize=1)
def load_sections() -> dict[str, str]:
    text = (DATA_DIR / "policies.md").read_text(encoding="utf-8")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    sections = {}
    for chunk in re.split(r"^## ", text, flags=re.M)[1:]:
        number = chunk.split(".", 1)[0].strip()
        sections[number] = "## " + chunk.strip()
    return sections


def get_policy_text(topic: str) -> str | None:
    number = POLICY_TOPICS.get(topic)
    return load_sections().get(number) if number else None
