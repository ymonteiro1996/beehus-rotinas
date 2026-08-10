from flask import Blueprint, render_template, jsonify, request
from db import (db, get_biz_dates, get_company_filter, catalog_companies, catalog_wallets,
                catalog_securities_by_id, catalog_groupings,
                api_processed_positions_multi, api_nav_results_multi)
import json, os, math

bp = Blueprint("banda_rentabilidades", __name__)

COMMENTS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "banda_comments.json")

_LAMBDA    = 0.94   # EWMA decay factor
_BAND_MULT = 2.0    # anomaly threshold: center ± BAND_MULT * ewma_std
_HIST_DAYS = 60     # history window for EWMA benchmark
_N_DISPLAY = 5      # number of daily columns shown


# ── Persistence ───────────────────────────────────────────────────────────────

def _load_comments():
    if not os.path.exists(COMMENTS_FILE):
        return {}
    with open(COMMENTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_comments(data):
    with open(COMMENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── EWMA / statistical helpers ────────────────────────────────────────────────

def _remove_outliers(vals):
    if len(vals) < 4:
        return vals
    sv = sorted(vals)
    n  = len(sv)
    q1 = sv[n // 4]
    q3 = sv[3 * n // 4]
    iqr = q3 - q1
    if iqr == 0:
        return vals
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    return [v for v in vals if lo <= v <= hi]


def _ewma_band(series):
    """
    Compute EWMA mean and std for series (oldest→newest).
    Returns (center, lower, upper) or (None, None, None).
    """
    clean = _remove_outliers(list(series))
    if not clean:
        return None, None, None
    n   = len(clean)
    lam = _LAMBDA
    w   = [(1 - lam) * lam ** (n - 1 - i) for i in range(n)]
    tw  = sum(w)
    mu  = sum(wi * xi for wi, xi in zip(w, clean)) / tw
    var = sum(wi * (xi - mu) ** 2 for wi, xi in zip(w, clean)) / tw
    sd  = math.sqrt(var) if var > 0 else 0.0
    half = _BAND_MULT * sd
    return round(mu, 8), round(mu - half, 8), round(mu + half, 8)


def _compound(returns):
    """Compound a list of daily returns into a single period return."""
    p = 1.0
    for r in returns:
        if r is not None:
            p *= (1.0 + r)
    return round(p - 1.0, 8)


# ── Helpers de dados (API-first — CLAUDE.md §8) ──────────────────────────────

def _wallets_for_company(company_id):
    if not company_id:
        return set()
    return {w["_id"] for w in catalog_wallets(company_ids=[company_id])}


def _classify_type(sec):
    """Map a security document to a benchmark category per the spreadsheet spec."""
    st  = (sec.get("securityType") or "").lower()
    tp  = (sec.get("type") or "").lower()
    yld = sec.get("yield")
    pct = sec.get("indexerPercentual")

    if any(k in st for k in ("ação", "ações", "etf", "equity", "acao", "acoes")):
        return "Ações/ETF"
    if "soberan" in st or "sovereign" in st:
        return "Bond Soberano"
    if "fundo" in st or "fund" in st:
        return "Fundo Offshore" if "off" in st else "Fundo Onshore"
    if "privado" in st or "private" in st:
        return "Mercado Privado"
    if "ltn" in st or "ltn" in tp:
        return "Título Público - LTN"
    if "ltf" in st or "lft" in st or "ltf" in tp or "lft" in tp:
        return "Título Público - LFT"
    if "ntn" in st or "ntn" in tp:
        return "Título Público - NTN"
    if any(k in st for k in ("bond", "debenture", "renda fixa", "cdb", "lca", "lci", "cri", "cra")):
        has_yield = yld is not None and yld != 0
        has_pct   = pct is not None and pct != 0
        if has_yield and not has_pct:
            return "Bond Pré-fixado"
        if has_pct and not has_yield:
            return "Bond Pós-fixado"
        if has_yield and has_pct:
            return "Bond Híbrido"
        return "Bond"
    return sec.get("securityType") or "Outros"


def _sec_meta(sec_ids):
    """Returns {secId: {"name": ..., "type": ...}}."""
    by_id = catalog_securities_by_id()
    meta  = {}
    for sid in sec_ids:
        s = by_id.get(str(sid))
        if s:
            meta[str(sid)] = {"name": s.get("beehusName", sid), "type": _classify_type(s)}
    return meta


def _compute_sec_returns(company_id, wids, all_dates):
    """
    Compute daily contribution-based returns per security from processedPosition.
    all_dates: sorted list (oldest→newest).
    Returns {secId: {date: return_value}}.
    """
    if not wids:
        return {}
    sorted_dates = sorted(all_dates)

    # Fetch all processedPositions in range for these wallets — 1 chamada por
    # data via a API (fan-out em paralelo, cacheado), filtrando no cliente
    # pelas carteiras da empresa/tela.
    by_date = api_processed_positions_multi(sorted_dates, company_ids=[company_id])
    positions = {}  # {wid: {date: {secId: {pu, qty, tc, ec}}}}
    for d, docs in by_date.items():
        for doc in docs:
            wid = str(doc.get("walletId", ""))
            if wid not in wids:
                continue
            positions.setdefault(wid, {})[d] = {
                str(s.get("securityId", "")): {
                    "pu":  s.get("pu"),
                    "qty": s.get("quantity"),
                    "tc":  s.get("totalContribution"),
                    "ec":  s.get("eventContribution") or 0,
                }
                for s in doc.get("securities", []) if s.get("securityId")
            }

    # Compute consecutive-date returns per wallet
    sec_ret = {}  # {secId: {date: [returns across wallets]}}
    for wid, wpos in positions.items():
        wdates = sorted(wpos.keys())
        for i in range(1, len(wdates)):
            cur_d = wdates[i]
            prv_d = wdates[i - 1]
            cur   = wpos[cur_d]
            prv   = wpos[prv_d]
            for sid, cs in cur.items():
                ps = prv.get(sid)
                if not ps:
                    continue
                f_pu  = ps["pu"]
                f_qty = ps["qty"]
                c_pu  = cs["pu"]
                tc    = cs["tc"]
                ec    = cs["ec"] or 0
                qty   = cs["qty"]
                f_bal = (f_pu * f_qty) if (f_pu and f_qty) else None

                # Contribution return (primary): totalContribution / formerBalance
                ret = None
                try:
                    ret = tc / f_bal if (tc is not None and f_bal) else None
                except (TypeError, ZeroDivisionError):
                    pass

                # PU return (fallback)
                if ret is None:
                    try:
                        ep  = (ec / qty) if (qty and qty != 0) else 0
                        ret = (c_pu + ep) / f_pu - 1 if (c_pu and f_pu) else None
                    except (TypeError, ZeroDivisionError):
                        pass

                if ret is not None:
                    sec_ret.setdefault(sid, {}).setdefault(cur_d, []).append(ret)

    # Average across wallets for same security on same date
    return {
        sid: {d: sum(vals) / len(vals) for d, vals in date_map.items()}
        for sid, date_map in sec_ret.items()
    }


def _build_type_benchmarks(sec_returns, sec_meta, all_dates_sorted, display_dates):
    """
    For each (securityType, display_date), compute EWMA band using the cross-sectional
    average return of that type over the historical window ending on that date.
    Returns {secType: {date: {"center", "lower", "upper"}}}.
    """
    type_date_rets = {}  # {secType: {date: [returns]}}
    for sid, date_map in sec_returns.items():
        st = sec_meta.get(sid, {}).get("type", "Outros")
        for d, ret in date_map.items():
            type_date_rets.setdefault(st, {}).setdefault(d, []).append(ret)

    benchmarks = {}
    for st, date_map in type_date_rets.items():
        benchmarks[st] = {}
        for d in display_dates:
            hist_avgs = []
            for hd in all_dates_sorted:
                if hd > d:
                    break
                vals  = date_map.get(hd, [])
                clean = _remove_outliers(vals)
                if clean:
                    hist_avgs.append(sum(clean) / len(clean))
            if hist_avgs:
                c, lo, hi = _ewma_band(hist_avgs)
                benchmarks[st][d] = {"center": c, "lower": lo, "upper": hi}
    return benchmarks


def _make_cells(item_date_returns, display_dates, bench_by_date):
    """Build the cell list for one row (daily mode)."""
    cells = []
    for d in display_dates:
        ret = item_date_returns.get(d)
        b   = (bench_by_date or {}).get(d) or {}
        lo  = b.get("lower")
        hi  = b.get("upper")
        is_anom = bool(ret is not None and lo is not None and hi is not None and (ret < lo or ret > hi))
        cells.append({
            "date":      d,
            "return":    round(ret, 8) if ret is not None else None,
            "lower":     lo,
            "upper":     hi,
            "center":    b.get("center"),
            "isAnomaly": is_anom,
        })
    return cells


def _published_wallet_ids(wids, dates):
    """Return subset of wids that have a published navPackage on any of the given dates.

    Gap confirmado [2026-08-10]: nenhum endpoint da API expõe o `published`
    de navPackage por CARTEIRA (`get_nav_results`/`walletsWithNavDetailed`
    não tem esse campo — só existe no nível de agrupamento). Fica em Mongo
    como fallback de leitura documentado (CLAUDE.md §8), só para este filtro.
    """
    pub = set()
    for doc in db.navPackages.find(
        {"walletId": {"$in": list(wids)},
         "positionDate": {"$in": list(dates)},
         "published": {"$in": [True, "true", "True", 1]},
         "trashed": {"$ne": True}},
        {"walletId": 1}
    ):
        pub.add(str(doc["walletId"]))
    return pub


def _fetch_nav_data(company_id, wids, all_dates, published_only):
    """Contexto:
    {walletId: {date: return}} (returnNavPerShare, com fallback pra
    returnContribution) para as carteiras/datas pedidas — usado por
    "Carteiras" e "Agrupamentos". Retorna só carteiras em `wids`.

    Pseudocódigo:
      1. `published_only` -> gap confirmado (ver `_published_wallet_ids`):
         não há campo `published` por carteira na API. Fica em Mongo.
      2. Sem esse filtro -> `api_nav_results_multi` (1 chamada por data,
         fan-out, cacheado) — caminho comum (não filtrado).
    """
    nav_data = {}
    if published_only:
        nav_filter = {
            "walletId":     {"$in": list(wids)},
            "positionDate": {"$in": all_dates},
            "trashed":      {"$ne": True},
            "published":    {"$in": [True, "true", "True", 1]},
        }
        for doc in db.navPackages.find(
            nav_filter, {"walletId": 1, "positionDate": 1, "returnNavPerShare": 1, "returnContribution": 1}
        ):
            wid = str(doc["walletId"])
            d   = str(doc["positionDate"])[:10]
            ret = doc.get("returnNavPerShare")
            if ret is None:
                ret = doc.get("returnContribution")
            if ret is not None:
                nav_data.setdefault(wid, {})[d] = ret
        return nav_data

    by_date = api_nav_results_multi(all_dates, company_ids=[company_id])
    for d, items in by_date.items():
        for item in items:
            wid = str(item.get("walletId", ""))
            if wid not in wids:
                continue
            ret = item.get("returnNavPerShare")
            if ret is None:
                ret = item.get("returnContribution")
            if ret is not None:
                nav_data.setdefault(wid, {})[d] = ret
    return nav_data


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route("/banda-rentabilidades")
def index():
    companies = sorted(
        [{"id": c["_id"], "name": c.get("name") or c["_id"]} for c in catalog_companies()],
        key=lambda c: c["name"],
    )
    cf = get_company_filter()
    if cf:
        companies = [c for c in companies if c["id"] in cf]
    return render_template("banda_rentabilidades.html", companies=companies)


@bp.route("/api/banda-rentabilidades/dates")
def get_dates():
    """Gap confirmado [2026-08-10]: nenhum endpoint da API dá "a última data
    com dado, em lote" — o mesmo gap já documentado no app-irmão ControleCargas
    (colunas "Última Unp/Pro/Pub"). Fica em Mongo como fallback de leitura."""
    company_id = request.args.get("companyId", "")
    end_date   = request.args.get("endDate") or None
    wids = list(_wallets_for_company(company_id))
    if not end_date and wids:
        latest = db.navPackages.find_one(
            {"walletId": {"$in": wids}, "trashed": {"$ne": True}},
            {"positionDate": 1},
            sort=[("positionDate", -1)]
        )
        if latest and latest.get("positionDate"):
            end_date = str(latest["positionDate"])[:10]
    return jsonify({"latestDate": end_date or ""})


# ── Ativos ────────────────────────────────────────────────────────────────────

@bp.route("/api/banda-rentabilidades/ativos")
def get_ativos():
    company_id     = request.args.get("companyId", "")
    end_date       = request.args.get("endDate") or None
    mode           = request.args.get("mode", "daily")   # daily | summary
    published_only = request.args.get("publishedOnly", "0") == "1"
    anomaly_only   = request.args.get("anomalyOnly",   "0") == "1"

    if not company_id:
        return jsonify({"rows": [], "dates": [], "mode": mode})

    wids = _wallets_for_company(company_id)
    if not wids:
        return jsonify({"rows": [], "dates": [], "mode": mode})

    # Date windows: display dates + history for EWMA
    if mode == "summary":
        n_extra = 252   # need full year for annual compound
    else:
        n_extra = _N_DISPLAY

    display_dates = get_biz_dates(n_extra, end_date)
    all_dates     = get_biz_dates(n_extra + _HIST_DAYS + 1, end_date)

    use_wids = wids
    if published_only:
        use_wids = _published_wallet_ids(wids, display_dates[-5:])

    sec_returns = _compute_sec_returns(company_id, use_wids, all_dates)
    if not sec_returns:
        cols = display_dates if mode == "daily" else ["Semanal", "Mensal", "Anual"]
        return jsonify({"rows": [], "dates": cols, "mode": mode})

    meta             = _sec_meta(list(sec_returns.keys()))
    all_dates_sorted = sorted(set(all_dates))
    comments         = _load_comments()

    if mode == "summary":
        today         = display_dates[-1]
        weekly_dates  = get_biz_dates(5,   today)
        monthly_dates = get_biz_dates(21,  today)
        annual_dates  = get_biz_dates(252, today)
        type_bench    = _build_type_benchmarks(sec_returns, meta, all_dates_sorted, display_dates)

        def _cmp_range(date_map, dates_range):
            rets = [date_map.get(d) for d in dates_range if date_map.get(d) is not None]
            return _compound(rets) if rets else None

        def _bench_cmp(st, dates_range):
            tb = type_bench.get(st, {})
            cs = [tb.get(d, {}).get("center") for d in dates_range if tb.get(d, {}).get("center") is not None]
            ls = [tb.get(d, {}).get("lower")  for d in dates_range if tb.get(d, {}).get("lower")  is not None]
            us = [tb.get(d, {}).get("upper")  for d in dates_range if tb.get(d, {}).get("upper")  is not None]
            return (_compound(cs) if cs else None,
                    _compound(ls) if ls else None,
                    _compound(us) if us else None)

        def _cell(label, ret, bc):
            c, lo, hi = bc
            is_anom = bool(ret is not None and lo is not None and hi is not None and (ret < lo or ret > hi))
            return {"date": label, "return": ret, "center": c, "lower": lo, "upper": hi, "isAnomaly": is_anom}

        rows = []
        for sid, date_map in sec_returns.items():
            m  = meta.get(sid, {"name": sid, "type": "Outros"})
            st = m["type"]
            cells = [
                _cell("Semanal", _cmp_range(date_map, weekly_dates),  _bench_cmp(st, weekly_dates)),
                _cell("Mensal",  _cmp_range(date_map, monthly_dates), _bench_cmp(st, monthly_dates)),
                _cell("Anual",   _cmp_range(date_map, annual_dates),  _bench_cmp(st, annual_dates)),
            ]
            is_any = any(c["isAnomaly"] for c in cells)
            if anomaly_only and not is_any:
                continue
            rows.append({"id": sid, "name": m["name"], "type": st,
                         "cells": cells, "isAnomalyToday": is_any,
                         "comment": comments.get(sid, "")})
        rows.sort(key=lambda r: (not r["isAnomalyToday"], r["type"], r["name"]))
        return jsonify({"rows": rows, "dates": ["Semanal", "Mensal", "Anual"], "mode": mode})

    # ── Daily mode ─────────────────────────────────────────────────────────────
    type_bench = _build_type_benchmarks(sec_returns, meta, all_dates_sorted, display_dates)
    rows = []
    for sid, date_map in sec_returns.items():
        m     = meta.get(sid, {"name": sid, "type": "Outros"})
        st    = m["type"]
        cells = _make_cells(date_map, display_dates, type_bench.get(st))
        is_anom_today = cells[-1]["isAnomaly"] if cells else False
        if anomaly_only and not any(c["isAnomaly"] for c in cells):
            continue
        rows.append({"id": sid, "name": m["name"], "type": st,
                     "cells": cells, "isAnomalyToday": is_anom_today,
                     "comment": comments.get(sid, "")})
    rows.sort(key=lambda r: (not r["isAnomalyToday"], r["type"], r["name"]))
    return jsonify({"rows": rows, "dates": display_dates, "mode": mode})


# ── Carteiras ─────────────────────────────────────────────────────────────────

@bp.route("/api/banda-rentabilidades/carteiras")
def get_carteiras():
    company_id     = request.args.get("companyId", "")
    end_date       = request.args.get("endDate") or None
    mode           = request.args.get("mode", "daily")
    published_only = request.args.get("publishedOnly", "0") == "1"
    anomaly_only   = request.args.get("anomalyOnly",   "0") == "1"

    if not company_id:
        return jsonify({"rows": [], "dates": [], "mode": mode})

    wids = _wallets_for_company(company_id)
    if not wids:
        return jsonify({"rows": [], "dates": [], "mode": mode})

    if mode == "summary":
        n_extra = 252
    else:
        n_extra = _N_DISPLAY

    display_dates = get_biz_dates(n_extra, end_date)
    all_dates     = get_biz_dates(n_extra + _HIST_DAYS, end_date)

    nav_data = _fetch_nav_data(company_id, wids, all_dates, published_only)

    if not nav_data:
        cols = display_dates if mode == "daily" else ["Semanal", "Mensal", "Anual"]
        return jsonify({"rows": [], "dates": cols, "mode": mode})

    # Wallet names
    wallet_names = {w["_id"]: w["name"] or w["_id"] for w in catalog_wallets(company_ids=[company_id])}

    # Cross-wallet EWMA benchmark
    all_wret = {}  # {date: [returns]}
    for wid, dm in nav_data.items():
        for d, ret in dm.items():
            all_wret.setdefault(d, []).append(ret)
    all_dates_sorted = sorted(all_wret.keys())

    def _bench_for_dates(target_dates):
        b = {}
        for d in target_dates:
            hist = []
            for hd in all_dates_sorted:
                if hd > d:
                    break
                vals  = all_wret.get(hd, [])
                clean = _remove_outliers(vals)
                if clean:
                    hist.append(sum(clean) / len(clean))
            if hist:
                c, lo, hi = _ewma_band(hist)
                b[d] = {"center": c, "lower": lo, "upper": hi}
        return b

    comments = _load_comments()

    if mode == "summary":
        today         = display_dates[-1]
        weekly_dates  = get_biz_dates(5,   today)
        monthly_dates = get_biz_dates(21,  today)
        annual_dates  = get_biz_dates(252, today)
        bench_w = _bench_for_dates(weekly_dates)
        bench_m = _bench_for_dates(monthly_dates)
        bench_a = _bench_for_dates(annual_dates)

        def _cmp(dm, dates):
            rets = [dm.get(d) for d in dates if dm.get(d) is not None]
            return _compound(rets) if rets else None

        def _bcmp(bench, dates):
            cs = [bench.get(d, {}).get("center") for d in dates if bench.get(d, {}).get("center") is not None]
            ls = [bench.get(d, {}).get("lower")  for d in dates if bench.get(d, {}).get("lower")  is not None]
            us = [bench.get(d, {}).get("upper")  for d in dates if bench.get(d, {}).get("upper")  is not None]
            return (_compound(cs) if cs else None, _compound(ls) if ls else None, _compound(us) if us else None)

        def _cell(label, ret, bc):
            c, lo, hi = bc
            is_a = bool(ret is not None and lo is not None and hi is not None and (ret < lo or ret > hi))
            return {"date": label, "return": ret, "center": c, "lower": lo, "upper": hi, "isAnomaly": is_a}

        rows = []
        for wid, dm in nav_data.items():
            cells = [
                _cell("Semanal", _cmp(dm, weekly_dates),  _bcmp(bench_w, weekly_dates)),
                _cell("Mensal",  _cmp(dm, monthly_dates), _bcmp(bench_m, monthly_dates)),
                _cell("Anual",   _cmp(dm, annual_dates),  _bcmp(bench_a, annual_dates)),
            ]
            is_any = any(c["isAnomaly"] for c in cells)
            if anomaly_only and not is_any:
                continue
            rows.append({"id": wid, "name": wallet_names.get(wid, wid),
                         "cells": cells, "isAnomalyToday": is_any,
                         "comment": comments.get(f"carteira_{wid}", "")})
        rows.sort(key=lambda r: (not r["isAnomalyToday"], r["name"]))
        return jsonify({"rows": rows, "dates": ["Semanal", "Mensal", "Anual"], "mode": mode})

    bench = _bench_for_dates(display_dates)
    rows  = []
    for wid, dm in nav_data.items():
        cells = _make_cells(dm, display_dates, bench)
        is_anom_today = cells[-1]["isAnomaly"] if cells else False
        if anomaly_only and not any(c["isAnomaly"] for c in cells):
            continue
        rows.append({"id": wid, "name": wallet_names.get(wid, wid),
                     "cells": cells, "isAnomalyToday": is_anom_today,
                     "comment": comments.get(f"carteira_{wid}", "")})
    rows.sort(key=lambda r: (not r["isAnomalyToday"], r["name"]))
    return jsonify({"rows": rows, "dates": display_dates, "mode": mode})


# ── Agrupamentos ──────────────────────────────────────────────────────────────

@bp.route("/api/banda-rentabilidades/agrupamentos")
def get_agrupamentos():
    company_id     = request.args.get("companyId", "")
    end_date       = request.args.get("endDate") or None
    mode           = request.args.get("mode", "daily")
    published_only = request.args.get("publishedOnly", "0") == "1"
    anomaly_only   = request.args.get("anomalyOnly",   "0") == "1"

    if not company_id:
        return jsonify({"rows": [], "dates": [], "mode": mode})

    company_wids = _wallets_for_company(company_id)
    if not company_wids:
        return jsonify({"rows": [], "dates": [], "mode": mode})

    # Discover groupings for this company via a API (substitui a antiga
    # leitura de `db.groups` — collection SEM nenhum documento, bug
    # pré-existente que fazia esta aba sempre voltar vazia; ver
    # `db.catalog_groupings`).
    groups = []
    for g in catalog_groupings(company_id):
        overlap = set(g["walletIds"]) & company_wids
        if overlap:
            groups.append({"id": g["_id"], "name": g["name"] or g["_id"], "walletIds": list(overlap)})

    if not groups:
        cols = get_biz_dates(_N_DISPLAY, end_date) if mode == "daily" else ["Semanal", "Mensal", "Anual"]
        return jsonify({"rows": [], "dates": cols, "mode": mode})

    all_group_wids = {wid for g in groups for wid in g["walletIds"]}

    if mode == "summary":
        n_extra = 252
    else:
        n_extra = _N_DISPLAY

    display_dates = get_biz_dates(n_extra, end_date)
    all_dates     = get_biz_dates(n_extra + _HIST_DAYS, end_date)

    nav_data = _fetch_nav_data(company_id, all_group_wids, all_dates, published_only)

    # Aggregate returns per group (average of member wallets)
    group_returns = {}
    for g in groups:
        gid = g["id"]
        group_returns[gid] = {}
        for d in all_dates:
            rets = [nav_data.get(wid, {}).get(d) for wid in g["walletIds"]]
            rets = [r for r in rets if r is not None]
            if rets:
                group_returns[gid][d] = sum(rets) / len(rets)

    # Cross-group EWMA benchmark
    all_gret = {}
    for gid, dm in group_returns.items():
        for d, ret in dm.items():
            all_gret.setdefault(d, []).append(ret)
    all_dates_sorted = sorted(all_gret.keys())

    def _bench_for_dates(target_dates):
        b = {}
        for d in target_dates:
            hist = []
            for hd in all_dates_sorted:
                if hd > d:
                    break
                vals  = all_gret.get(hd, [])
                clean = _remove_outliers(vals)
                if clean:
                    hist.append(sum(clean) / len(clean))
            if hist:
                c, lo, hi = _ewma_band(hist)
                b[d] = {"center": c, "lower": lo, "upper": hi}
        return b

    comments = _load_comments()

    if mode == "summary":
        today         = display_dates[-1]
        weekly_dates  = get_biz_dates(5,   today)
        monthly_dates = get_biz_dates(21,  today)
        annual_dates  = get_biz_dates(252, today)
        bench_w = _bench_for_dates(weekly_dates)
        bench_m = _bench_for_dates(monthly_dates)
        bench_a = _bench_for_dates(annual_dates)

        def _cmp(dm, dates):
            rets = [dm.get(d) for d in dates if dm.get(d) is not None]
            return _compound(rets) if rets else None

        def _bcmp(bench, dates):
            cs = [bench.get(d, {}).get("center") for d in dates if bench.get(d, {}).get("center") is not None]
            ls = [bench.get(d, {}).get("lower")  for d in dates if bench.get(d, {}).get("lower")  is not None]
            us = [bench.get(d, {}).get("upper")  for d in dates if bench.get(d, {}).get("upper")  is not None]
            return (_compound(cs) if cs else None, _compound(ls) if ls else None, _compound(us) if us else None)

        def _cell(label, ret, bc):
            c, lo, hi = bc
            is_a = bool(ret is not None and lo is not None and hi is not None and (ret < lo or ret > hi))
            return {"date": label, "return": ret, "center": c, "lower": lo, "upper": hi, "isAnomaly": is_a}

        rows = []
        for g in groups:
            gid = g["id"]
            dm  = group_returns.get(gid, {})
            cells = [
                _cell("Semanal", _cmp(dm, weekly_dates),  _bcmp(bench_w, weekly_dates)),
                _cell("Mensal",  _cmp(dm, monthly_dates), _bcmp(bench_m, monthly_dates)),
                _cell("Anual",   _cmp(dm, annual_dates),  _bcmp(bench_a, annual_dates)),
            ]
            is_any = any(c["isAnomaly"] for c in cells)
            if anomaly_only and not is_any:
                continue
            rows.append({"id": gid, "name": g["name"],
                         "cells": cells, "isAnomalyToday": is_any,
                         "comment": comments.get(f"grupo_{gid}", "")})
        rows.sort(key=lambda r: (not r["isAnomalyToday"], r["name"]))
        return jsonify({"rows": rows, "dates": ["Semanal", "Mensal", "Anual"], "mode": mode})

    bench = _bench_for_dates(display_dates)
    rows  = []
    for g in groups:
        gid   = g["id"]
        dm    = group_returns.get(gid, {})
        cells = _make_cells(dm, display_dates, bench)
        is_anom_today = cells[-1]["isAnomaly"] if cells else False
        if anomaly_only and not any(c["isAnomaly"] for c in cells):
            continue
        rows.append({"id": gid, "name": g["name"],
                     "cells": cells, "isAnomalyToday": is_anom_today,
                     "comment": comments.get(f"grupo_{gid}", "")})
    rows.sort(key=lambda r: (not r["isAnomalyToday"], r["name"]))
    return jsonify({"rows": rows, "dates": display_dates, "mode": mode})


# ── Benchmarks (por tipo de ativo) ────────────────────────────────────────────

@bp.route("/api/banda-rentabilidades/benchmarks")
def get_benchmarks():
    company_id   = request.args.get("companyId", "")
    end_date     = request.args.get("endDate") or None
    mode         = request.args.get("mode", "daily")
    anomaly_only = request.args.get("anomalyOnly", "0") == "1"

    if not company_id:
        return jsonify({"rows": [], "dates": [], "mode": mode})

    wids = _wallets_for_company(company_id)
    if not wids:
        return jsonify({"rows": [], "dates": [], "mode": mode})

    n_extra       = 252 if mode == "summary" else _N_DISPLAY
    display_dates = get_biz_dates(n_extra, end_date)
    all_dates     = get_biz_dates(n_extra + _HIST_DAYS + 1, end_date)

    sec_returns = _compute_sec_returns(company_id, wids, all_dates)
    if not sec_returns:
        cols = display_dates if mode == "daily" else ["Semanal", "Mensal", "Anual"]
        return jsonify({"rows": [], "dates": cols, "mode": mode})

    meta = _sec_meta(list(sec_returns.keys()))

    # Group returns by (secType, date) — cross-sectional
    type_date_rets = {}
    for sid, date_map in sec_returns.items():
        st = meta.get(sid, {}).get("type", "Outros")
        for d, ret in date_map.items():
            type_date_rets.setdefault(st, {}).setdefault(d, []).append(ret)

    all_dates_sorted = sorted({d for dm in type_date_rets.values() for d in dm})

    def _bench_value(st, d):
        """Cross-sectional mean (outliers removed) for type st on date d."""
        vals  = type_date_rets.get(st, {}).get(d, [])
        clean = _remove_outliers(vals)
        return (sum(clean) / len(clean), len(vals)) if clean else (None, 0)

    def _type_ewma_band(st, up_to_date):
        hist = []
        for hd in all_dates_sorted:
            if hd > up_to_date:
                break
            v, _ = _bench_value(st, hd)
            if v is not None:
                hist.append(v)
        return _ewma_band(hist) if hist else (None, None, None)

    rows = []

    if mode == "summary":
        today         = display_dates[-1]
        weekly_dates  = get_biz_dates(5,   today)
        monthly_dates = get_biz_dates(21,  today)
        annual_dates  = get_biz_dates(252, today)

        for st in sorted(type_date_rets.keys()):
            def _period_return(dates_range):
                rets = [_bench_value(st, d)[0] for d in dates_range]
                rets = [r for r in rets if r is not None]
                return _compound(rets) if rets else None

            c_w, lo_w, hi_w = _type_ewma_band(st, weekly_dates[-1]  if weekly_dates  else today)
            c_m, lo_m, hi_m = _type_ewma_band(st, monthly_dates[-1] if monthly_dates else today)
            c_a, lo_a, hi_a = _type_ewma_band(st, annual_dates[-1]  if annual_dates  else today)

            def _cell(label, ret, c, lo, hi):
                is_a = bool(ret is not None and lo is not None and hi is not None and (ret < lo or ret > hi))
                return {"date": label, "return": ret, "center": c, "lower": lo, "upper": hi,
                        "count": 0, "isAnomaly": is_a}

            cells = [
                _cell("Semanal", _period_return(weekly_dates),  c_w, lo_w, hi_w),
                _cell("Mensal",  _period_return(monthly_dates), c_m, lo_m, hi_m),
                _cell("Anual",   _period_return(annual_dates),  c_a, lo_a, hi_a),
            ]
            is_any = any(c["isAnomaly"] for c in cells)
            if anomaly_only and not is_any:
                continue
            rows.append({"id": st, "name": st, "cells": cells, "isAnomalyToday": is_any, "comment": ""})

        rows.sort(key=lambda r: (not r["isAnomalyToday"], r["name"]))
        return jsonify({"rows": rows, "dates": ["Semanal", "Mensal", "Anual"], "mode": mode})

    # ── Daily mode ─────────────────────────────────────────────────────────────
    for st in sorted(type_date_rets.keys()):
        c_today, lo_today, hi_today = _type_ewma_band(st, display_dates[-1])
        cells = []
        is_anom_today = False
        for d in display_dates:
            v, count = _bench_value(st, d)
            is_a = bool(v is not None and lo_today is not None and hi_today is not None
                        and (v < lo_today or v > hi_today))
            if d == display_dates[-1] and is_a:
                is_anom_today = True
            cells.append({"date": d, "return": round(v, 8) if v is not None else None,
                          "center": c_today, "lower": lo_today, "upper": hi_today,
                          "count": count, "isAnomaly": is_a})
        if anomaly_only and not any(c["isAnomaly"] for c in cells):
            continue
        rows.append({"id": st, "name": st, "cells": cells,
                     "isAnomalyToday": is_anom_today, "comment": ""})

    rows.sort(key=lambda r: (not r["isAnomalyToday"], r["name"]))
    return jsonify({"rows": rows, "dates": display_dates, "mode": mode})


# ── Detalhe ao clicar num ativo discrepante ───────────────────────────────────

_ATIVO_DETAIL_WINDOW_DU = 40  # janela de busca (du) — folga sobre os 12 pontos pedidos


@bp.route("/api/banda-rentabilidades/ativo-detail")
def get_ativo_detail():
    """Contexto:
    Histórico dos últimos ~12 pontos de um ativo numa carteira, para o
    drill-down de uma anomalia. Substitui `db.processedPosition.find(...)
    .sort().limit(12)` por carteira — a API não tem um "últimos N documentos"
    por carteira, então buscamos uma janela de `_ATIVO_DETAIL_WINDOW_DU` dias
    úteis (folga generosa sobre os 12 pontos) via `api_processed_positions_multi`
    e pegamos, por carteira, as 12 datas mais recentes que realmente têm
    posição — equivalente na prática (carteiras ativas têm posição quase
    todo dia útil), mas não é garantia estrita como o `limit(12)` do Mongo
    era para carteiras com histórico muito esparso.

    Pseudocódigo:
      1. Janela de datas ≤ endDate (ou último du).
      2. 1 chamada `api_processed_positions_multi` (fan-out por data, 1 empresa).
      3. Por carteira: filtra as datas com o `securityId` pedido, ordena
         desc., mantém as 12 mais recentes.
      4. Calcula retorno (contribuição e PU) entre pontos consecutivos, igual
         à lógica original.
    """
    security_id = request.args.get("securityId", "")
    company_id  = request.args.get("companyId", "")
    end_date    = request.args.get("endDate") or None

    if not security_id or not company_id:
        return jsonify({"history": []})

    wids     = _wallets_for_company(company_id)
    end_d    = end_date or get_biz_dates(1)[0]
    history  = []

    window_dates = get_biz_dates(_ATIVO_DETAIL_WINDOW_DU, end_d)
    by_date = api_processed_positions_multi(window_dates, company_ids=[company_id])
    # {wid: {date: doc}}
    by_wallet_date = {}
    for d, docs in by_date.items():
        for doc in docs:
            wid = doc.get("walletId", "")
            if wid in wids:
                by_wallet_date.setdefault(wid, {})[d] = doc

    for wid, date_map in by_wallet_date.items():
        recent_dates = sorted(date_map.keys(), reverse=True)[:12]
        positions = [date_map[d] for d in recent_dates]

        # Check if this wallet has this security
        has_sec = any(
            str(s.get("securityId", "")) == security_id
            for doc in positions
            for s in doc.get("securities", [])
        )
        if not has_sec:
            continue

        raw = []
        for doc in positions:
            d   = str(doc.get("positionDate", ""))[:10]
            sec = next((s for s in doc.get("securities", [])
                        if str(s.get("securityId", "")) == security_id), None)
            if sec:
                raw.append({
                    "date": d, "wid": wid,
                    "pu":   sec.get("pu"),
                    "qty":  sec.get("quantity"),
                    "tc":   sec.get("totalContribution"),
                    "ec":   sec.get("eventContribution") or 0,
                })

        for i in range(len(raw) - 1):
            cur = raw[i]
            prv = raw[i + 1]
            f_pu  = prv["pu"]
            f_qty = prv["qty"]
            c_pu  = cur["pu"]
            tc    = cur["tc"]
            ec    = cur["ec"] or 0
            qty   = cur["qty"]
            f_bal = (f_pu * f_qty) if (f_pu and f_qty) else None

            ret_c = None
            try:
                ret_c = round(tc / f_bal, 8) if (tc is not None and f_bal) else None
            except (TypeError, ZeroDivisionError):
                pass
            ret_pu = None
            try:
                ep     = (ec / qty) if (qty and qty != 0) else 0
                ret_pu = round((c_pu + ep) / f_pu - 1, 8) if (c_pu and f_pu) else None
            except (TypeError, ZeroDivisionError):
                pass

            history.append({
                "date":       cur["date"],
                "walletId":   wid,
                "pu":         c_pu,
                "quantity":   qty,
                "retContrib": ret_c,
                "retPU":      ret_pu,
            })

    history.sort(key=lambda x: (x["date"], x["walletId"]), reverse=True)
    return jsonify({"history": history[:30]})


# ── Comentários ───────────────────────────────────────────────────────────────

@bp.route("/api/banda-rentabilidades/comment", methods=["POST"])
def save_comment():
    data    = request.get_json() or {}
    key     = data.get("key", "").strip()
    comment = data.get("comment", "").strip()
    if not key:
        return jsonify({"ok": False}), 400
    comments = _load_comments()
    if comment:
        comments[key] = comment
    else:
        comments.pop(key, None)
    _save_comments(comments)
    return jsonify({"ok": True})
