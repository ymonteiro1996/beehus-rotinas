from flask import Blueprint, render_template, jsonify, request
from db import db, get_biz_dates, get_company_filter
from bson import ObjectId as _OID
import json, os

bp = Blueprint("repeticao", __name__)

TEMPLATES_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "repeticao_templates.json")


def _load_templates():
    if not os.path.exists(TEMPLATES_FILE):
        return []
    with open(TEMPLATES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_templates(templates):
    with open(TEMPLATES_FILE, "w", encoding="utf-8") as f:
        json.dump(templates, f, indent=2, ensure_ascii=False)


@bp.route("/repeticao")
def index():
    return render_template("repeticao.html")


@bp.route("/api/repeticao/companies")
def get_companies():
    companies = sorted(
        [{"id": str(c["_id"]), "name": c.get("name", "")}
         for c in db.companies.find({}, {"name": 1})],
        key=lambda c: c["name"],
    )
    cf = get_company_filter()
    if cf:
        companies = [c for c in companies if c["id"] in cf]
    return jsonify(companies)


@bp.route("/api/repeticao/wallets")
def get_wallets():
    company_id = request.args.get("companyId", "").strip()
    if not company_id:
        return jsonify([])
    wallets = list(db.wallets.find(
        {"companyId": company_id},
        {"name": 1, "accountCode": 1, "currency": 1}
    ).sort("name", 1))
    return jsonify([
        {"id": str(w["_id"]), "name": w.get("name", ""),
         "accountCode": w.get("accountCode", ""),
         "currency": w.get("currency", "")}
        for w in wallets
    ])


# ── Templates ────────────────────────────────────────────────────────────────

@bp.route("/api/repeticao/templates")
def get_templates():
    return jsonify(_load_templates())


@bp.route("/api/repeticao/templates", methods=["POST"])
def save_template():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Nome obrigatório"}), 400
    template = {
        "name":    name,
        "wallets": data.get("wallets", []),
    }
    templates = _load_templates()
    for t in templates:
        if t.get("name") == name:
            t["wallets"] = template["wallets"]
            _save_templates(templates)
            return jsonify({"ok": True, "overwritten": True})
    templates.append(template)
    _save_templates(templates)
    return jsonify({"ok": True, "overwritten": False})


@bp.route("/api/repeticao/templates/<name>", methods=["DELETE"])
def delete_template(name):
    templates = _load_templates()
    _save_templates([t for t in templates if t.get("name") != name])
    return jsonify({"ok": True})


# ── Date cards ───────────────────────────────────────────────────────────────

@bp.route("/api/repeticao/date-cards", methods=["POST"])
def date_cards():
    """Return last 10 biz dates with counts of unprocessed, processed, and total wallets."""
    data = request.get_json() or {}
    wallet_ids = data.get("walletIds", [])
    limit      = int(data.get("limit", 10))

    if not wallet_ids:
        return jsonify({"cards": [], "dates": []})

    dates = get_biz_dates(limit)
    total_wallets = len(wallet_ids)

    # Count unprocessedSecurityPositions per date (distinct walletIds that have a doc)
    unproc = {}
    for doc in db.unprocessedSecurityPositions.aggregate([
        {"$match": {"walletId": {"$in": wallet_ids}, "positionDate": {"$in": dates}}},
        {"$group": {"_id": "$positionDate", "n": {"$sum": 1}}},
    ]):
        unproc[str(doc["_id"])[:10]] = doc["n"]

    # Count processedPosition per date (distinct walletIds)
    proc = {}
    for doc in db.processedPosition.aggregate([
        {"$match": {"walletId": {"$in": wallet_ids}, "positionDate": {"$in": dates}}},
        {"$group": {"_id": "$positionDate", "n": {"$sum": 1}}},
    ]):
        proc[str(doc["_id"])[:10]] = doc["n"]

    cards = []
    suggested_to = None    # most recent incomplete (for dateTo)
    suggested_from = None  # most recent all-green (for dateFrom)
    for d in dates:
        u = unproc.get(d, 0)
        p = proc.get(d, 0)
        cards.append({
            "date":        d,
            "unprocessed": u,
            "processed":   p,
            "total":       total_wallets,
        })
        if u < total_wallets or p < total_wallets:
            suggested_to = d
        if u == total_wallets and p == total_wallets:
            suggested_from = d

    return jsonify({"cards": cards, "suggestedFrom": suggested_from, "suggestedTo": suggested_to})


# ── Generate ─────────────────────────────────────────────────────────────────

@bp.route("/api/repeticao/generate", methods=["POST"])
def generate():
    """Generate position clone data for clipboard."""
    data = request.get_json() or {}
    wallet_ids = data.get("walletIds", [])
    date_from  = data.get("dateFrom", "").strip()
    date_to    = data.get("dateTo", "").strip()

    if not wallet_ids or not date_from or not date_to:
        return jsonify({"error": "Parâmetros obrigatórios: walletIds, dateFrom, dateTo"}), 400

    # Pre-load wallet currencies (try both string and ObjectId)
    wallet_currency = {}
    id_query = list(wallet_ids)
    for wid in wallet_ids:
        try:
            id_query.append(_OID(wid))
        except Exception:
            pass
    for w in db.wallets.find({"_id": {"$in": id_query}}, {"currency": 1}):
        wallet_currency[str(w["_id"])] = w.get("currency", "")

    rows = []

    for wid in wallet_ids:
        currency = wallet_currency.get(wid, "")

        # unprocessedSecurityPositions: one doc per wallet/date with securities array
        pos_doc = db.unprocessedSecurityPositions.find_one(
            {"walletId": wid, "positionDate": date_from}
        )
        if pos_doc:
            for sec in pos_doc.get("securities", []):
                rows.append({
                    "data":       date_to,
                    "carteira":   wid,
                    "ativo":      sec.get("unprocessedId", ""),
                    "quant":      sec.get("quantity", 0),
                    "pu":         sec.get("pu", 0),
                    "saldoBruto": sec.get("balance", 0),
                    "caixa":      "Não",
                    "moeda":      currency,
                })

        for ca in db.cashAccounts.find({"walletId": wid}):
            ca_currency = ca.get("currency", currency)
            unproc_id   = ca.get("unprocessedId", "")
            for entry in ca.get("values", []):
                entry_date = str(entry.get("date", ""))[:10]
                if entry_date == date_from:
                    val = entry.get("value")
                    if val is not None:
                        rows.append({
                            "data":       date_to,
                            "carteira":   wid,
                            "ativo":      unproc_id,
                            "quant":      0,
                            "pu":         0,
                            "saldoBruto": val,
                            "caixa":      "Sim",
                            "moeda":      ca_currency,
                        })

    # Resolve companyId from the first wallet
    company_id = ""
    if wallet_ids:
        w_doc = db.wallets.find_one({"_id": {"$in": id_query[:len(wallet_ids)*2]}}, {"companyId": 1})
        if w_doc:
            company_id = w_doc.get("companyId", "")

    return jsonify({"rows": rows, "dateFrom": date_from, "dateTo": date_to, "companyId": company_id})
