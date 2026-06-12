from flask import Blueprint, render_template, jsonify, request
from bson import ObjectId
from db import (db, get_biz_dates, get_biz_dates_range, load_config_full, load_settings,
                wallet_filter_query, get_company_filter,
                biz_days_elapsed as _biz_days_elapsed,
                cell_cls as _cell_cls, wallet_cls as _wallet_cls,
                build_wallet_map as _build_wallet_map)
import json, os

bp = Blueprint("dashboard", __name__)

WALLET_TEMPLATES_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "wallet_templates.json")
WALLET_COMMENTS_FILE  = os.path.join(os.path.dirname(__file__), "..", "data", "wallet_comments.json")

def _load_wallet_templates():
    if not os.path.exists(WALLET_TEMPLATES_FILE):
        return []
    with open(WALLET_TEMPLATES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def _write_wallet_templates(templates):
    with open(WALLET_TEMPLATES_FILE, "w", encoding="utf-8") as f:
        json.dump(templates, f, indent=2, ensure_ascii=False)

def _load_wallet_comments():
    if not os.path.exists(WALLET_COMMENTS_FILE):
        return {}
    with open(WALLET_COMMENTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def _write_wallet_comments(comments):
    with open(WALLET_COMMENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(comments, f, indent=2, ensure_ascii=False)


def _count_collection(collection, dates, wallet_to_pair, pairs):
    """Count documents per (companyId-entityId pair, date) using aggregation."""
    relevant_wids = [wid for wid, pair in wallet_to_pair.items() if pair in pairs]
    if not relevant_wids:
        return {}
    pipeline = [
        {"$match": {"walletId": {"$in": relevant_wids}, "positionDate": {"$in": dates}}},
        {"$group": {"_id": {"w": "$walletId", "d": "$positionDate"}, "n": {"$sum": 1}}},
    ]
    counts = {}
    for doc in collection.aggregate(pipeline):
        wid  = str(doc["_id"]["w"])
        d    = str(doc["_id"]["d"])[:10]
        pair = wallet_to_pair.get(wid)
        if pair and pair in pairs:
            counts[(pair, d)] = counts.get((pair, d), 0) + doc["n"]
    return counts


# ── Routes ─────────────────────────────────────────────────────────────────────

@bp.route("/")
def index():
    return render_template("dashboard.html")


@bp.route("/api/rows")
def get_rows():
    limit         = int(request.args.get("limit", 10))
    dates         = get_biz_dates(limit)
    company_names = {str(c["_id"]): c.get("name", "") for c in db.companies.find({}, {"name": 1})}
    entity_names  = {str(e["_id"]): e.get("name", "") for e in db.entities.find({}, {"name": 1})}

    wallet_to_pair, pair_total = _build_wallet_map(load_settings())

    pairs = set(wallet_to_pair.values())
    selected, delays, methods, responsible = load_config_full()
    if selected:
        pairs = pairs & selected
    cf = get_company_filter()
    if cf:
        pairs = {p for p in pairs if p[0] in cf}

    elapsed = {d: _biz_days_elapsed(d) for d in dates}
    counts  = _count_collection(db.unprocessedSecurityPositions, dates, wallet_to_pair, pairs)

    rows = []
    for cid, eid in sorted(pairs, key=lambda p: (company_names.get(p[0], p[0]), entity_names.get(p[1], p[1]))):
        total = pair_total.get((cid, eid), 0)
        delay = delays.get((cid, eid), 0)
        cells = []
        for d in dates:
            count    = counts.get(((cid, eid), d), 0)
            expected = elapsed[d] >= delay
            cells.append({
                "label": f"{count}/{total}",
                "cls":   _cell_cls(count, total, expected),
            })
        rows.append({
            "companyId": cid,
            "entityId":  eid,
            "company":   company_names.get(cid, cid),
            "entity":    entity_names.get(eid, eid),
            "delay":     delay,
            "method":      methods.get((cid, eid), ""),
            "responsible": responsible.get((cid, eid), ""),
            "cells":     cells,
        })

    return jsonify({"rows": rows, "dates": dates})


@bp.route("/api/detail")
def get_detail():
    cid = request.args.get("companyId")
    eid = request.args.get("entityId")
    d   = request.args.get("date")

    wq = {"companyId": cid, "entityId": eid, **wallet_filter_query(load_settings())}
    wallets = {
        str(w["_id"]): {"name": w.get("name", str(w["_id"])), "accountCode": w.get("accountCode", "")}
        for w in db.wallets.find(wq, {"name": 1, "accountCode": 1})
    }

    counts = {wid: 0 for wid in wallets}
    for pos in db.unprocessedSecurityPositions.find(
        {"walletId": {"$in": list(wallets)}, "positionDate": d},
        {"walletId": 1}
    ):
        wid = str(pos.get("walletId", ""))
        if wid in counts:
            counts[wid] += 1

    detail = sorted([
        {
            "walletId":    wid,
            "name":        wallets[wid]["name"],
            "accountCode": wallets[wid]["accountCode"],
            "count":       counts[wid],
            "cls":         _wallet_cls(counts[wid]),
        }
        for wid in wallets
    ], key=lambda x: x["name"])

    return jsonify({"detail": detail, "date": d})


@bp.route("/api/detail-grid")
def get_detail_grid():
    cid   = request.args.get("companyId")
    eid   = request.args.get("entityId")
    limit = int(request.args.get("limit", 10))
    dates = get_biz_dates(limit)

    wq = {"companyId": cid, "entityId": eid, **wallet_filter_query(load_settings())}
    wallets = {
        str(w["_id"]): {"name": w.get("name", str(w["_id"])), "accountCode": w.get("accountCode", "")}
        for w in db.wallets.find(wq, {"name": 1, "accountCode": 1})
    }

    counts = {}
    for doc in db.unprocessedSecurityPositions.aggregate([
        {"$match": {"walletId": {"$in": list(wallets)}, "positionDate": {"$in": dates}}},
        {"$group": {"_id": {"w": "$walletId", "d": "$positionDate"}, "n": {"$sum": 1}}},
    ]):
        counts[(str(doc["_id"]["w"]), str(doc["_id"]["d"])[:10])] = doc["n"]

    rows = sorted([
        {
            "walletId":    wid,
            "name":        wallets[wid]["name"],
            "accountCode": wallets[wid]["accountCode"],
            "cells": [
                {"label": str(counts.get((wid, d), 0)), "cls": _wallet_cls(counts.get((wid, d), 0))}
                for d in dates
            ],
        }
        for wid in wallets
    ], key=lambda x: x["name"])

    return jsonify({"rows": rows, "dates": dates})


@bp.route("/api/processed/rows")
def get_processed_rows():
    limit         = int(request.args.get("limit", 10))
    dates         = get_biz_dates(limit)
    company_names = {str(c["_id"]): c.get("name", "") for c in db.companies.find({}, {"name": 1})}
    entity_names  = {str(e["_id"]): e.get("name", "") for e in db.entities.find({}, {"name": 1})}

    wallet_to_pair, pair_total = _build_wallet_map(load_settings())

    pairs = set(wallet_to_pair.values())
    selected, delays, methods, responsible = load_config_full()
    if selected:
        pairs = pairs & selected
    cf = get_company_filter()
    if cf:
        pairs = {p for p in pairs if p[0] in cf}

    elapsed = {d: _biz_days_elapsed(d) for d in dates}
    counts  = _count_collection(db.processedPosition, dates, wallet_to_pair, pairs)

    rows = []
    for cid, eid in sorted(pairs, key=lambda p: (company_names.get(p[0], p[0]), entity_names.get(p[1], p[1]))):
        total = pair_total.get((cid, eid), 0)
        delay = delays.get((cid, eid), 0)
        cells = []
        for d in dates:
            count    = counts.get(((cid, eid), d), 0)
            expected = elapsed[d] >= delay
            cells.append({
                "label": f"{count}/{total}",
                "cls":   _cell_cls(count, total, expected),
            })
        rows.append({
            "companyId":   cid,
            "entityId":    eid,
            "company":     company_names.get(cid, cid),
            "entity":      entity_names.get(eid, eid),
            "delay":       delay,
            "method":      methods.get((cid, eid), ""),
            "responsible": responsible.get((cid, eid), ""),
            "cells":       cells,
        })

    return jsonify({"rows": rows, "dates": dates})


@bp.route("/api/processed/detail")
def get_processed_detail():
    cid = request.args.get("companyId")
    eid = request.args.get("entityId")
    d   = request.args.get("date")

    wq = {"companyId": cid, "entityId": eid, **wallet_filter_query(load_settings())}
    wallets = {
        str(w["_id"]): {"name": w.get("name", str(w["_id"])), "accountCode": w.get("accountCode", "")}
        for w in db.wallets.find(wq, {"name": 1, "accountCode": 1})
    }

    wids_with_pp = {
        str(pos["walletId"])
        for pos in db.processedPosition.find(
            {"walletId": {"$in": list(wallets)}, "positionDate": d},
            {"walletId": 1}
        )
    }

    detail = sorted([
        {
            "walletId":    wid,
            "name":        wallets[wid]["name"],
            "accountCode": wallets[wid]["accountCode"],
            "count":       1 if wid in wids_with_pp else 0,
            "cls":         _wallet_cls(1 if wid in wids_with_pp else 0),
        }
        for wid in wallets
    ], key=lambda x: x["name"])

    return jsonify({"detail": detail, "date": d})


@bp.route("/api/processed/detail-grid")
def get_processed_detail_grid():
    cid   = request.args.get("companyId")
    eid   = request.args.get("entityId")
    limit = int(request.args.get("limit", 10))
    dates = get_biz_dates(limit)

    wq = {"companyId": cid, "entityId": eid, **wallet_filter_query(load_settings())}
    wallets = {
        str(w["_id"]): {"name": w.get("name", str(w["_id"])), "accountCode": w.get("accountCode", "")}
        for w in db.wallets.find(wq, {"name": 1, "accountCode": 1})
    }

    wids_by_date = {d: set() for d in dates}
    for doc in db.processedPosition.aggregate([
        {"$match": {"walletId": {"$in": list(wallets)}, "positionDate": {"$in": dates}}},
        {"$group": {"_id": {"w": "$walletId", "d": "$positionDate"}}},
    ]):
        wids_by_date.get(str(doc["_id"]["d"])[:10], set()).add(str(doc["_id"]["w"]))

    rows = sorted([
        {
            "walletId":    wid,
            "name":        wallets[wid]["name"],
            "accountCode": wallets[wid]["accountCode"],
            "cells": [
                {"label": "✓" if wid in wids_by_date[d] else "—",
                 "cls":   _wallet_cls(1 if wid in wids_by_date[d] else 0)}
                for d in dates
            ],
        }
        for wid in wallets
    ], key=lambda x: x["name"])

    return jsonify({"rows": rows, "dates": dates})


# ── Wallet-level view ─────────────────────────────────────────────────────────

@bp.route("/api/wallet/list")
def wallet_list():
    """Lightweight: returns wallets for a company (id, name, accountCode only)."""
    company_id = request.args.get("companyId", "").strip()
    entity_id  = request.args.get("entityId", "").strip()
    if not company_id:
        return jsonify([])
    wq = {"companyId": company_id, **wallet_filter_query(load_settings())}
    if entity_id:
        wq["entityId"] = entity_id
    wallets = sorted([
        {"walletId": str(w["_id"]), "name": w.get("name", str(w["_id"])), "accountCode": w.get("accountCode", "")}
        for w in db.wallets.find(wq, {"name": 1, "accountCode": 1})
    ], key=lambda w: w["name"])
    return jsonify(wallets)


@bp.route("/api/wallet/companies")
def wallet_companies():
    companies = sorted(
        [{"id": str(c["_id"]), "name": c.get("name", "")}
         for c in db.companies.find({}, {"name": 1})],
        key=lambda c: c["name"],
    )
    cf = get_company_filter()
    if cf:
        companies = [c for c in companies if c["id"] in cf]
    return jsonify(companies)


@bp.route("/api/wallet/entities")
def wallet_entities():
    company_id = request.args.get("companyId", "").strip()
    if not company_id:
        return jsonify([])
    wq = {"companyId": company_id, **wallet_filter_query(load_settings())}
    entity_ids = db.wallets.distinct("entityId", wq)
    oid_list = []
    for eid in entity_ids:
        if eid:
            try:
                oid_list.append(ObjectId(str(eid)))
            except Exception:
                pass
    entities = sorted(
        [{"id": str(e["_id"]), "name": e.get("name", "")}
         for e in db.entities.find({"_id": {"$in": oid_list}}, {"name": 1})],
        key=lambda e: e["name"],
    )
    return jsonify(entities)


@bp.route("/api/wallet/rows")
def get_wallet_rows():
    company_id = request.args.get("companyId", "").strip()
    entity_id  = request.args.get("entityId", "").strip()
    limit      = int(request.args.get("limit", 10))
    mode       = request.args.get("mode", "cargas").strip()
    start_date = request.args.get("startDate", "").strip()
    end_date   = request.args.get("endDate", "").strip()

    if not company_id:
        return jsonify({"rows": [], "dates": []})

    dates = get_biz_dates_range(start_date, end_date) if (start_date and end_date) else get_biz_dates(limit)

    wq = {"companyId": company_id, **wallet_filter_query(load_settings())}
    if entity_id:
        wq["entityId"] = entity_id

    wallets = {
        str(w["_id"]): {
            "name":        w.get("name", str(w["_id"])),
            "accountCode": w.get("accountCode", ""),
            "entityId":    str(w.get("entityId", "")),
        }
        for w in db.wallets.find(wq, {"name": 1, "accountCode": 1, "entityId": 1})
    }

    if not wallets:
        return jsonify({"rows": [], "dates": dates})

    entity_map = {
        str(e["_id"]): {"name": e.get("name", ""), "beehusName": e.get("beehusName", "")}
        for e in db.entities.find({}, {"name": 1, "beehusName": 1})
    }
    wid_list = list(wallets.keys())

    if mode in ("processed", "nav"):
        collection = db.processedPosition if mode == "processed" else db.navPackages
        match = {"walletId": {"$in": wid_list}, "positionDate": {"$in": dates}}
        if mode == "nav":
            match["trashed"] = {"$ne": True}
        wids_by_date = {d: set() for d in dates}
        for doc in collection.aggregate([
            {"$match": match},
            {"$group": {"_id": {"w": "$walletId", "d": "$positionDate"}}},
        ]):
            wids_by_date.get(str(doc["_id"]["d"])[:10], set()).add(str(doc["_id"]["w"]))

        rows = sorted([{
            "walletId": wid, "name": wallets[wid]["name"],
            "accountCode": wallets[wid]["accountCode"],
            "entity": entity_map.get(wallets[wid]["entityId"], {}).get("name", ""),
            "institution": (entity_map.get(wallets[wid]["entityId"], {}).get("beehusName") or
                            entity_map.get(wallets[wid]["entityId"], {}).get("name", "")),
            "cells": [{"label": "✓" if wid in wids_by_date[d] else "—",
                        "cls": _wallet_cls(1 if wid in wids_by_date[d] else 0)} for d in dates],
        } for wid in wallets], key=lambda x: x["name"])
    else:
        counts = {}
        for doc in db.unprocessedSecurityPositions.aggregate([
            {"$match": {"walletId": {"$in": wid_list}, "positionDate": {"$in": dates}}},
            {"$group": {"_id": {"w": "$walletId", "d": "$positionDate"}, "n": {"$sum": 1}}},
        ]):
            counts[(str(doc["_id"]["w"]), str(doc["_id"]["d"])[:10])] = doc["n"]

        rows = sorted([{
            "walletId": wid, "name": wallets[wid]["name"],
            "accountCode": wallets[wid]["accountCode"],
            "entity": entity_map.get(wallets[wid]["entityId"], {}).get("name", ""),
            "institution": (entity_map.get(wallets[wid]["entityId"], {}).get("beehusName") or
                            entity_map.get(wallets[wid]["entityId"], {}).get("name", "")),
            "cells": [{"label": str(counts.get((wid, d), 0)),
                        "cls": _wallet_cls(counts.get((wid, d), 0))} for d in dates],
        } for wid in wallets], key=lambda x: x["name"])

    return jsonify({"rows": rows, "dates": dates})


# ── Wallet templates ──────────────────────────────────────────────────────────

@bp.route("/api/wallet/templates")
def get_wallet_templates():
    return jsonify(_load_wallet_templates())


@bp.route("/api/wallet/templates", methods=["POST"])
def save_wallet_template():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Nome obrigatório"}), 400

    template = {
        "name":             name,
        "wallets":          data.get("wallets", []),
        "difRentThreshold": data.get("difRentThreshold", 0.0005),
    }

    templates = _load_wallet_templates()
    for t in templates:
        if t.get("name") == name:
            t["wallets"]          = template["wallets"]
            t["difRentThreshold"] = template["difRentThreshold"]
            _write_wallet_templates(templates)
            return jsonify({"ok": True, "overwritten": True})

    templates.append(template)
    _write_wallet_templates(templates)
    return jsonify({"ok": True, "overwritten": False})


@bp.route("/api/wallet/comments")
def get_wallet_comments():
    wids  = [w.strip() for w in request.args.get("walletIds", "").split(",") if w.strip()]
    dates = [d.strip() for d in request.args.get("dates", "").split(",") if d.strip()]
    all_comments = _load_wallet_comments()
    result = {}
    for wid in wids:
        for d in dates:
            key = f"{wid}_{d}"
            if key in all_comments:
                result[key] = all_comments[key]
    return jsonify(result)


@bp.route("/api/wallet/comments", methods=["POST"])
def save_wallet_comment():
    data      = request.get_json() or {}
    wallet_id = (data.get("walletId") or "").strip()
    date      = (data.get("date") or "").strip()
    text      = (data.get("text") or "").strip()
    created   = (data.get("createdAt") or "").strip()
    if not wallet_id or not date:
        return jsonify({"error": "walletId e date são obrigatórios"}), 400
    comments = _load_wallet_comments()
    key = f"{wallet_id}_{date}"
    if text:
        comments[key] = {"text": text, "createdAt": created}
    elif key in comments:
        del comments[key]
    _write_wallet_comments(comments)
    return jsonify({"ok": True})


@bp.route("/api/wallet/templates/<name>", methods=["DELETE"])
def delete_wallet_template(name):
    templates = _load_wallet_templates()
    _write_wallet_templates([t for t in templates if t.get("name") != name])
    return jsonify({"ok": True})


@bp.route("/api/wallet/template-rows")
def get_wallet_template_rows():
    """Load a template and return wallet-level grid data for its wallets."""
    import traceback
    template_name = request.args.get("name", "").strip()
    limit         = int(request.args.get("limit", 10))
    mode          = request.args.get("mode", "cargas").strip()
    start_date    = request.args.get("startDate", "").strip()
    end_date      = request.args.get("endDate", "").strip()

    try:
        templates = _load_wallet_templates()
        tmpl = next((t for t in templates if t.get("name") == template_name), None)
        if not tmpl:
            return jsonify({"rows": [], "dates": [], "error": "Template não encontrado"})

        dates = get_biz_dates_range(start_date, end_date) if (start_date and end_date) else get_biz_dates(limit)
        wid_list = [w["walletId"] for w in tmpl.get("wallets", []) if w.get("walletId")]
        if not wid_list:
            return jsonify({"rows": [], "dates": dates})

        tmpl_map = {w["walletId"]: w for w in tmpl.get("wallets", []) if w.get("walletId")}
        elapsed  = {d: _biz_days_elapsed(d) for d in dates}

        _oid_list = [ObjectId(wid) for wid in wid_list if len(wid) == 24]
        _wallet_entity = {
            str(w["_id"]): str(w.get("entityId", ""))
            for w in db.wallets.find({"_id": {"$in": _oid_list}}, {"entityId": 1})
        }
        _ent_inst = {
            str(e["_id"]): (e.get("beehusName") or e.get("name", ""))
            for e in db.entities.find({}, {"name": 1, "beehusName": 1})
        }

        def _cell(has_data, delay, d):
            expected = elapsed[d] >= delay
            if not expected:
                return {"label": "✓" if has_data else "—", "cls": "bg-gray-50 text-gray-300"}
            if has_data:
                return {"label": "✓", "cls": "bg-green-100 text-green-700"}
            return {"label": "—", "cls": "bg-red-100 text-red-700"}

        def _cell_count(count, delay, d):
            expected = elapsed[d] >= delay
            if not expected:
                return {"label": str(count), "cls": "bg-gray-50 text-gray-300"}
            if count > 0:
                return {"label": str(count), "cls": "bg-green-100 text-green-700"}
            return {"label": "0", "cls": "bg-red-100 text-red-700"}

        if mode in ("processed", "nav"):
            collection = db.processedPosition if mode == "processed" else db.navPackages
            match = {"walletId": {"$in": wid_list}, "positionDate": {"$in": dates}}
            if mode == "nav":
                match["trashed"] = {"$ne": True}
            wids_by_date = {d: set() for d in dates}
            for doc in collection.aggregate([
                {"$match": match},
                {"$group": {"_id": {"w": "$walletId", "d": "$positionDate"}}},
            ]):
                d_key = str(doc["_id"]["d"])[:10]
                if d_key in wids_by_date:
                    wids_by_date[d_key].add(str(doc["_id"]["w"]))

            rows = [{
                "walletId": wid,
                "name": tmpl_map[wid].get("name", wid),
                "accountCode": tmpl_map[wid].get("accountCode", ""),
                "institution": _ent_inst.get(_wallet_entity.get(wid, ""), ""),
                "periodicity": tmpl_map[wid].get("periodicity", ""),
                "delay": tmpl_map[wid].get("delay", 0),
                "note": tmpl_map[wid].get("note", ""),
                "responsible": tmpl_map[wid].get("responsible", ""),
                "cells": [_cell(wid in wids_by_date[d], tmpl_map[wid].get("delay", 0), d) for d in dates],
            } for wid in wid_list if wid in tmpl_map]
        else:
            counts = {}
            for doc in db.unprocessedSecurityPositions.aggregate([
                {"$match": {"walletId": {"$in": wid_list}, "positionDate": {"$in": dates}}},
                {"$group": {"_id": {"w": "$walletId", "d": "$positionDate"}, "n": {"$sum": 1}}},
            ]):
                counts[(str(doc["_id"]["w"]), str(doc["_id"]["d"])[:10])] = doc["n"]

            rows = [{
                "walletId": wid,
                "name": tmpl_map[wid].get("name", wid),
                "accountCode": tmpl_map[wid].get("accountCode", ""),
                "institution": _ent_inst.get(_wallet_entity.get(wid, ""), ""),
                "periodicity": tmpl_map[wid].get("periodicity", ""),
                "delay": tmpl_map[wid].get("delay", 0),
                "note": tmpl_map[wid].get("note", ""),
                "responsible": tmpl_map[wid].get("responsible", ""),
                "cells": [_cell_count(counts.get((wid, d), 0), tmpl_map[wid].get("delay", 0), d) for d in dates],
            } for wid in wid_list if wid in tmpl_map]

        return jsonify({"rows": rows, "dates": dates})

    except Exception:
        traceback.print_exc()
        return jsonify({"rows": [], "dates": [], "error": "Erro interno ao processar template. Veja o terminal para detalhes."}), 500


@bp.route("/api/wallet/template-detail")
def get_wallet_template_detail():
    """Return daily issue detail for wallets in a template at a specific date."""
    template_name = request.args.get("name", "").strip()
    date_str      = request.args.get("date", "").strip()

    if not template_name or not date_str:
        return jsonify({"rows": [], "date": date_str})

    templates = _load_wallet_templates()
    tmpl = next((t for t in templates if t.get("name") == template_name), None)
    if not tmpl:
        return jsonify({"rows": [], "date": date_str})

    tmpl_map = {w["walletId"]: w for w in tmpl.get("wallets", []) if w.get("walletId")}
    wid_list = list(tmpl_map.keys())
    if not wid_list:
        return jsonify({"rows": [], "date": date_str})

    issue_types = [
        "security_unmapped",
        "security_missing_classification",
        "security_missing_history_price",
        "security_missing_price",
        "explosion_error",
        "missing_fund_position_for_explosion",
    ]

    # Count issues per (walletId, type)
    counts = {(wid, it): 0 for wid in wid_list for it in issue_types}
    for doc in db.issues.aggregate([
        {"$match": {"walletId": {"$in": wid_list}, "date": date_str, "type": {"$in": issue_types}, "status": "pending"}},
        {"$group": {"_id": {"w": "$walletId", "t": "$type"}, "n": {"$sum": 1}}},
    ]):
        key = (str(doc["_id"]["w"]), doc["_id"]["t"])
        if key in counts:
            counts[key] = doc["n"]

    # Unidentified transactions (beehusTransactionType is null) per wallet
    unidentified = {wid: 0 for wid in wid_list}
    for doc in db.transactions.aggregate([
        {"$match": {"walletId": {"$in": wid_list}, "liquidationDate": date_str, "beehusTransactionType": None}},
        {"$group": {"_id": "$walletId", "n": {"$sum": 1}}},
    ]):
        wid = str(doc["_id"])
        if wid in unidentified:
            unidentified[wid] = doc["n"]

    # Processed position existence per wallet
    pp_wids = set()
    for doc in db.processedPosition.aggregate([
        {"$match": {"walletId": {"$in": wid_list}, "positionDate": date_str}},
        {"$group": {"_id": "$walletId"}},
    ]):
        pp_wids.add(str(doc["_id"]))

    # NAV data per wallet (existence + returnNavPerShare / returnContribution)
    nav_wids = set()
    nav_dif  = {}  # walletId -> difRent value (or None)
    for doc in db.navPackages.aggregate([
        {"$match": {"walletId": {"$in": wid_list}, "positionDate": date_str, "trashed": {"$ne": True}}},
        {"$project": {"walletId": 1, "dif": {"$subtract": [{"$ifNull": ["$returnNavPerShare", 0]}, {"$ifNull": ["$returnContribution", 0]}]}}},
    ]):
        wid = str(doc.get("walletId", ""))
        nav_wids.add(wid)
        nav_dif[wid] = doc.get("dif", 0)

    threshold = tmpl.get("difRentThreshold", 0.0005)

    rows = []
    for wid in wid_list:
        w = tmpl_map[wid]
        dif_val   = nav_dif.get(wid)
        rows.append({
            "walletId":    wid,
            "name":        w.get("name", wid),
            "accountCode": w.get("accountCode", ""),
            "periodicity": w.get("periodicity", ""),
            "delay":       w.get("delay", 0),
            "note":        w.get("note", ""),
            "responsible": w.get("responsible", ""),
            "unmapped":              counts[(wid, "security_unmapped")],
            "missingClassification": counts[(wid, "security_missing_classification")],
            "missingHistoryPrice":   counts[(wid, "security_missing_history_price")],
            "missingPrice":          counts[(wid, "security_missing_price")],
            "explosionError":        counts[(wid, "explosion_error")],
            "missingFundPosition":   counts[(wid, "missing_fund_position_for_explosion")],
            "unidentifiedTxn":       unidentified[wid],
            "hasProcessedPosition":  wid in pp_wids,
            "hasNav":                wid in nav_wids,
            "difRent":               dif_val,
            "difRentThreshold":      threshold,
            "difRentAlert":          dif_val is not None and abs(dif_val) > threshold,
        })

    return jsonify({"rows": rows, "date": date_str})
