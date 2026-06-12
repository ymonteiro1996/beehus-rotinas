from pymongo import MongoClient
from datetime import date, timedelta
import json, os, certifi

DB_NAME                = "Beehus"
CONFIG_FILE            = os.path.join(os.path.dirname(__file__), "data", "config.json")
SETTINGS_FILE          = os.path.join(os.path.dirname(__file__), "data", "settings.json")
DEFAULT_BLACKLIST_FILE = os.path.join(os.path.dirname(__file__), "data", "default_blacklist.json")
NAV_SETTINGS_FILE      = os.path.join(os.path.dirname(__file__), "data", "nav_settings.json")
USER_CONNECTIONS_FILE  = os.path.join(os.path.dirname(__file__), "data", "user_connections.json")


# ── Windows user ────────────────────────────────────────────────────────────

def get_windows_user():
    """Returns the current Windows username in lowercase."""
    return os.environ.get("USERNAME", "unknown").lower()


# ── Per-user connection storage ─────────────────────────────────────────────

def load_user_connections():
    if not os.path.exists(USER_CONNECTIONS_FILE):
        return {}
    with open(USER_CONNECTIONS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_user_connections(conns):
    with open(USER_CONNECTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(conns, f, indent=2)


# ── DB proxy ─────────────────────────────────────────────────────────────────
# A single object whose internal reference can be swapped after registration,
# so all existing `from db import db` imports see the live database immediately.

class _DbProxy:
    def __init__(self):
        self._db = None

    def _init(self, mongo_db):
        self._db = mongo_db

    def _ready(self):
        return self._db is not None

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if self._db is None:
            raise RuntimeError("Database not initialized – please register at /setup.")
        return getattr(self._db, name)

    def __getitem__(self, name):
        if self._db is None:
            raise RuntimeError("Database not initialized – please register at /setup.")
        return self._db[name]


db     = _DbProxy()
client = None

# Try to connect for the current Windows user
_user  = get_windows_user()
_conns = load_user_connections()
if _user in _conns:
    client = MongoClient(_conns[_user], tlsCAFile=certifi.where())
    db._init(client[DB_NAME])

# Fallback: connect via MONGO_URI environment variable (Railway/cloud deployments)
if not db._ready():
    _uri_env = os.environ.get("MONGO_URI", "")
    if _uri_env:
        client = MongoClient(_uri_env, tlsCAFile=certifi.where())
        db._init(client[DB_NAME])


def ensure_indexes():
    """Create compound indexes for heavy queries (idempotent)."""
    if not db._ready():
        return
    try:
        db.unprocessedSecurityPositions.create_index(
            [("walletId", 1), ("positionDate", 1)])
        db.processedPosition.create_index(
            [("walletId", 1), ("positionDate", 1)])
        db.navPackages.create_index(
            [("walletId", 1), ("positionDate", 1), ("trashed", 1)])
        db.wallets.create_index(
            [("companyId", 1), ("entityId", 1)])
        db.transactions.create_index(
            [("walletId", 1), ("liquidationDate", 1)])
        db.issues.create_index(
            [("walletId", 1), ("date", 1), ("type", 1), ("status", 1)])
        db.issues.create_index(
            [("status", 1), ("date", 1)])
        db.provisions.create_index(
            [("walletId", 1), ("initialDate", 1), ("liquidationDate", 1)])
        db.cashAccounts.create_index(
            [("walletId", 1)])
    except Exception as exc:
        import logging
        logging.warning("ensure_indexes failed (non-critical): %s", exc)

ensure_indexes()


# ── Helpers ──────────────────────────────────────────────────────────────────

def valid_wallet_ids():
    """Returns the set of walletId strings that exist in the wallets collection."""
    return {str(w["_id"]) for w in db.wallets.find({}, {"_id": 1})}


def get_biz_dates(limit, end_date=None):
    """Last `limit` business days (Mon-Fri, excluding holidays) ending on end_date (or today), oldest → newest."""
    holidays = set(load_settings().get("holidays", []))
    result  = []
    current = date.fromisoformat(end_date) if end_date else date.today()
    while len(result) < limit:
        if current.weekday() < 5 and current.strftime("%Y-%m-%d") not in holidays:
            result.append(current.strftime("%Y-%m-%d"))
        current -= timedelta(days=1)
    return list(reversed(result))


def get_biz_dates_range(start_date, end_date):
    """All business days (Mon-Fri, excluding holidays) between start_date and end_date inclusive, oldest → newest."""
    holidays = set(load_settings().get("holidays", []))
    start   = date.fromisoformat(start_date)
    end     = date.fromisoformat(end_date)
    result  = []
    current = start
    while current <= end:
        if current.weekday() < 5 and current.strftime("%Y-%m-%d") not in holidays:
            result.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return result


def load_config_full():
    """Read config.json once; return (selected, delays, methods, responsible)."""
    if not os.path.exists(CONFIG_FILE):
        return set(), {}, {}, {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        items = json.load(f)
    selected    = {(i["companyId"], i["entityId"]) for i in items}
    delays      = {(i["companyId"], i["entityId"]): int(i.get("delay", 0))   for i in items}
    methods     = {(i["companyId"], i["entityId"]): i.get("method", "")      for i in items}
    responsible = {(i["companyId"], i["entityId"]): i.get("responsible", "") for i in items}
    return selected, delays, methods, responsible


def load_config():
    selected, _, _, _ = load_config_full()
    return selected


def load_config_delays():
    """Returns {(companyId, entityId): delay_in_biz_days}"""
    _, delays, _, _ = load_config_full()
    return delays


def load_config_methods():
    """Returns {(companyId, entityId): method_string}"""
    _, _, methods, _ = load_config_full()
    return methods


def load_config_responsible():
    """Returns {(companyId, entityId): responsible_string}"""
    _, _, _, responsible = load_config_full()
    return responsible


def _load_default_blacklist():
    try:
        with open(DEFAULT_BLACKLIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

def load_settings():
    defaults = {"only_daily_position": False, "only_with_consumption": False,
                "wizard_blacklist": _load_default_blacklist(), "company_filter": []}
    if not os.path.exists(SETTINGS_FILE):
        return defaults
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("wizard_blacklist", _load_default_blacklist())
    return data


def load_nav_settings():
    defaults = {"only_daily_position": False, "only_with_consumption": False}
    if not os.path.exists(NAV_SETTINGS_FILE):
        return defaults
    with open(NAV_SETTINGS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("only_daily_position", False)
    data.setdefault("only_with_consumption", False)
    return data


def get_company_filter():
    """Returns set of visible company ID strings. Empty set means show all."""
    return set(load_settings().get("company_filter", []))


def wallet_filter_query(settings):
    """Build a MongoDB filter dict based on active settings toggles."""
    q = {}
    if settings.get("only_daily_position"):
        q["hasDailyPosition"] = True
    if settings.get("only_with_consumption"):
        q["consumptionIdentifiers"] = {"$exists": True, "$not": {"$size": 0}}
    return q


# ── Shared helpers (dashboard / nav) ─────────────────────────────────────────

def biz_days_elapsed(date_str):
    """Count business days from date_str up to (and including) today."""
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        return 0
    today = date.today()
    if d >= today:
        return 0
    # Fast formula: count full weeks × 5, then add remaining weekdays
    delta = (today - d).days  # calendar days between d and today
    full_weeks, remainder = divmod(delta, 7)
    count = full_weeks * 5
    # Count remaining days after full weeks (from d's weekday forward)
    wd = d.weekday()  # 0=Mon
    for i in range(1, remainder + 1):
        if (wd + i) % 7 < 5:
            count += 1
    return count


def cell_cls(count, total, expected=True):
    if not expected:
        return "bg-gray-50 text-gray-300"
    if count == total:
        return "bg-green-100 text-green-700"
    if count > 0:
        return "bg-yellow-100 text-yellow-700"
    return "bg-red-100 text-red-600"


def wallet_cls(has_value):
    return "bg-green-50 text-green-700" if has_value else "bg-red-50 text-red-600"


def build_wallet_map(settings=None):
    """Returns (wallet_to_pair, pair_total) filtered by settings."""
    query = wallet_filter_query(settings or {})
    wallet_to_pair = {}
    pair_total     = {}
    for w in db.wallets.find(query, {"companyId": 1, "entityId": 1}):
        wid = str(w["_id"])
        cid = str(w.get("companyId", ""))
        eid = str(w.get("entityId", ""))
        if cid and eid:
            wallet_to_pair[wid] = (cid, eid)
            pair_total[(cid, eid)] = pair_total.get((cid, eid), 0) + 1
    return wallet_to_pair, pair_total
