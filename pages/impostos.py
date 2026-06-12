from flask import Blueprint, render_template, jsonify, request
from db import db, get_company_filter
from bson import ObjectId

bp = Blueprint("impostos", __name__)


def _get_most_recent_position(wallet_id):
    """Get the most recent processedPosition for a wallet."""
    or_q = [{"walletId": wallet_id}]
    try:
        or_q.append({"walletId": ObjectId(wallet_id)})
    except Exception:
        pass

    pos_doc = next(iter(
        db.processedPosition.find(
            {"$or": or_q},
            {"securities": 1, "positionDate": 1}
        ).sort("positionDate", -1).limit(1)
    ), None)

    if not pos_doc:
        return None, None
    return pos_doc, str(pos_doc.get("positionDate", ""))[:10]


# ── Page ───────────────────────────────────────────────────────────────────────

@bp.route("/impostos")
def index():
    return render_template("impostos.html")


# ── API: Companies & Wallets ──────────────────────────────────────────────────

@bp.route("/api/impostos/companies")
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


@bp.route("/api/impostos/wallets")
def get_wallets():
    company_id = request.args.get("companyId", "").strip()
    if not company_id:
        return jsonify([])
    wallets = list(db.wallets.find(
        {"companyId": company_id},
        {"name": 1, "accountCode": 1}
    ).sort("name", 1))
    return jsonify([
        {"id": str(w["_id"]), "name": w.get("name", ""), "accountCode": w.get("accountCode", "")}
        for w in wallets
    ])


# ── API: Securities in wallet ─────────────────────────────────────────────────

@bp.route("/api/impostos/wallet-securities")
def get_wallet_securities():
    wallet_id = request.args.get("walletId", "").strip()
    if not wallet_id:
        return jsonify({"error": "walletId obrigatório"}), 400

    pos_doc, position_date = _get_most_recent_position(wallet_id)
    if not pos_doc:
        return jsonify({"securities": [], "positionDate": None})

    raw_secs = pos_doc.get("securities", [])

    # Batch-fetch security metadata
    oid_ids = []
    for s in raw_secs:
        sid = s.get("securityId")
        if sid:
            try:
                oid_ids.append(ObjectId(str(sid)))
            except Exception:
                pass

    sec_meta = {}
    for sec in db.securities.find({"_id": {"$in": oid_ids}}, {
        "beehusName": 1, "mainId": 1, "securityType": 1, "currency": 1,
        "indexer": 1, "maturityDate": 1,
    }):
        sec_meta[str(sec["_id"])] = {
            "beehusName":   sec.get("beehusName", ""),
            "mainId":       sec.get("mainId", ""),
            "securityType": sec.get("securityType", ""),
            "currency":     sec.get("currency", ""),
            "indexer":      sec.get("indexer", ""),
            "maturityDate": str(sec.get("maturityDate", ""))[:10] if sec.get("maturityDate") else None,
        }

    result = []
    for s in raw_secs:
        sid = str(s.get("securityId", ""))
        qty = s.get("quantity")
        pu  = s.get("pu")
        if qty is None or pu is None:
            continue
        try:
            q = float(qty)
            p = float(pu)
        except (ValueError, TypeError):
            continue
        if q == 0:
            continue

        meta = sec_meta.get(sid, {})
        balance = round(q * p, 2)

        result.append({
            "securityId":   sid,
            "beehusName":   meta.get("beehusName", sid),
            "mainId":       meta.get("mainId", ""),
            "securityType": meta.get("securityType", ""),
            "currency":     meta.get("currency", ""),
            "indexer":      meta.get("indexer", ""),
            "maturityDate": meta.get("maturityDate"),
            "pricingType":  s.get("pricingType", ""),
            "quantity":     q,
            "pu":           p,
            "balance":      balance,
            # Tax fields — to be implemented
            "tax":          None,
            "netBalance":   None,
        })

    result.sort(key=lambda x: (x["beehusName"] or "").lower())
    return jsonify({"securities": result, "positionDate": position_date, "walletId": wallet_id})
