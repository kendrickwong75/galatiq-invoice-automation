"""Business rules and runtime settings, kept in one place so policy changes are one-line edits."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "inventory.db"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"

# --- Approval policy -------------------------------------------------------
VP_REVIEW_THRESHOLD_USD = 10_000.00  # case brief: invoices over $10K need VP-level scrutiny
NEAR_THRESHOLD_BAND = 0.95  # amounts in [95%, 100%] of the threshold get a NEAR_THRESHOLD flag
STRONG_FRAUD_SIGNALS_TO_REJECT = 2  # this many independent strong fraud signals reject outright

# --- Validation tolerances ------------------------------------------------
MONEY_TOLERANCE = 0.01  # cents
PRICE_VARIANCE_TOLERANCE = 0.10  # flag unit prices more than 10% above catalog

# --- Simulated FX service (static rates to USD) ----------------------------
# A real deployment would call a treasury/FX API; the case brief asks us to simulate external services.
FX_RATES_TO_USD = {
    "USD": 1.00,
    "EUR": 1.08,
    "GBP": 1.27,
}

# --- Agent loop limits -----------------------------------------------------
MAX_EXTRACTION_ATTEMPTS = 3
MAX_REFLECTION_ROUNDS = 2
MAX_VALIDATOR_TURNS = 4
GRAPH_RECURSION_LIMIT = 60

# --- Seed data -------------------------------------------------------------
# Stock levels are exactly the case brief's. unit_price is our extension for the catalog price check.
INVENTORY_SEED = [
    ("WidgetA", 15, 250.00),
    ("WidgetB", 10, 500.00),
    ("GadgetX", 5, 750.00),
    ("FakeItem", 0, None),
]

SUPPORTED_EXTENSIONS = {".txt", ".pdf", ".json", ".csv", ".xml"}

# --- LLM providers -----------------------------------------------------------
DEFAULT_PROVIDER = "grok"
DEFAULT_GROK_MODEL = "grok-4.7"  # the case brief names grok-3, which xAI no longer lists
