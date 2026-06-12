from flask import Blueprint, render_template, jsonify, request
from db import db, get_company_filter
from bson import ObjectId
from datetime import date as _date, timedelta
import re

bp = Blueprint("precos", __name__)


@bp.route("/precos")
def index():
    return render_template("precos.html")


@bp.route("/api/precos/search")
def search_securities():
    q          = request.args.get("q", "").strip()
    sec_type   = request.args.get("securityType", "").strip()
    currency   = request.args.get("currency", "").strip()

    if len(q) < 2:
        return jsonify({"results": []})

    escaped_q = re.escape(q)
    or_clauses = [
        {"beehusName": {"$regex": escaped_q, "$options": "i"}},
        {"mainId":     {"$regex": escaped_q, "$options": "i"}},
    ]
    try:
        or_clauses.append({"_id": ObjectId(q)})
    except Exception:
        pass

    query = {"$or": or_clauses}
    if sec_type:
        query["securityType"] = sec_type
    if currency:
        query["currency"] = currency

    results = []
    for sec in db.securities.find(query, {
        "beehusName": 1, "mainId": 1, "securityType": 1, "currency": 1,
    }).limit(30):
        results.append({
            "id":           str(sec["_id"]),
            "beehusName":   sec.get("beehusName", ""),
            "mainId":       sec.get("mainId", ""),
            "securityType": sec.get("securityType", ""),
            "currency":     sec.get("currency", ""),
        })
    return jsonify({"results": results})


@bp.route("/api/precos/filters")
def get_filters():
    """Return distinct securityType and currency for filter dropdowns."""
    types      = sorted([t for t in db.securities.distinct("securityType") if t])
    currencies = sorted([c for c in db.securities.distinct("currency") if c])
    return jsonify({"securityTypes": types, "currencies": currencies})


@bp.route("/api/precos/price-sources")
def get_price_sources():
    """For a given securityId, return all available price docs (sources).
    Each doc = a unique (type, walletId, companyId) combination.
    """
    sec_id = request.args.get("securityId", "").strip()
    if not sec_id:
        return jsonify({"sources": []})

    # Match securityId as both string and ObjectId
    or_q = [{"securityId": sec_id}]
    try:
        or_q.append({"securityId": ObjectId(sec_id)})
    except Exception:
        pass

    docs = list(db.securityPrices.find(
        {"$or": or_q},
        {"type": 1, "walletId": 1, "companyId": 1, "entityId": 1, "historyPrice": {"$slice": -1}}
    ))

    # Enrich with wallet name and company name
    wallet_ids = set()
    company_ids = set()
    for d in docs:
        if d.get("walletId"):
            wallet_ids.add(str(d["walletId"]))
        if d.get("companyId"):
            company_ids.add(str(d["companyId"]))

    wallet_names = {}
    for w in db.wallets.find({"_id": {"$in": [ObjectId(wid) for wid in wallet_ids if wid]}}, {"name": 1}):
        wallet_names[str(w["_id"])] = w.get("name", "")
    # Also try string match
    for w in db.wallets.find({"_id": {"$in": list(wallet_ids)}}, {"name": 1}):
        wallet_names[str(w["_id"])] = w.get("name", "")

    company_names = {}
    for c in db.companies.find({}, {"name": 1}):
        company_names[str(c["_id"])] = c.get("name", "")

    # Apply company filter
    cf = get_company_filter()

    sources = []
    for d in docs:
        cid = str(d.get("companyId") or "")
        if cf and cid and cid not in cf:
            continue
        wid = str(d.get("walletId") or "")
        # Get last date from historyPrice
        hp = d.get("historyPrice", [])
        last_date = ""
        if isinstance(hp, list) and hp:
            last_date = str(hp[-1].get("date", ""))[:10]
        elif isinstance(hp, dict):
            last_date = str(hp.get("date", ""))[:10]

        sources.append({
            "priceDocId":  str(d["_id"]),
            "type":        d.get("type", ""),
            "walletId":    wid,
            "walletName":  wallet_names.get(wid, ""),
            "companyId":   cid,
            "companyName": company_names.get(cid, ""),
            "lastDate":    last_date,
        })

    sources.sort(key=lambda s: (s["type"], s["companyName"], s["walletName"]))
    return jsonify({"sources": sources})


@bp.route("/api/precos/history")
def get_price_history():
    """Return price history for selected price doc IDs within a date range."""
    doc_ids   = request.args.getlist("docId")
    date_from = request.args.get("dateFrom", "").strip()
    date_to   = request.args.get("dateTo", "").strip()

    if not doc_ids:
        return jsonify({"series": [], "dates": []})

    # _id can be ObjectId or string (UUID) — query with both
    id_list = []
    for did in doc_ids:
        id_list.append(did)  # string
        try:
            id_list.append(ObjectId(did))
        except Exception:
            pass

    if not id_list:
        return jsonify({"series": [], "dates": []})

    docs = list(db.securityPrices.find({"_id": {"$in": id_list}}, {"historyPrice": 1, "type": 1, "walletId": 1, "companyId": 1}))

    # Enrich names
    wallet_ids = set()
    company_ids = set()
    for d in docs:
        if d.get("walletId"):
            wallet_ids.add(str(d["walletId"]))
        if d.get("companyId"):
            company_ids.add(str(d["companyId"]))

    wallet_names = {}
    for w in db.wallets.find({"_id": {"$in": [ObjectId(wid) for wid in wallet_ids if wid]}}, {"name": 1}):
        wallet_names[str(w["_id"])] = w.get("name", "")

    company_names = {}
    for c in db.companies.find({}, {"name": 1}):
        company_names[str(c["_id"])] = c.get("name", "")

    # Collect all dates and values
    all_dates = set()
    series = []

    for d in docs:
        hp_raw = d.get("historyPrice", [])
        if isinstance(hp_raw, dict):
            hp_raw = [hp_raw]

        values = {}
        for hp in hp_raw:
            dt = str(hp.get("date", ""))[:10]
            if not dt:
                continue
            if date_from and dt < date_from:
                continue
            if date_to and dt > date_to:
                continue
            values[dt] = hp.get("value")
            all_dates.add(dt)

        wid = str(d.get("walletId") or "")
        cid = str(d.get("companyId") or "")
        label = d.get("type", "")
        if company_names.get(cid):
            label += f" · {company_names[cid]}"
        if wallet_names.get(wid):
            label += f" · {wallet_names[wid]}"
        if not label:
            label = str(d["_id"])

        series.append({
            "docId":  str(d["_id"]),
            "label":  label,
            "values": values,
        })

    sorted_dates = sorted(all_dates)
    return jsonify({"series": series, "dates": sorted_dates})
