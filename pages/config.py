from flask import Blueprint, render_template, jsonify, request
from db import db, load_config, load_config_delays, load_config_methods, load_config_responsible, load_settings, CONFIG_FILE, SETTINGS_FILE
import json, os

bp = Blueprint("config", __name__)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_company_entity_list(selected, delays, methods, responsible):
    company_names = {str(c["_id"]): c.get("name", "") for c in db.companies.find({}, {"name": 1})}
    entity_names  = {str(e["_id"]): e.get("name", "") for e in db.entities.find({}, {"name": 1})}

    company_map = {}
    for w in db.wallets.find({}, {"companyId": 1, "entityId": 1}):
        cid = str(w.get("companyId", ""))
        eid = str(w.get("entityId", ""))
        if cid and eid:
            company_map.setdefault(cid, set()).add(eid)

    result = []
    for cid in sorted(company_map, key=lambda c: company_names.get(c, c)):
        entities = sorted([
            {
                "entityId":   eid,
                "entityName": entity_names.get(eid, eid),
                "selected":   (cid, eid) in selected,
                "delay":       delays.get((cid, eid), 0),
                "method":      methods.get((cid, eid), ""),
                "responsible": responsible.get((cid, eid), ""),
            }
            for eid in company_map[cid]
        ], key=lambda e: e["entityName"])
        result.append({
            "companyId":   cid,
            "companyName": company_names.get(cid, cid),
            "entities":    entities,
        })
    return result


# ── Routes ─────────────────────────────────────────────────────────────────────

@bp.route("/config")
def config_page():
    return render_template("config.html")


@bp.route("/api/config/entities")
def config_entities():
    return jsonify(_build_company_entity_list(
        load_config(), load_config_delays(), load_config_methods(), load_config_responsible()
    ))


@bp.route("/api/config/save", methods=["POST"])
def config_save():
    selected = (request.get_json() or {}).get("selected", [])
    # Ensure each entry has companyId, entityId, delay, method
    cleaned = [
        {
            "companyId":   s["companyId"],
            "entityId":    s["entityId"],
            "delay":       int(s.get("delay", 0)),
            "method":      s.get("method", ""),
            "responsible": s.get("responsible", ""),
        }
        for s in selected
    ]
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2)
    return jsonify({"saved": len(cleaned)})


@bp.route("/settings")
def settings_page():
    return render_template("settings.html")


@bp.route("/api/settings/load")
def settings_load():
    return jsonify(load_settings())


@bp.route("/api/settings/save", methods=["POST"])
def settings_save():
    current = load_settings()
    data    = request.get_json() or {}
    if "only_daily_position"   in data: current["only_daily_position"]   = bool(data["only_daily_position"])
    if "only_with_consumption" in data: current["only_with_consumption"] = bool(data["only_with_consumption"])
    if "wizard_blacklist"      in data: current["wizard_blacklist"]      = [str(w).lower() for w in data["wizard_blacklist"]]
    if "company_filter"        in data: current["company_filter"]        = [str(i) for i in data["company_filter"]]
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)
    return jsonify({"ok": True})


@bp.route("/api/settings/companies")
def settings_companies():
    cf     = set(load_settings().get("company_filter", []))
    all_co = sorted(
        [{"id": str(c["_id"]), "name": c.get("name", str(c["_id"]))}
         for c in db.companies.find({}, {"name": 1})],
        key=lambda c: c["name"],
    )
    for c in all_co:
        c["visible"] = (not cf) or (c["id"] in cf)
    return jsonify({"companies": all_co, "filterActive": bool(cf)})
