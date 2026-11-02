"""Tools the LLM can call.

Every business rule (availability, promotion price, PIX discount, installment
limits, order ownership check) is computed here in plain Python, so the model
only decides *which* tool to call and how to phrase the answer. The
docstrings and type hints double as the function-calling schema sent to Gemini.
"""

import math
from datetime import date
from typing import Literal

from . import clock, storage
from .data import Product, digits, get_store, normalize
from .policies import get_policy_text

CategoryName = Literal[
    "Guitarras", "Baixos", "Baterias e Percussão", "Teclados e Pianos", "Violões",
    "Instrumentos de Sopro (Madeiras)", "Instrumentos de Sopro (Metais)",
    "Cordas Orquestrais", "Ukuleles",
]
PolicyTopic = Literal[
    "store_info", "opening_hours", "payment", "returns_exchanges",
    "shipping_delivery", "promotions", "warranty", "privacy",
]

PIX_DISCOUNT = 0.05
COMBINED_PAYMENT_MIN = 2000.0
# (max installments in the bracket, minimum installment value)
INSTALLMENT_BRACKETS = [(3, 50.0), (6, 80.0), (12, 100.0)]
LOW_STOCK = 3
MAX_RESULTS = 10

STATUS_LABELS = {
    "pending": "Aguardando pagamento/aprovação",
    "confirmed": "Pagamento confirmado, em separação",
    "shipped": "Enviado (em transporte)",
    "delivered": "Entregue",
    "cancelled": "Cancelado",
}
PAYMENT_LABELS = {
    "pix": "PIX", "debit": "Cartão de débito", "boleto": "Boleto bancário",
}
STOPWORDS = {"de", "da", "do", "das", "dos", "com", "para", "e", "o", "a", "os", "as",
             "um", "uma", "ate", "r", "reais", "em", "no", "na", "modelo", "quero"}


def _stem(word: str) -> str:
    """Very small Portuguese plural stemmer: violões -> violao, guitarras -> guitarra."""
    if word.endswith(("oes", "aes")):
        return word[:-3] + "ao"
    if word.endswith("s") and len(word) > 3:
        return word[:-1]
    return word


def _tokens(text: str) -> list[str]:
    return [_stem(t) for t in normalize(text).split() if t not in STOPWORDS and len(t) > 1]


def _money(value: float) -> float:
    return round(value + 1e-9, 2)


def _price_info(product: Product) -> dict:
    promo = get_store().promotions.get(product.id)
    info = {"preco_tabela": product.price}
    if promo:
        info["promocao"] = {
            "campanha": promo.description,
            "desconto_percentual": promo.discount_percent,
            "preco_promocional": _money(product.price * (1 - promo.discount_percent / 100)),
        }
    return info


def _effective_price(product: Product) -> float:
    promo = get_store().promotions.get(product.id)
    return product.price * (1 - promo.discount_percent / 100) if promo else product.price


def _availability(product: Product) -> str:
    if product.status == "discontinued":
        return "descontinuado (não faz mais parte do catálogo)"
    if product.status == "coming_soon":
        return "em breve (ainda não disponível para venda)"
    if product.stock <= 0:
        return "esgotado no momento"
    if product.stock <= LOW_STOCK:
        return "disponível (últimas unidades)"
    return "disponível"


def _summary(product: Product) -> dict:
    return {
        "product_id": product.id,
        "nome": product.name,
        "categoria": get_store().categories[product.category_id].name,
        **_price_info(product),
        "disponibilidade": _availability(product),
    }


def _alternatives(product: Product, limit: int = 3) -> list[dict]:
    """Available products of the same category with the closest price."""
    candidates = [p for p in get_store().products.values()
                  if p.category_id == product.category_id and p.available and p.id != product.id]
    candidates.sort(key=lambda p: abs(_effective_price(p) - product.price))
    return [_summary(p) for p in candidates[:limit]]


def search_products(
    query: str = "",
    category: CategoryName | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    include_unavailable: bool = False,
) -> dict:
    """Search the instrument catalog. Use for any question about products, prices or availability.

    Args:
        query: Free-text terms such as brand, model or type (e.g. "Takamine GD20", "nylon",
            "elétrico", "7 cordas"). Leave empty to list a whole category. Do not put prices here.
        category: Restrict to one catalog category.
        min_price: Minimum price in BRL (uses the promotional price when there is an active promotion).
        max_price: Maximum price in BRL (uses the promotional price when there is an active promotion).
        include_unavailable: Also return out-of-stock, discontinued and coming-soon items.
            Set to true when the customer asks about a specific model by name.
    """
    store = get_store()
    terms = _tokens(query)
    scored = []
    for product in store.products.values():
        category_name = store.categories[product.category_id].name
        if category and category_name != category:
            continue
        if not include_unavailable and not product.available:
            continue
        price = _effective_price(product)
        if min_price is not None and price < min_price:
            continue
        if max_price is not None and price > max_price:
            continue
        haystack = _tokens(" ".join([product.name, category_name, *map(str, product.specs.values())]))
        compact = "".join(haystack)
        score = sum(1 for t in terms if t in haystack or t in compact)
        scored.append((score, price, product))

    exact = [s for s in scored if s[0] == len(terms)]
    partial = sorted((s for s in scored if 0 < s[0] < len(terms)), key=lambda s: (-s[0], s[1]))
    exact.sort(key=lambda s: s[1])
    results = exact or partial

    response = {
        "total_encontrado": len(results),
        "produtos": [_summary(p) for _, _, p in results[:MAX_RESULTS]],
    }
    if not exact and partial:
        response["observacao"] = "Nenhum produto corresponde a todos os termos; resultados parciais."
    if len(results) > MAX_RESULTS:
        response["observacao_lista"] = f"Mostrando os {MAX_RESULTS} mais baratos; refine a busca."
    return response


def get_product_details(product_id: int) -> dict:
    """Full details of one product: description, specs, price, promotion and availability.
    When the product is not available, also returns similar available alternatives.

    Args:
        product_id: The product_id returned by search_products.
    """
    product = get_store().products.get(product_id)
    if not product:
        return {"erro": f"Produto {product_id} não encontrado."}
    details = _summary(product)
    details["descricao"] = product.description or "Descrição indisponível; use apenas nome e especificações."
    details["especificacoes"] = product.specs
    if not product.available:
        details["alternativas_disponiveis"] = _alternatives(product)
    return details


def _max_installments(total: float) -> int:
    best = 1
    for n in range(2, 13):
        minimum = next(m for limit, m in INSTALLMENT_BRACKETS if n <= limit)
        if total / n >= minimum:
            best = n
    return best


def calculate_payment_options(product_id: int, quantity: int = 1) -> dict:
    """Payment options for buying a product: PIX price, maximum interest-free installments
    and whether combining payment methods is allowed. Shipping is not included.

    Args:
        product_id: The product_id returned by search_products.
        quantity: Number of units.
    """
    product = get_store().products.get(product_id)
    if not product:
        return {"erro": f"Produto {product_id} não encontrado."}
    quantity = max(1, int(quantity))
    promo = get_store().promotions.get(product.id)
    total = _money(_effective_price(product) * quantity)
    n = _max_installments(total)

    options = {
        "produto": product.name,
        "quantidade": quantity,
        **_price_info(product),
        "total": total,
        "cartao_debito_ou_boleto": total,
        "cartao_credito_sem_juros": {
            "max_parcelas": n,
            "valor_parcela": _money(math.floor(total / n * 100) / 100),
        },
        "permite_combinar_formas_pagamento": total > COMBINED_PAYMENT_MIN,
    }
    if promo:
        options["pix"] = {"total": total, "observacao": "Desconto PIX de 5% não acumula com promoção."}
    else:
        options["pix"] = {"total": _money(total * (1 - PIX_DISCOUNT)), "observacao": "5% de desconto no PIX."}
    if not product.available:
        options["aviso"] = f"Produto {_availability(product)}."
    return options


def _contact_matches(customer, email_or_phone: str) -> bool:
    given = email_or_phone.strip().lower()
    if "@" in given:
        return given == customer.email
    given_digits, stored_digits = digits(given), digits(customer.phone)
    return len(given_digits) >= 8 and stored_digits.endswith(given_digits)


def get_order_status(order_id: int, email_or_phone: str) -> dict:
    """Status of a customer's order. For privacy (LGPD) the customer must provide the order
    number AND the e-mail or phone registered in the purchase. Ask for both before calling.

    Args:
        order_id: The order number.
        email_or_phone: E-mail or phone number registered by the customer.
    """
    store = get_store()
    order = store.orders.get(order_id)
    customer = store.customers.get(order.customer_id) if order else None
    if not order or not customer or not _contact_matches(customer, email_or_phone):
        # Same message whether the order exists or not, to avoid leaking data.
        return {"verificado": False,
                "mensagem": "Não encontramos um pedido com esse número para o contato informado."}

    result = {
        "verificado": True,
        "pedido": order.id,
        "cliente_primeiro_nome": customer.name.split()[0],
        "data_pedido": order.date,
        "status": STATUS_LABELS.get(order.status, order.status),
        "total_pago": order.total,
        "forma_pagamento": PAYMENT_LABELS.get(order.payment_method,
                                              order.payment_method.replace("credit_", "Cartão de crédito em ")),
        "itens": [{"produto": store.products[i.product_id].name if i.product_id in store.products
                   else f"Produto {i.product_id}", "quantidade": i.quantity} for i in order.items],
    }
    if order.tracking_code:
        result["codigo_rastreio"] = order.tracking_code
    if order.estimated_delivery:
        result["previsao_entrega"] = order.estimated_delivery
        days = (clock.today() - date.fromisoformat(order.estimated_delivery)).days
        if order.status == "shipped" and days > 0:
            result["alerta"] = f"Entrega atrasada em {days} dias em relação à previsão."
        if order.status == "delivered":
            # We only have the estimated date; assume it as the receipt date.
            result["dias_desde_entrega"] = days
    if order.notes:
        result["observacao"] = order.notes
    return result


def list_active_promotions() -> dict:
    """List all promotions currently active in the system."""
    store = get_store()
    items = [_summary(store.products[pid]) for pid in store.promotions if pid in store.products]
    return {"promocoes_ativas": items, "total": len(items)}


def get_store_policy(topic: PolicyTopic) -> dict:
    """Official store policy text. Use for questions about the store itself (address, contact,
    what it sells), opening hours, payment methods and installments, returns and exchanges,
    shipping and delivery, promotion rules, warranty and privacy.

    Args:
        topic: Policy topic to retrieve.
    """
    text = get_policy_text(topic)
    return {"topico": topic, "texto": text} if text else {"erro": f"Tópico desconhecido: {topic}"}


def create_support_ticket(
    reason: Literal["complaint", "human_request", "other"],
    customer_name: str,
    contact: str,
    summary: str,
    order_id: int | None = None,
) -> dict:
    """Register a complaint or a request to talk to a human attendant. The team replies within
    24 business hours. Ask the customer for name and contact (phone or e-mail) before calling.

    Args:
        reason: complaint, human_request or other.
        customer_name: Customer's name.
        contact: Phone or e-mail for the team to reply.
        summary: Short summary of the issue in Portuguese.
        order_id: Related order number, if any.
    """
    with storage.connect() as conn:
        ticket_id = storage.create_ticket(conn, reason, customer_name, contact, summary, order_id)
    return {"protocolo": f"EM-{ticket_id:05d}", "prazo_retorno": "até 24 horas úteis"}


TOOLS = [
    search_products,
    get_product_details,
    calculate_payment_options,
    get_order_status,
    list_active_promotions,
    get_store_policy,
    create_support_ticket,
]
TOOLS_BY_NAME = {t.__name__: t for t in TOOLS}
