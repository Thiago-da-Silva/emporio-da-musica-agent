from datetime import date, datetime

import pytest

from emporio_agent import clock, storage, tools
from emporio_agent.data import get_store
from emporio_agent.schema import declaration_for


def names(result):
    return [p["nome"] for p in result["produtos"]]


# --- catalog -----------------------------------------------------------------

def test_search_by_model_name():
    result = tools.search_products("Takamine GD20")
    assert names(result) == ["Takamine GD20 Dreadnought Natural"]
    assert result["produtos"][0]["preco_tabela"] == 2199.0


def test_search_category_and_max_price_only_returns_available_items():
    result = tools.search_products(category="Violões", max_price=1000)
    store = get_store()
    for item in result["produtos"]:
        product = store.products[item["product_id"]]
        assert product.available
        assert product.price <= 1000
    # Out of stock (96) and discontinued (113) are hidden by default
    ids = {p["product_id"] for p in result["produtos"]}
    assert 96 not in ids and 113 not in ids


def test_search_plural_query_matches_category():
    assert tools.search_products("violões nylon")["total_encontrado"] > 0


def test_max_price_uses_promotional_price():
    # Crafter HT-100 costs 2399 but has an active 18% promotion (1967.18)
    result = tools.search_products(min_price=1900, max_price=2000, category="Violões")
    ids = {p["product_id"] for p in result["produtos"]}
    assert 90 in ids


def test_unavailable_product_shows_status_and_alternatives():
    details = tools.get_product_details(96)
    assert details["disponibilidade"] == "esgotado no momento"
    assert details["alternativas_disponiveis"]
    assert all(a["disponibilidade"].startswith("disponível") for a in details["alternativas_disponiveis"])


def test_discontinued_and_coming_soon_labels():
    assert tools.get_product_details(113)["disponibilidade"].startswith("descontinuado")
    assert tools.get_product_details(130)["disponibilidade"].startswith("em breve")


def test_inconsistent_description_is_hidden():
    # "Music Man Bass 1X" has a description about a Fender Jazz Bass
    assert "indisponível" in tools.get_product_details(135)["descricao"]


def test_only_active_promotions_are_listed():
    ids = {p["product_id"] for p in tools.list_active_promotions()["promocoes_ativas"]}
    assert ids == {127, 121, 90, 94}


# --- payment rules -----------------------------------------------------------

def test_pix_discount_on_regular_price():
    options = tools.calculate_payment_options(95)  # 2199.00, no promotion
    assert options["pix"]["total"] == pytest.approx(2089.05)
    assert options["cartao_credito_sem_juros"]["max_parcelas"] == 12
    assert options["permite_combinar_formas_pagamento"] is True


def test_pix_discount_does_not_stack_with_promotion():
    options = tools.calculate_payment_options(90)  # 2399 with 18% off
    assert options["total"] == pytest.approx(1967.18)
    assert options["pix"]["total"] == pytest.approx(1967.18)
    assert options["permite_combinar_formas_pagamento"] is False


@pytest.mark.parametrize("total, expected", [
    (90, 1),      # 2x would be 45 < 50
    (159.9, 3),   # 3x = 53.30; 4x would be 39.98 < 80
    (480, 6),     # 6x = 80
    (599.9, 6),   # 7x = 85.70 < 100, so 6x (99.98 >= 80)
    (1200, 12),
])
def test_installment_brackets(total, expected):
    assert tools._max_installments(total) == expected


# --- orders ------------------------------------------------------------------

def test_order_requires_matching_contact():
    denied = tools.get_order_status(8, "someone@else.com")
    assert denied["verificado"] is False
    assert "Kala" not in str(denied)


def test_order_with_email_and_phone():
    by_email = tools.get_order_status(8, "AnaCarol.Ferreira@coldmail.com ")
    by_phone = tools.get_order_status(8, "(67) 99654-7821")
    assert by_email["verificado"] and by_phone["verificado"]
    assert by_email["codigo_rastreio"] == "BRJL5544332BR"


def test_nonexistent_order_gives_same_message_as_wrong_contact():
    assert tools.get_order_status(999, "x@y.com") == tools.get_order_status(8, "x@y.com")


def test_shipped_order_past_estimate_is_flagged(monkeypatch):
    monkeypatch.setattr(clock, "today", lambda: date(2026, 3, 5))
    result = tools.get_order_status(8, "anacarol.ferreira@coldmail.com")
    assert "atrasada em 5 dias" in result["alerta"]


def test_delivered_order_reports_days_since_delivery(monkeypatch):
    monkeypatch.setattr(clock, "today", lambda: date(2026, 2, 21))
    customer = get_store().customers[14]
    result = tools.get_order_status(7, customer.email)
    assert result["dias_desde_entrega"] == 4


# --- policies and tickets ----------------------------------------------------

def test_policy_topics():
    assert "09:00 às 18:00" in tools.get_store_policy("opening_hours")["texto"]
    assert "7 (sete) dias" in tools.get_store_policy("returns_exchanges")["texto"]
    assert "erro" in tools.get_store_policy("nope")


def test_support_ticket(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test.sqlite3")
    ticket = tools.create_support_ticket("complaint", "Ana", "ana@x.com", "Atraso na entrega", 8)
    assert ticket["protocolo"] == "EM-00001"


# --- store hours -------------------------------------------------------------

def at(*args):
    return datetime(*args, tzinfo=clock.STORE_TZ)


def test_store_status():
    assert "ABERTA" in clock.store_status(at(2026, 11, 3, 10, 0))           # Tuesday
    assert "Reabre amanhã" in clock.store_status(at(2026, 11, 2, 10, 0))     # holiday (Finados)
    assert "segunda-feira" in clock.store_status(at(2026, 11, 7, 14, 0))     # Saturday afternoon
    assert "Abre hoje às 09:00" in clock.store_status(at(2026, 11, 4, 7, 0))


# --- function-calling schema -------------------------------------------------

def test_every_tool_has_a_valid_declaration():
    for func in tools.TOOLS:
        declaration = declaration_for(func)
        assert declaration.description
        for prop in declaration.parameters_json_schema["properties"].values():
            assert prop.get("description"), f"{func.__name__} has an undocumented argument"
