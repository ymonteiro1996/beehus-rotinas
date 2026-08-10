from flask import Blueprint, jsonify, request, redirect
from db import (catalog_company_names, catalog_entity_names, catalog_wallets, filter_wallets,
                api_nav_results, get_biz_dates, load_config_full, load_nav_settings,
                NAV_SETTINGS_FILE, biz_days_elapsed as _biz_days_elapsed,
                cell_cls as _cell_cls, wallet_cls as _wallet_cls,
                build_wallet_map as _build_wallet_map)
import json, os

bp = Blueprint("nav", __name__)


def _count_navs(dates, wallet_to_pair, pairs):
    """Contexto:
    Conta, por (companyId,entityId) e data, quantas carteiras têm NAV
    calculado — substitui a antiga agregação `db.navPackages.aggregate`.
    Chamada pela grade principal de NAV. Retorna {(pair, date): count}.

    Pseudocódigo:
      1. Sem carteiras relevantes -> {}.
      2. 1 chamada `api_nav_results(d)` por data (fan-out por empresa, já
         cacheado em db.py) — cada item já vem por carteira.
      3. Agrupa por (pair, date), só contando carteiras que estão em
         `wallet_to_pair`/`pairs` (mesmo filtro de antes).
    """
    relevant_wids = {wid for wid, pair in wallet_to_pair.items() if pair in pairs}
    if not relevant_wids:
        return {}
    pair_wids = {}  # (pair, date) -> set of walletIds
    for d in dates:
        for item in api_nav_results(d):
            wid = str(item.get("walletId", ""))
            if wid not in relevant_wids:
                continue
            pair = wallet_to_pair.get(wid)
            if pair and pair in pairs:
                pair_wids.setdefault((pair, d), set()).add(wid)
    return {k: len(v) for k, v in pair_wids.items()}


# ── Main page ──────────────────────────────────────────────────────────────────

@bp.route("/nav")
def index():
    return redirect("/")


@bp.route("/api/nav/rows")
def get_rows():
    limit         = int(request.args.get("limit", 10))
    dates         = get_biz_dates(limit)
    company_names = catalog_company_names()
    entity_names  = catalog_entity_names()

    wallet_to_pair, pair_total = _build_wallet_map(load_nav_settings())

    pairs = set(wallet_to_pair.values())
    selected, delays, _, __ = load_config_full()
    if selected:
        pairs = pairs & selected

    elapsed = {d: _biz_days_elapsed(d) for d in dates}
    counts  = _count_navs(dates, wallet_to_pair, pairs)

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
            "cells":     cells,
        })

    return jsonify({"rows": rows, "dates": dates})


@bp.route("/api/nav/detail")
def get_detail():
    cid = request.args.get("companyId")
    eid = request.args.get("entityId")
    d   = request.args.get("date")

    matched = filter_wallets(catalog_wallets(company_ids=[cid]), company_id=cid, entity_id=eid,
                             settings=load_nav_settings())
    wallets = {w["_id"]: {"name": w["name"] or w["_id"], "accountCode": w["accountCode"]} for w in matched}

    wids_with_nav = {
        str(item.get("walletId", ""))
        for item in api_nav_results(d, company_ids=[cid])
        if str(item.get("walletId", "")) in wallets
    }

    detail = sorted([
        {
            "name":        wallets[wid]["name"],
            "accountCode": wallets[wid]["accountCode"],
            "count":       1 if wid in wids_with_nav else 0,
            "cls":         _wallet_cls(wid in wids_with_nav),
        }
        for wid in wallets
    ], key=lambda x: x["name"])

    return jsonify({"detail": detail, "date": d})


@bp.route("/api/nav/detail-grid")
def get_nav_detail_grid():
    cid   = request.args.get("companyId")
    eid   = request.args.get("entityId")
    limit = int(request.args.get("limit", 10))
    dates = get_biz_dates(limit)

    matched = filter_wallets(catalog_wallets(company_ids=[cid]), company_id=cid, entity_id=eid,
                             settings=load_nav_settings())
    wallets = {w["_id"]: {"name": w["name"] or w["_id"], "accountCode": w["accountCode"]} for w in matched}

    wids_by_date = {d: set() for d in dates}
    for d in dates:
        for item in api_nav_results(d, company_ids=[cid]):
            wid = str(item.get("walletId", ""))
            if wid in wallets:
                wids_by_date[d].add(wid)

    rows = sorted([
        {
            "name":        wallets[wid]["name"],
            "accountCode": wallets[wid]["accountCode"],
            "cells": [
                {"label": "✓" if wid in wids_by_date[d] else "—",
                 "cls":   _wallet_cls(wid in wids_by_date[d])}
                for d in dates
            ],
        }
        for wid in wallets
    ], key=lambda x: x["name"])

    return jsonify({"rows": rows, "dates": dates})


# ── Settings ───────────────────────────────────────────────────────────────────

@bp.route("/api/nav/settings/load")
def nav_settings_load():
    return jsonify(load_nav_settings())


@bp.route("/api/nav/settings/save", methods=["POST"])
def nav_settings_save():
    current = load_nav_settings()
    data    = request.get_json() or {}
    if "only_daily_position"   in data: current["only_daily_position"]   = bool(data["only_daily_position"])
    if "only_with_consumption" in data: current["only_with_consumption"] = bool(data["only_with_consumption"])
    os.makedirs(os.path.dirname(NAV_SETTINGS_FILE), exist_ok=True)
    with open(NAV_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)
    return jsonify({"ok": True})
