"""Loads the store CSVs into typed, cleaned in-memory records.

The dataset is tiny (65 products, 20 orders), so everything is loaded once
into dictionaries. Cleaning rules are applied here so the tools and the LLM
never see the raw inconsistencies (see README, "Data treatment").
"""

import csv
import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Brands that appear in the catalog. Used to detect descriptions that talk
# about a different brand than the product name (a known data issue).
KNOWN_BRANDS = {
    "yamaha", "tagima", "giannini", "rozini", "shelby", "crafter", "martin",
    "taylor", "takamine", "kala", "ohana", "kalani", "fender", "gibson",
    "ibanez", "prs", "music man", "pearl", "tama", "korg", "nord", "roland",
}


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse spaces."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return text.strip()


def digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


@dataclass
class Category:
    id: int
    name: str
    description: str


@dataclass
class Product:
    id: int
    name: str
    category_id: int
    price: float
    description: str | None
    stock: int
    status: str  # active | discontinued | coming_soon
    specs: dict = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.status == "active" and self.stock > 0


@dataclass
class Promotion:
    id: int
    product_id: int
    discount_percent: float
    description: str


@dataclass
class Customer:
    id: int
    name: str
    phone: str
    email: str
    city: str


@dataclass
class OrderItem:
    product_id: int
    quantity: int


@dataclass
class Order:
    id: int
    customer_id: int
    date: str
    status: str
    total: float
    payment_method: str
    tracking_code: str | None
    estimated_delivery: str | None
    notes: str | None
    items: list[OrderItem] = field(default_factory=list)


@dataclass
class Store:
    categories: dict[int, Category]
    products: dict[int, Product]
    promotions: dict[int, Promotion]  # active promotions only, keyed by product_id
    customers: dict[int, Customer]
    orders: dict[int, Order]


def _read(name: str) -> list[dict]:
    with open(DATA_DIR / f"{name}.csv", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _clean_description(name: str, description: str) -> str | None:
    """Drop descriptions that mention a brand other than the product's own.

    Products 135-144 have generic names ("Music Man Bass 1X") and descriptions
    copied from other brands ("Fender Jazz Bass..."). Showing them would make
    the agent state wrong facts, so we keep only name + specs for those.
    """
    own = {b for b in KNOWN_BRANDS if re.search(rf"\b{b}\b", normalize(name))}
    mentioned = {b for b in KNOWN_BRANDS if re.search(rf"\b{b}\b", normalize(description))}
    if mentioned - own:
        return None
    return description.strip()


def load_store() -> Store:
    categories = {
        int(r["category_id"]): Category(int(r["category_id"]), r["name"], r["description"])
        for r in _read("categories")
    }

    products = {}
    for r in _read("products"):
        products[int(r["product_id"])] = Product(
            id=int(r["product_id"]),
            name=r["name"].strip(),
            category_id=int(r["category_id"]),
            price=float(r["price_brl"]),
            description=_clean_description(r["name"], r["description"]),
            stock=int(r["stock_quantity"] or 0),
            status=r["status"].strip(),
            specs=json.loads(r["specs"]) if r["specs"] else {},
        )

    # Only active promotions matter; inactive ones must never be offered.
    # If a product ever had two active promotions, keep the biggest discount
    # (promotions are not cumulative).
    promotions: dict[int, Promotion] = {}
    for r in _read("promotions"):
        if r["is_active"].strip() != "1":
            continue
        promo = Promotion(int(r["promotion_id"]), int(r["product_id"]),
                          float(r["discount_percent"]), r["description"])
        current = promotions.get(promo.product_id)
        if current is None or promo.discount_percent > current.discount_percent:
            promotions[promo.product_id] = promo

    customers = {
        int(r["customer_id"]): Customer(int(r["customer_id"]), r["name"], r["phone"],
                                        r["email"].strip().lower(), r["city"])
        for r in _read("customers")
    }

    orders = {}
    for r in _read("orders"):
        orders[int(r["order_id"])] = Order(
            id=int(r["order_id"]),
            customer_id=int(r["customer_id"]),
            date=r["order_date"],
            status=r["status"].strip(),
            # The stored total is what the customer paid; item prices may have
            # changed since, so we never recompute it from the catalog.
            total=float(r["total_brl"]),
            payment_method=r["payment_method"],
            tracking_code=r["tracking_code"] or None,
            estimated_delivery=r["estimated_delivery"] or None,
            notes=r["notes"] or None,
        )
    for r in _read("order_items"):
        orders[int(r["order_id"])].items.append(OrderItem(int(r["product_id"]), int(r["quantity"])))

    return Store(categories, products, promotions, customers, orders)


@lru_cache(maxsize=1)
def get_store() -> Store:
    return load_store()
