from flask import Blueprint, render_template, jsonify, request
from db import db, get_company_filter
from bson import ObjectId
from bson.errors import InvalidId
import json, os, re

bp = Blueprint("precificacao", __name__)

SAVED_LISTS_FILE  = os.path.join(os.path.dirname(__file__), "..", "data", "precificacao_lists.json")
CONFIG_FILE       = os.path.join(os.path.dirname(__file__), "..", "data", "precificacao_config.json")

_DEFAULT_CONFIG = {
    "benchmarks": [
        {"id": "66fc4a71e88f2f542b805639", "name": "CDI"}
    ]
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_lists():
    if not os.path.exists(SAVED_LISTS_FILE):
        return []
    with open(SAVED_LISTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_lists(lists):
    with open(SAVED_LISTS_FILE, "w", encoding="utf-8") as f:
        json.dump(lists, f, indent=2, ensure_ascii=False)


def _load_config():
    if not os.path.exists(CONFIG_FILE):
        return dict(_DEFAULT_CONFIG)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def _extract_hp(doc):
    """Return the historyPrice sub-document from a securityPrices doc."""
    if not doc:
        return {}
    raw = doc.get("historyPrice")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list) and raw:
        try:
            return max(raw, key=lambda x: str(x.get("date", "")))
        except Exception:
            return raw[0] if raw else {}
    return {}


def _extract_all_hp(doc):
    """Return a list of all historyPrice entries from a securityPrices doc."""
    if not doc:
        return []
    raw = doc.get("historyPrice")
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return raw
    return []


def _find_price(sec_id_str, query_extra, proj):
    """Find one price doc trying ObjectId then string for securityId."""
    try:
        oid = ObjectId(sec_id_str)
        doc = next(iter(db.securityPrices.find(
            {"securityId": oid, **query_extra}, proj
        ).sort("historyPrice.date", -1).limit(1)), None)
        if doc:
            return doc
    except Exception:
        pass
    return next(iter(db.securityPrices.find(
        {"securityId": sec_id_str, **query_extra}, proj
    ).sort("historyPrice.date", -1).limit(1)), None)


def _find_all_prices(sec_id_str, query_extra, proj, ascending=True):
    """Find all price docs trying ObjectId then string for securityId."""
    direction = 1 if ascending else -1
    try:
        oid  = ObjectId(sec_id_str)
        docs = list(db.securityPrices.find(
            {"securityId": oid, **query_extra}, proj
        ).sort("historyPrice.date", direction))
        if docs:
            return docs
    except Exception:
        pass
    return list(db.securityPrices.find(
        {"securityId": sec_id_str, **query_extra}, proj
    ).sort("historyPrice.date", direction))


def _get_most_recent_position(wallet_id, position_date=None):
    """Get processedPosition for a wallet at a given date (or most recent).
    Returns (pos_doc, position_date_str) or (None, None).
    """
    or_q = [{"walletId": wallet_id}]
    try:
        or_q.append({"walletId": ObjectId(wallet_id)})
    except Exception:
        pass

    query = {"$or": or_q}
    if position_date:
        query["positionDate"] = position_date

    pos_doc = next(iter(
        db.processedPosition.find(
            query,
            {"securities": 1, "positionDate": 1}
        ).sort("positionDate", -1).limit(1)
    ), None)

    if not pos_doc:
        return None, None
    return pos_doc, str(pos_doc.get("positionDate", ""))[:10]


# ── Page ───────────────────────────────────────────────────────────────────────

@bp.route("/precificacao")
def index():
    cfg        = _load_config()
    benchmarks = cfg.get("benchmarks", [])
    return render_template("precificacao.html", benchmarks=benchmarks)


# ── API: Companies & Wallets ──────────────────────────────────────────────────

@bp.route("/api/precificacao/companies")
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


@bp.route("/api/precificacao/wallets")
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


@bp.route("/api/precificacao/latest-position-date")
def get_latest_position_date():
    """Return the most recent positionDate for a wallet."""
    wallet_id = request.args.get("walletId", "").strip()
    if not wallet_id:
        return jsonify({"date": None})
    _, pos_date = _get_most_recent_position(wallet_id)
    return jsonify({"date": pos_date})


# ── API: Wallet Securities (from processedPosition) ──────────────────────────

@bp.route("/api/precificacao/wallet-securities")
def get_wallet_securities():
    wallet_id     = request.args.get("walletId", "").strip()
    pos_date_arg  = request.args.get("positionDate", "").strip()
    if not wallet_id:
        return jsonify({"error": "walletId obrigatório"}), 400

    pos_doc, position_date = _get_most_recent_position(wallet_id, pos_date_arg or None)
    if not pos_doc:
        return jsonify({"securities": [], "positionDate": None})

    raw_secs = pos_doc.get("securities", [])

    # Batch-fetch security names from securities collection
    oid_ids = []
    for s in raw_secs:
        sid = s.get("securityId")
        if sid:
            try:
                oid_ids.append(ObjectId(str(sid)))
            except Exception:
                pass

    sec_meta = {}
    for sec in db.securities.find({"_id": {"$in": oid_ids}}, {"beehusName": 1, "mainId": 1}):
        sec_meta[str(sec["_id"])] = {
            "beehusName": sec.get("beehusName", ""),
            "mainId":     sec.get("mainId", ""),
        }

    result = []
    for s in raw_secs:
        sid = str(s.get("securityId", ""))
        qty = s.get("quantity")
        pu  = s.get("pu")
        balance = None
        if qty is not None and pu is not None:
            try:
                balance = round(float(qty) * float(pu), 2)
            except Exception:
                pass
        meta = sec_meta.get(sid, {})
        result.append({
            "securityId":  sid,
            "beehusName":  meta.get("beehusName", sid),
            "mainId":      meta.get("mainId", ""),
            "pricingType": s.get("pricingType", ""),
            "quantity":    float(qty) if qty is not None else None,
            "pu":          float(pu)  if pu  is not None else None,
            "balance":     balance,
        })

    result.sort(key=lambda x: (x["beehusName"] or "").lower())
    return jsonify({"securities": result, "positionDate": position_date})


# ── API: Security detail ─────────────────────────────────────────────────────
# Merges: securities collection (metadata) + securityPrices (lastPU)
#       + processedPosition (pricingType, quantity, pu) from most recent position

@bp.route("/api/precificacao/security/<sec_id>")
def get_security(sec_id):
    try:
        oid = ObjectId(sec_id)
    except Exception:
        return jsonify({"error": "ID inválido"}), 400

    sec = db.securities.find_one({"_id": oid}, {
        "beehusName": 1, "mainId": 1, "securityType": 1, "type": 1,
        "currency": 1, "maturityDate": 1, "emissionDate": 1, "issuer": 1,
        "indexer": 1, "indexerPercentual": 1, "yield": 1,
    })
    if not sec:
        return jsonify({"error": "Ativo não encontrado"}), 404

    # Last PU from securityPrices
    last_price = _find_price(sec_id, {}, {"historyPrice": 1})
    hp = _extract_hp(last_price)

    # Position data from processedPosition
    wallet_id    = request.args.get("walletId", "").strip()
    pos_date_arg = request.args.get("positionDate", "").strip()
    pos_pricing_type = ""
    pos_quantity     = None
    pos_pu           = None

    if wallet_id:
        pos_doc, _ = _get_most_recent_position(wallet_id, pos_date_arg or None)
        if pos_doc:
            for ps in pos_doc.get("securities", []):
                if str(ps.get("securityId", "")) == sec_id:
                    pos_pricing_type = ps.get("pricingType", "")
                    pos_quantity     = float(ps["quantity"]) if ps.get("quantity") is not None else None
                    pos_pu           = float(ps["pu"])       if ps.get("pu")       is not None else None
                    break

    return jsonify({
        "id":               str(sec["_id"]),
        "beehusName":       sec.get("beehusName", ""),
        "mainId":           sec.get("mainId", ""),
        "securityType":     sec.get("securityType", ""),
        "type":             sec.get("type", ""),
        "currency":         sec.get("currency", ""),
        "maturityDate":     str(sec.get("maturityDate", ""))[:10] if sec.get("maturityDate") else None,
        "emissionDate":     str(sec.get("emissionDate", ""))[:10] if sec.get("emissionDate") else None,
        "issuer":           sec.get("issuer", ""),
        "indexer":          sec.get("indexer", ""),
        "indexerPercentual": sec.get("indexerPercentual"),
        "yield":            sec.get("yield"),
        "lastPU":           hp.get("value"),
        "lastPUDate":       str(hp.get("date", ""))[:10] if hp.get("date") else None,
        "pricingType":      pos_pricing_type,
        "posQuantity":      pos_quantity,
        "posPU":            pos_pu,
    })


# ── API: Security search ─────────────────────────────────────────────────────

@bp.route("/api/precificacao/search")
def search_securities():
    q = request.args.get("q", "").strip()
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

    results = []
    for sec in db.securities.find({"$or": or_clauses}, {
        "beehusName": 1, "mainId": 1, "securityType": 1,
        "indexer": 1, "indexerPercentual": 1,
    }).limit(20):
        results.append({
            "id":               str(sec["_id"]),
            "beehusName":       sec.get("beehusName", ""),
            "mainId":           sec.get("mainId", ""),
            "securityType":     sec.get("securityType", ""),
            "indexer":          sec.get("indexer", ""),
            "indexerPercentual": sec.get("indexerPercentual"),
        })
    return jsonify({"results": results})


# ── API: Transactions ─────────────────────────────────────────────────────────

@bp.route("/api/precificacao/security-transactions")
def get_security_transactions():
    sec_id        = request.args.get("securityId", "").strip()
    wallet_id     = request.args.get("walletId",   "").strip()
    position_date = request.args.get("positionDate", "").strip()
    if not sec_id or not wallet_id:
        return jsonify({"transactions": []})

    # Match securityId as both ObjectId and string
    sid_q = [{"securityId": sec_id}]
    try:
        sid_q.append({"securityId": ObjectId(sec_id)})
    except Exception:
        pass

    # Match walletId as both ObjectId and string
    wid_q = [{"walletId": wallet_id}]
    try:
        wid_q.append({"walletId": ObjectId(wallet_id)})
    except Exception:
        pass

    query = {
        "$or": sid_q,
        "$and": [{"$or": wid_q}],
        "beehusTransactionType": {"$in": ["buySell", "securityTransfer"]},
    }
    if position_date:
        query["liquidationDate"] = {"$lte": position_date}

    txns = list(db.transactions.find(
        query,
        {"liquidationDate": 1, "beehusTransactionType": 1,
         "quantity": 1, "price": 1, "balance": 1, "description": 1}
    ).sort("liquidationDate", 1))

    result = []
    for txn in txns:
        qty = txn.get("quantity")
        if qty is None:
            continue
        try:
            if float(qty) == 0:
                continue
        except (ValueError, TypeError):
            continue
        prc = txn.get("price")
        q   = float(qty)
        p   = float(prc) if prc is not None else None
        result.append({
            "liquidationDate":      str(txn.get("liquidationDate", "") or "")[:10],
            "beehusTransactionType": txn.get("beehusTransactionType", ""),
            "quantity":             q,
            "price":                p,
            "balance":              round(q * p, 2) if p is not None else None,
            "description":          txn.get("description", ""),
        })
    return jsonify({"transactions": result})


# ── API: Calculate ────────────────────────────────────────────────────────────

@bp.route("/api/precificacao/calcular", methods=["POST"])
def calcular():
    data            = request.get_json() or {}
    securities_list = data.get("securities", [])

    if not securities_list:
        return jsonify({"error": "Parâmetros inválidos"}), 400

    # Find each security's last available PU
    sec_last = {}
    for s in securities_list:
        sec_id = s.get("id")
        if not sec_id or sec_id in sec_last:
            continue
        last_price = _find_price(sec_id, {}, {"historyPrice": 1})
        last_hp    = _extract_hp(last_price)
        if last_hp.get("value"):
            sec_last[sec_id] = (float(last_hp["value"]), str(last_hp.get("date", ""))[:10])

    # Earliest start date to limit benchmark fetch
    all_start_dates = [v[1] for v in sec_last.values() if v[1]]
    global_start    = min(all_start_dates) if all_start_dates else ""

    # Build factor map for each unique benchmark
    unique_bm_ids = {s.get("benchmarkId") for s in securities_list if s.get("benchmarkId")}
    has_curva = any(s.get("calcType") in ("pre_fixado_curva", "inflacao_curva") for s in securities_list)
    if has_curva and not unique_bm_ids:
        cfg = _load_config()
        default_bms = cfg.get("benchmarks", [])
        if default_bms:
            unique_bm_ids.add(default_bms[0]["id"])

    bm_factor_map = {}
    bm_errors     = {}

    for bm_id in unique_bm_ids:
        query_extra = {"historyPrice.date": {"$gte": global_start}} if global_start else {}
        docs = _find_all_prices(bm_id, query_extra, {"historyPrice": 1}, ascending=True)
        if not docs:
            bm_errors[bm_id] = "Preços do benchmark não encontrados"
            continue

        all_hps = []
        for d in docs:
            all_hps.extend(_extract_all_hp(d))

        seen = set()
        series = []
        for hp in sorted(all_hps, key=lambda x: str(x.get("date", ""))):
            dt = str(hp.get("date", ""))[:10]
            if dt and dt not in seen:
                seen.add(dt)
                series.append((dt, hp))

        if not series:
            bm_errors[bm_id] = "Datas do benchmark não encontradas"
            continue

        fmap = {}
        for i, (dt, hp) in enumerate(series):
            if hp.get("rentability") is not None:
                fmap[dt] = float(hp["rentability"])
            elif i > 0:
                prev_val = series[i - 1][1].get("value")
                curr_val = hp.get("value")
                if prev_val and curr_val:
                    fmap[dt] = float(curr_val) / float(prev_val) - 1
        bm_factor_map[bm_id] = fmap

    # Roll PU forward for each security
    results = []
    for sec_inp in securities_list:
        sec_id       = sec_inp.get("id")
        calc_type    = sec_inp.get("calcType", "pos_fixado")
        bm_id        = sec_inp.get("benchmarkId", "")
        bm_name      = sec_inp.get("benchmarkName", "")
        wallet_id    = sec_inp.get("walletId", "")
        wallet_name  = sec_inp.get("walletName", "")
        pricing_type = sec_inp.get("pricingType", "")

        if calc_type == "pos_fixado":
            if sec_id not in sec_last:
                results.append({"securityId": sec_id, "beehusName": sec_inp.get("beehusName", ""),
                                "calcType": calc_type, "error": "Sem PU disponível"})
                continue
            current_pu, last_pu_date = sec_last[sec_id]
            if not bm_id or bm_id in bm_errors:
                results.append({"securityId": sec_id, "beehusName": sec_inp.get("beehusName", ""),
                                "benchmarkName": bm_name, "calcType": calc_type,
                                "error": bm_errors.get(bm_id, "Benchmark não configurado")})
                continue
            idx_pct = sec_inp.get("indexerPercentual")
            if idx_pct is None:
                results.append({"securityId": sec_id, "beehusName": sec_inp.get("beehusName", ""),
                                "calcType": calc_type, "error": "indexerPercentual ausente"})
                continue
            idx_pct_f  = float(idx_pct) / 100.0
            factor_map = bm_factor_map.get(bm_id, {})
            for dt in sorted(dt for dt in factor_map if dt > last_pu_date):
                factor = factor_map[dt]
                new_pu = current_pu * (1 + factor * idx_pct_f)
                results.append({
                    "securityId": sec_id, "beehusName": sec_inp.get("beehusName", ""),
                    "calcType": calc_type, "walletId": wallet_id, "walletName": wallet_name,
                    "pricingType": pricing_type, "benchmarkName": bm_name,
                    "date": dt, "benchmarkFactor": round(1 + factor, 10),
                    "indexerPercentual": idx_pct_f, "pu": round(new_pu, 8),
                })
                current_pu = new_pu

        elif calc_type in ("pre_fixado_curva", "inflacao_curva"):
            txn_list = sec_inp.get("transactions", [])
            if not txn_list:
                results.append({"securityId": sec_id, "beehusName": sec_inp.get("beehusName", ""),
                                "calcType": calc_type, "walletId": wallet_id, "walletName": wallet_name,
                                "pricingType": pricing_type, "error": "Nenhuma transação informada"})
                continue

            cal_fmap = next(iter(bm_factor_map.values()), {})
            if not cal_fmap:
                results.append({"securityId": sec_id, "beehusName": sec_inp.get("beehusName", ""),
                                "calcType": calc_type, "walletId": wallet_id, "walletName": wallet_name,
                                "pricingType": pricing_type,
                                "error": "Nenhum calendário disponível — configure ao menos um benchmark"})
                continue

            lots = []
            for t in txn_list:
                qty, yld = t.get("quantity"), t.get("yield")
                if qty is None or yld is None:
                    continue
                try:
                    lots.append({"quantity": float(qty), "yield": float(yld)})
                except (ValueError, TypeError):
                    continue

            if not lots:
                results.append({"securityId": sec_id, "beehusName": sec_inp.get("beehusName", ""),
                                "calcType": calc_type, "walletId": wallet_id, "walletName": wallet_name,
                                "pricingType": pricing_type, "error": "Nenhuma transação válida"})
                continue

            # Determine initial PU — priority: posPU/positionDate > initialPU > securityPrices
            pos_pu_raw    = sec_inp.get("posPU")
            pos_date_raw  = str(sec_inp.get("positionDate") or "")[:10]
            init_pu_raw   = sec_inp.get("initialPU")
            init_date_raw = str(sec_inp.get("initialPUDate") or "")[:10]

            last_pu, last_pu_date = None, None
            if pos_pu_raw is not None and pos_date_raw:
                try:
                    last_pu, last_pu_date = float(pos_pu_raw), pos_date_raw
                except (ValueError, TypeError):
                    pass
            if (not last_pu or not last_pu_date) and init_pu_raw is not None and init_date_raw:
                try:
                    last_pu, last_pu_date = float(init_pu_raw), init_date_raw
                except (ValueError, TypeError):
                    pass
            if not last_pu or not last_pu_date:
                last_price_doc = _find_price(sec_id, {}, {"historyPrice": 1})
                last_hp = _extract_hp(last_price_doc)
                last_pu = float(last_hp["value"]) if last_hp.get("value") else None
                last_pu_date = str(last_hp.get("date", ""))[:10] if last_hp.get("date") else None

            if not last_pu or not last_pu_date:
                results.append({"securityId": sec_id, "beehusName": sec_inp.get("beehusName", ""),
                                "calcType": calc_type, "walletId": wallet_id, "walletName": wallet_name,
                                "pricingType": pricing_type,
                                "error": "Nenhum PU disponível — informe o PU inicial"})
                continue

            sorted_cal  = sorted(cal_fmap.keys())
            date_to_idx = {d: i for i, d in enumerate(sorted_cal)}

            if last_pu_date in date_to_idx:
                base_idx = date_to_idx[last_pu_date]
            else:
                base_idx = next((date_to_idx[d] for d in sorted_cal if d >= last_pu_date), None)
            if base_idx is None:
                results.append({"securityId": sec_id, "beehusName": sec_inp.get("beehusName", ""),
                                "calcType": calc_type, "walletId": wallet_id, "walletName": wallet_name,
                                "pricingType": pricing_type, "error": "Data do último PU fora do calendário"})
                continue

            active_lots = [l for l in lots if l["quantity"] > 0]
            total_qty   = sum(l["quantity"] for l in active_lots)
            if not active_lots or total_qty <= 0:
                results.append({"securityId": sec_id, "beehusName": sec_inp.get("beehusName", ""),
                                "calcType": calc_type, "walletId": wallet_id, "walletName": wallet_name,
                                "pricingType": pricing_type, "error": "Nenhuma transação ativa"})
                continue

            w_yield      = sum(l["quantity"] * l["yield"] for l in active_lots) / total_qty
            daily_factor = (1 + w_yield / 100) ** (1 / 252)

            # For inflacao_curva, accumulate benchmark factor on top of yield accrual
            inf_factor_map = {}
            if calc_type == "inflacao_curva" and bm_id:
                inf_factor_map = bm_factor_map.get(bm_id, {})
                if not inf_factor_map:
                    results.append({"securityId": sec_id, "beehusName": sec_inp.get("beehusName", ""),
                                    "calcType": calc_type, "walletId": wallet_id, "walletName": wallet_name,
                                    "pricingType": pricing_type, "benchmarkName": bm_name,
                                    "error": bm_errors.get(bm_id, "Benchmark de inflação não encontrado")})
                    continue

            # Use the default calendar benchmark for annualized yield display
            cal_bm_id = bm_id if calc_type == "inflacao_curva" else next(iter(bm_factor_map), None)
            cal_bm_fmap = bm_factor_map.get(cal_bm_id, {}) if cal_bm_id else {}

            accum_bm = 1.0
            for dt in sorted_cal:
                dt_idx = date_to_idx[dt]
                if dt_idx <= base_idx:
                    continue
                n = dt_idx - base_idx
                if calc_type == "inflacao_curva" and dt in inf_factor_map:
                    accum_bm *= (1 + inf_factor_map[dt])
                pu_val = last_pu * accum_bm * (daily_factor ** n)
                # Annualize daily rentability: ((1 + daily)^252 - 1) * 100
                daily_rent = cal_bm_fmap.get(dt)
                bm_yield_ann = round(((1 + daily_rent) ** 252 - 1) * 100, 4) if daily_rent is not None else None
                # Daily factor for inflacao_curva = yield daily factor × benchmark daily factor
                if calc_type == "inflacao_curva":
                    bm_daily = inf_factor_map.get(dt, 0)
                    combined_daily = daily_factor * (1 + bm_daily)
                    display_factor = round(combined_daily, 10)
                else:
                    display_factor = round(daily_factor, 10)

                results.append({
                    "securityId": sec_id, "beehusName": sec_inp.get("beehusName", ""),
                    "calcType": calc_type, "walletId": wallet_id, "walletName": wallet_name,
                    "pricingType": pricing_type, "date": dt,
                    "benchmarkName": bm_name if calc_type == "inflacao_curva" else "",
                    "benchmarkFactor": display_factor,
                    "benchmarkYield": bm_yield_ann,
                    "pu": round(pu_val, 8),
                })
        else:
            results.append({"securityId": sec_id, "beehusName": sec_inp.get("beehusName", ""),
                            "calcType": calc_type, "error": f"Tipo de cálculo desconhecido: {calc_type}"})

    return jsonify({"results": results})


# ── Saved lists ───────────────────────────────────────────────────────────────

@bp.route("/api/precificacao/lists")
def get_lists():
    return jsonify(_load_lists())


@bp.route("/api/precificacao/lists", methods=["POST"])
def save_list():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    secs = data.get("securities", [])
    if not name:
        return jsonify({"error": "Nome obrigatório"}), 400
    if not secs:
        return jsonify({"error": "Lista vazia"}), 400

    lists = _load_lists()
    for existing in lists:
        if existing.get("name") == name:
            existing["securities"] = secs
            _write_lists(lists)
            return jsonify({"ok": True, "overwritten": True})

    lists.append({"name": name, "securities": secs})
    _write_lists(lists)
    return jsonify({"ok": True, "overwritten": False})


@bp.route("/api/precificacao/lists/<name>", methods=["DELETE"])
def delete_list(name):
    lists = _load_lists()
    _write_lists([l for l in lists if l.get("name") != name])
    return jsonify({"ok": True})


# ── Config (benchmarks) ──────────────────────────────────────────────────────

@bp.route("/api/precificacao/config")
def get_config():
    cfg        = _load_config()
    benchmarks = cfg.get("benchmarks", [])

    enriched = []
    for b in benchmarks:
        entry = {"id": b["id"], "name": b.get("name", "")}
        try:
            oid = ObjectId(b["id"])
            row = _find_price(b["id"], {}, {"historyPrice": 1})
            entry["lastDate"] = str(_extract_hp(row).get("date", ""))[:10] if row else None
            sec = db.securities.find_one({"_id": oid}, {"beehusName": 1, "mainId": 1})
            if sec:
                entry["beehusName"] = sec.get("beehusName", "")
                entry["mainId"]     = sec.get("mainId", "")
        except Exception:
            entry["lastDate"] = None
        enriched.append(entry)
    return jsonify({"benchmarks": enriched})


@bp.route("/api/precificacao/config", methods=["POST"])
def save_config():
    data       = request.get_json() or {}
    benchmarks = data.get("benchmarks", [])
    clean = [
        {"id": str(b["id"]).strip(), "name": str(b.get("name", "")).strip()}
        for b in benchmarks if b.get("id") and b.get("name")
    ]
    _write_config({"benchmarks": clean})
    return jsonify({"ok": True})
