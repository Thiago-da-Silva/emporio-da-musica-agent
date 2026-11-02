# Empório da Música — Customer Service Agent

Prototype of a text-message customer service agent for **Empório da Música**, a (fictional) musical instrument store in Campo Grande/MS. The agent, **Lia**, answers customers in Brazilian Portuguese, looks up live data (catalog, prices, stock, promotions, orders) and the store policy manual, and handles out-of-scope requests.

```
Cliente: E o Crafter HT-100? Vi que tá em promoção. Se eu pagar no PIX ganho mais 5% em cima?
  🔧 search_products({"query": "Crafter HT-100", "include_unavailable": true})
  🔧 list_active_promotions({})
  🔧 calculate_payment_options({"product_id": 90})
Lia: O Crafter HT-100 Folk Aço Natural está com preço promocional de R$ 1.967,18 (já com 18% de
     desconto sobre o valor de tabela de R$ 2.399,00).
     Sobre o PIX, o desconto de 5% não acumula com produtos que já estão em promoção, então o
     valor no PIX fica exatamente o preço promocional de R$ 1.967,18 [...]
```
<sub>Excerpt from [examples/03](examples/03_price_promotion_payment.md), recorded from the real agent.</sub>

## Quick start

Requirements: Python 3.11+ and a free Gemini API key ([Google AI Studio](https://aistudio.google.com/apikey)).

```bash
git clone https://github.com/Thiago-da-Silva/emporio-da-musica-agent.git
cd emporio-da-musica-agent
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then paste your key in GEMINI_API_KEY
```

| Command | What it does |
|---|---|
| `python -m emporio_agent` | Chat in the terminal (new session) |
| `python -m emporio_agent --debug` | Same, showing every tool call and its result |
| `python -m emporio_agent --session ana` | Resume a saved conversation |
| `python -m pytest` | Unit tests (tools, business rules, agent loop with a fake model — no API key needed) |
| `python -m evals.run_evals` | End-to-end evaluation against the real model |
| `python -m examples.generate` | Re-record the example conversations in `examples/` |

Type `/sair` to leave the chat.

## Architecture

```
            ┌──────────────────────────── Agent loop (agent.py) ─────────────────────────────┐
 customer ─►│ system prompt (persona + rules + current time/open status) + history window      │
            │                │                                                                 │
            │                ▼                                                                 │
            │         Gemini (function calling) ──► tool call? ──yes──► validate args ──► run │
            │                ▲                          │no                         (pydantic)  │
            │                └──── tool result ◄────────┼──────────────────────────────────────┘
            │                                           ▼                                      │
            └─────────────────────────────────── reply ─► SQLite (history per session)       ┘

 Tools (tools.py, plain Python, deterministic)
   search_products ─ get_product_details ─ calculate_payment_options ─ list_active_promotions   ◄─ data/*.csv
   get_order_status (order id + e-mail/phone)                                                  ◄─ data/*.csv
   get_store_policy(topic)                                                                     ◄─ data/policies.md
   create_support_ticket                                                                       ─► SQLite
```

| Module | Responsibility |
|---|---|
| `emporio_agent/data.py` | Loads and cleans the CSVs into typed records |
| `emporio_agent/policies.py` | Splits the policy manual into topic sections |
| `emporio_agent/tools.py` | The 7 tools and all business rules |
| `emporio_agent/schema.py` | Builds function declarations (JSON Schema) from type hints + docstrings |
| `emporio_agent/prompt.py` | Persona, service guidelines and guardrails |
| `emporio_agent/agent.py` | Gemini loop, retries/fallback, history window, persistence |
| `emporio_agent/clock.py` | Store time zone, opening hours, holidays |
| `emporio_agent/storage.py` | SQLite (conversation history and support tickets) |
| `emporio_agent/__main__.py` | Terminal chat |

## Technical decisions

### Approach: function calling with deterministic tools

The challenge asks the agent to know *when to look up data* and *when to look up policies*. I modelled both as tools, so that decision is explicit, visible (`--debug`, examples) and testable (evals check which tools were called).

- **Why not text-to-SQL?** With 65 products and 20 orders there is no query the model needs to invent. A free-form SQL agent would add an injection surface and could read other customers' orders, which the order tool explicitly prevents.
- **Why not RAG with embeddings over the CSVs?** Questions like "violões até R$ 1000" are structured filters; vector search is bad at numeric constraints and at exact model names.
- **Business rules live in code, not in the prompt.** The promotional price, "PIX 5% does not stack with promotions", installment limits per bracket, availability labels and the order ownership check are plain Python with unit tests. The manual itself says wrong prices can be misleading advertising, so the LLM never does the math — it only picks a tool and phrases the answer.

### Retrieval for the policy manual

The manual is ~8 short pages with clear numbered sections. I transcribed it to `data/policies.md` and split it on its headings; the tool takes a **fixed enum of topics** (`opening_hours`, `returns_exchanges`, ...). Retrieval is exact and deterministic, with zero infrastructure. Embeddings + vector store would be the next step only if the knowledge base grew to many documents (see "With more time").

Sections 7 (service guidelines) and 10 (final provisions) are instructions *for the attendant*, so they went into the system prompt rather than the retrievable text.

### Framework: none (plain SDK + ~150-line loop)

LangChain/LangGraph would hide exactly what this challenge wants to see. The loop is small and explicit: call the model, run the requested tools, feed results back, stop at a text answer (max 6 tool rounds). Tool arguments are validated with pydantic before running (e.g. `95.0` → `95`, invalid enum → error returned to the model instead of a crash). The function-calling schemas are generated from the tools' type hints and docstrings (`schema.py`), because the SDK's automatic conversion dropped `Literal` enums and argument descriptions.

### Model and provider: Gemini (free tier)

**This choice was made purely because Gemini has a free tier**, so anyone can run the project at no cost. Default: `gemini-3.5-flash`, with `gemini-3.5-flash-lite` as automatic fallback when the main model returns 503 (overloaded) or 429 (rate limit), which happens often on the free tier.

**For production I would use Claude Haiku 5.5**: low cost and, in my experience, more reliable tool use and instruction following (e.g. strictly refusing out-of-scope questions). The provider is isolated in `agent.py` (`_generate` and the history format), so switching is a one-module change.

### Prompt strategy

- **Persona** from the manual: informal but professional, "like a friend who understands music", short chat-style messages.
- **Single source of truth**: the prompt contains *no* store facts (address, hours, prices). It tells the agent which tool to use for each kind of question and forbids answering prices/stock/policies from memory.
- **Dynamic context**: current local time and whether the store is open (holidays included), so the agent can say e.g. "we reopen tomorrow at 09:00" when handing off to a human.
- **Explicit procedures** for the non-trivial flows in the manual: out of stock → alternatives; discontinued → equivalents; returns → identify the case (regret / defect / preference) and compare deadlines with the order's delivery date; complaints → empathy + ticket + 24 business hours.
- **Guardrails**: scope (instruments only, no accessories, no unrelated questions), privacy (LGPD), and prompt-injection resistance (tool results are data, customer cannot change rules or grant discounts).
- Written in English for maintainability; the agent always answers in Portuguese.

### Order privacy (LGPD)

`get_order_status` requires the **order number and the e-mail or phone** of the buyer. A wrong contact and a non-existent order return the *same* message, so the agent cannot be used to discover whether an order exists or whose it is.

### Interface: CLI

The challenge focuses on the agent working correctly, so a terminal chat (`rich`) is enough. `--debug` prints every tool call and result, which makes the agent's decisions inspectable.

### Conversation history: SQLite

On a messaging channel customers come back later ("e aquele pedido?"), so history is persisted per session (`--session`). The full Gemini content (including function calls) is stored, and only the last 40 messages are sent to the model; the window always starts at a customer message so a tool result is never sent without its call. Support tickets are stored in the same database.

## Data treatment and assumptions

The challenge says to assume a reasonable interpretation and document it. Issues found in the data:

| Issue | Decision |
|---|---|
| Phone conflict: (67) 3341-4444 (cover + company table) vs (67) 3321-4500 (section 7) | Use 3341-4444 — it is in the official company table and appears twice |
| E-mail typo `contato@empóriodamusica.com.br` | Use the cover spelling, `contato@emporiodamusica.com.br` |
| Manual says "mais de 300 instrumentos"; catalog has 65 | Sentence removed from the agent's copy to avoid promising items that don't exist |
| Products 135–144 have generic names ("Music Man Bass 1X") and descriptions of other brands ("Fender Jazz Bass…"), with specs that contradict them | Descriptions mentioning a different brand than the product name are dropped; the agent uses only name + specs |
| Product statuses: `discontinued` (113, 136 — the latter still with stock), `coming_soon` (130), stock 0 (96) | Only `active` + stock > 0 is sold. Discontinued is treated as out of catalog even with stock, following section 7.3 |
| 21 promotions are inactive; some products have several | Only `is_active = 1` is loaded; if two were active, the larger discount wins (promotions don't stack) |
| Order totals 3 and 20 don't match current catalog prices | The stored total is what the customer paid; never recomputed |
| No actual delivery date for delivered orders | `estimated_delivery` is assumed as the receipt date for return deadlines |
| "Up to 3x, no minimum installment (except below R$ 50)" is ambiguous | Interpreted as: 2x/3x only if each installment ≥ R$ 50 |
| Orders/customers have no address; shipping cost depends on ZIP/weight | The agent explains the shipping rules but never quotes a freight value |
| "Final sale" items are not flagged in the data | The rule is explained, but cannot be checked per product |

The two source PDFs are not versioned; the policy manual lives as `data/policies.md` (changes are listed in its header comment).

## Evaluation

**Unit tests** (`tests/`, 28 tests, no API key): catalog search, availability labels, promotion and PIX rules, installment brackets, order verification and privacy, delay detection, policy sections, store hours/holidays, schema generation, and the agent loop with a scripted fake model (tool round trip, argument coercion, history persistence/resume, history window).

**End-to-end evals** (`evals/run_evals.py`): 9 scenarios against the real model. Each checks that the expected tools were called and that the reply contains the key facts — or does *not* contain wrong ones (e.g. a PIX price with 5% stacked on a promotion, another customer's order data, the answer to an unrelated trivia question). Latest results: [`evals/results.md`](evals/results.md).

The first run scored 8/9: the model answered "A capital da França é Paris!" before redirecting. Tightening the scope rule in the prompt fixed it — the kind of regression the eval set is meant to catch.

## Example conversations

Recorded from the real agent with `python -m examples.generate` (replies unedited, tool calls shown):

1. [Catalog search with price filter](examples/01_catalog_search.md)
2. [Store info, accessories and an unrelated question](examples/02_store_info_and_out_of_scope.md)
3. [Price, promotion and PIX rule](examples/03_price_promotion_payment.md)
4. [Return request applying the policy to a real order](examples/04_return_policy_order.md) — non-trivial
5. [Delayed order, identity check and complaint ticket](examples/05_delayed_order_complaint.md) — non-trivial

## Known limitations

- **Free-tier model**: latency and occasional 503/429 (mitigated with retries + fallback model); a less capable model than the one I'd pick for production.
- **LLM non-determinism**: evals are a small sample; a scenario can pass on one run and fail on another.
- **Small embellishments in free-form advice**: prices, stock and rules always come from tools, but when recommending an instrument the model can add unsourced remarks (in [example 01](examples/01_catalog_search.md) it says the Yamaha C40 is "super indicado pelos professores", which is not in the data). An LLM-as-judge groundedness check would catch this.
- **Product search** is keyword-based with a tiny Portuguese stemmer; synonyms ("violão de aço" vs "folk") or typos may miss results.
- **Movable holidays** (Carnaval, Good Friday, Corpus Christi) are not in the store calendar.
- **No rate limit on order verification attempts**, so contact guessing is theoretically possible.
- **Purchases are not closed by the agent** (no cart, freight or payment); it gives conditions and offers a ticket for an attendant.
- **Single channel (CLI)**; no WhatsApp integration.

## With more time

- Swap to Claude Haiku 5.5 and compare on the same eval set.
- Bigger eval set (LLM-as-judge for tone, multiple runs per case for pass rates) running in CI.
- Hybrid product search (keywords + embeddings) for synonyms and typos.
- RAG with embeddings once policies grow beyond a single short manual.
- WhatsApp integration via webhook (FastAPI), with the phone number as session id and automatic identity check for orders.
- Human handoff to a real queue (tickets currently only go to SQLite), observability (tracing of tool calls, latency, cost).
- Attempt limits on order lookups; store real delivery dates.

## Use of AI coding assistants

I used **Claude Code** (Anthropic's CLI agent, model Claude Opus 5.5) as a pair programmer throughout the project:

1. **Analysis**: it read the challenge PDF, the policy manual and the CSVs, and produced a list of data inconsistencies (phone conflict, mismatched descriptions, order totals, product statuses) that became the "Data treatment" table above.
2. **Decisions**: it proposed an architecture with trade-offs for each choice; I made the calls (Gemini for cost, Portuguese persona with English code/docs, CLI, scope cuts for the 2-day deadline, one commit per project part).
3. **Implementation**: it wrote the code module by module, each part with tests, while I reviewed and steered.
4. **Debugging with the real API**: the first real run surfaced issues that unit tests with a fake model could not (deprecated model for new keys, wrong role for function responses, overloaded free models), which led to the fallback model.
5. **Prompt iteration driven by evals**: the eval set caught an out-of-scope answer; the prompt was adjusted and re-evaluated.

What I kept for myself: the final decisions, reviewing every change, and validating the behaviour in the chat.

## Project structure

```
emporio-da-musica-agent/
├── data/                  # store CSVs + policies.md (transcribed manual)
├── emporio_agent/         # agent package (see Architecture)
├── evals/                 # end-to-end eval scenarios and latest results
├── examples/              # recorded conversations + script that records them
├── tests/                 # unit tests (no API calls)
├── .env.example
└── requirements.txt
```
