from flask import Blueprint, render_template, jsonify, request
from db import db, get_biz_dates, get_company_filter, valid_wallet_ids
from bson import ObjectId as _OID
from datetime import date as _date, timedelta, datetime as _dt, timezone
import json, os, statistics

_THRESHOLDS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "rentability_thresholds.json")


def _load_thresholds():
    if not os.path.exists(_THRESHOLDS_FILE):
        return {}
    with open(_THRESHOLDS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


bp = Blueprint("conciliacao", __name__)

_NUM_DATES = 10


_valid_wallet_ids = valid_wallet_ids


def _mismatch_query(company_id, valid_wallet_ids, date=None):
    """MongoDB query for navPackages where returnNavPerShare ≠ returnContribution."""
    q = {
        "companyId": company_id,
        "walletId":  {"$in": list(valid_wallet_ids)},
        "trashed":   {"$ne": True},
        "$expr":     {"$ne": ["$returnNavPerShare", "$returnContribution"]},
    }
    if date is not None:
        if isinstance(date, list):
            q["positionDate"] = {"$in": date}
        else:
            q["positionDate"] = date
    return q


# ── Routes ─────────────────────────────────────────────────────────────────────

@bp.route("/conciliacao")
def index():
    companies = sorted(
        [{"id": str(c["_id"]), "name": c.get("name", str(c["_id"]))}
         for c in db.companies.find({}, {"name": 1})],
        key=lambda c: c["name"],
    )
    cf = get_company_filter()
    if cf:
        companies = [c for c in companies if c["id"] in cf]
    return render_template("conciliacao.html", companies=companies)


@bp.route("/api/conciliacao/dates")
def get_dates():
    company_id = request.args.get("companyId", "")
    end_date   = request.args.get("endDate") or None
    wallet_ids = _valid_wallet_ids()

    # If no explicit endDate, use the most recent navPackages date for this company's wallets
    if not end_date:
        latest = db.navPackages.find_one(
            {"companyId": company_id, "walletId": {"$in": list(wallet_ids)}, "trashed": {"$ne": True}},
            {"positionDate": 1},
            sort=[("positionDate", -1)]
        )
        if latest and latest.get("positionDate"):
            end_date = str(latest["positionDate"])[:10]

    dates = get_biz_dates(_NUM_DATES, end_date)

    totals = {}
    for doc in db.navPackages.aggregate([
        {"$match": _mismatch_query(company_id, wallet_ids, dates)},
        {"$group": {"_id": "$positionDate", "n": {"$sum": 1}}},
    ]):
        d = str(doc["_id"])[:10]
        if d:
            totals[d] = doc["n"]

    cards = [{"date": d, "total": totals.get(d, 0)} for d in dates]
    return jsonify({"cards": cards})


@bp.route("/api/conciliacao/rows")
def get_rows():
    company_id = request.args.get("companyId", "")
    date       = request.args.get("date", "")

    wallet_ids = _valid_wallet_ids()
    wallet_names = {
        str(w["_id"]): w.get("name", "")
        for w in db.wallets.find({}, {"name": 1})
        if str(w["_id"]) in wallet_ids
    }

    proj = {
        "walletId": 1, "nav": 1, "navPerShare": 1, "amount": 1,
        "inAndOutFlows": 1, "returnNavPerShare": 1, "returnContribution": 1,
        "formerNav": 1,
    }

    # Pre-fetch former NAV for each wallet (previous navPackage before the selected date)
    former_nav_map = {}  # {walletId: nav}
    for prev in db.navPackages.aggregate([
        {"$match": {"walletId": {"$in": list(wallet_ids)}, "positionDate": {"$lt": date}, "trashed": {"$ne": True}}},
        {"$sort": {"positionDate": -1}},
        {"$group": {"_id": "$walletId", "nav": {"$first": "$nav"}}},
    ]):
        former_nav_map[str(prev["_id"])] = prev.get("nav")

    rows = []
    for pkg in db.navPackages.find(_mismatch_query(company_id, wallet_ids, date), proj):
        wid = str(pkg.get("walletId", ""))
        former_nav = pkg.get("formerNav") or former_nav_map.get(wid)
        rows.append({
            "walletId":           wid,
            "walletName":         wallet_names.get(wid, wid),
            "nav":                pkg.get("nav"),
            "navPerShare":        pkg.get("navPerShare"),
            "amount":             pkg.get("amount"),
            "inAndOutFlows":      pkg.get("inAndOutFlows"),
            "returnNavPerShare":  pkg.get("returnNavPerShare"),
            "returnContribution": pkg.get("returnContribution"),
            "formerNav":          former_nav,
        })

    rows.sort(key=lambda x: x["walletName"])
    return jsonify({"rows": rows, "date": date})


@bp.route("/api/conciliacao/wallet-detail")
def get_wallet_detail():
    wallet_id = request.args.get("walletId", "")
    date      = request.args.get("date", "")

    # Current position
    current_pos = db.processedPosition.find_one(
        {"walletId": wallet_id, "positionDate": date},
        {"securities": 1}
    )

    # Most recent position strictly before the current date
    former_docs = list(
        db.processedPosition.find(
            {"walletId": wallet_id, "positionDate": {"$lt": date}},
            {"securities": 1, "positionDate": 1}
        ).sort("positionDate", -1).limit(1)
    )
    former_pos  = former_docs[0] if former_docs else None
    former_date = str(former_pos.get("positionDate", ""))[:10] if former_pos else None

    # Build former lookup: {securityId: {pu, quantity}}
    former_map = {}
    for sec in (former_pos or {}).get("securities", []):
        sid = str(sec.get("securityId", ""))
        former_map[sid] = {
            "pu":       sec.get("pu"),
            "quantity": sec.get("quantity"),
        }

    # ── Transaction totals per security (liquidationDate == date) ───────────────
    txn_by_security = {}   # {securityId: total_balance}
    for doc in db.transactions.aggregate([
        {"$match": {"walletId": wallet_id, "liquidationDate": date, "balance": {"$ne": None}}},
        {"$group": {"_id": "$securityId", "total": {"$sum": "$balance"}}},
    ]):
        sid = str(doc["_id"] or "")
        txn_by_security[sid] = float(doc["total"])

    securities = []
    for sec in (current_pos or {}).get("securities", []):
        sid = str(sec.get("securityId", ""))
        pu  = sec.get("pu")
        qty = sec.get("quantity")
        balance = round(pu * qty, 6) if (pu is not None and qty is not None) else None

        total_contrib = sec.get("totalContribution")
        event_contrib = sec.get("eventContribution") or 0

        f      = former_map.get(sid, {})
        f_pu   = f.get("pu")
        f_qty  = f.get("quantity")
        f_bal  = round(f_pu * f_qty, 6) if (f_pu is not None and f_qty is not None) else None
        amt_diff = round(qty - f_qty, 6) if (qty is not None and f_qty is not None) else None

        try:
            return_pu = round(pu / f_pu - 1, 8) if (pu is not None and f_pu and f_pu != 0) else None
        except (TypeError, ZeroDivisionError):
            return_pu = None

        try:
            return_contrib = round(total_contrib / f_bal, 8) if (total_contrib is not None and f_bal and f_bal != 0) else None
        except (TypeError, ZeroDivisionError):
            return_contrib = None

        diff_rent = round(return_pu - return_contrib, 8) if (return_pu is not None and return_contrib is not None) else None

        txn_bal = txn_by_security.get(sid)   # None if no transactions for this security

        securities.append({
            "securityId":        sid,
            "beehusName":        sec.get("beehusName", ""),
            "pricingType":       sec.get("pricingType", ""),
            "pu":                pu,
            "executionPrice":    sec.get("executionPrice"),
            "quantity":          qty,
            "balance":           balance,
            "totalContribution": total_contrib,
            "formerPu":          f_pu,
            "formerQuantity":    f_qty,
            "formerBalance":     f_bal,
            "amountDifference":  amt_diff,
            "returnPU":          return_pu,
            "returnContrib":     return_contrib,
            "diffRent":          diff_rent,
            "transactionBalance": txn_bal,
            "dailyContribution":    sec.get("dailyContribution"),
            "intradayContribution": sec.get("intradayContribution"),
            "eventContribution":    event_contrib,
        })

    securities.sort(key=lambda s: s["beehusName"])

    current_sids = {s["securityId"] for s in securities}
    unmatched_txn_total = sum(
        bal for sid, bal in txn_by_security.items() if sid not in current_sids
    ) or None
    matched_txn_total = sum(
        bal for sid, bal in txn_by_security.items() if sid in current_sids
    ) or None

    # ── Cash accounts ──────────────────────────────────────────────────────────
    former_cash  = _sum_cash(wallet_id, former_date)
    current_cash = _sum_cash(wallet_id, date)

    # Total transactions = sum of all per-security transaction balances
    total_txns = sum(txn_by_security.values())

    projected_cash = former_cash + total_txns if former_cash is not None else None
    cash_diff = (
        projected_cash - current_cash
        if projected_cash is not None and current_cash is not None
        else None
    )

    # ── Alerts ─────────────────────────────────────────────────────────────────
    alerts = []

    # Alert 1: transactions with unidentified type
    if db.transactions.count_documents(
        {"walletId": wallet_id, "liquidationDate": date, "beehusTransactionType": None}
    ) > 0:
        alerts.append({"id": "unidentified_txns", "message": "Existem transações não identificadas"})

    # Alert 2: projected cash ≠ current cash
    if projected_cash is not None and current_cash is not None:
        if round(projected_cash - current_cash, 2) != 0:
            alerts.append({"id": "cash_mismatch", "message": "Há uma divergência no caixa"})

    return jsonify({
        "securities":            securities,
        "formerDate":            former_date,
        "date":                  date,
        "formerCash":            former_cash,
        "totalTransactions":     total_txns,
        "projectedCash":         projected_cash,
        "currentCash":           current_cash,
        "cashDifference":        cash_diff,
        "unmatchedTransactions": unmatched_txn_total,
        "matchedTransactions":   matched_txn_total,
        "alerts":                alerts,
    })



@bp.route("/api/conciliacao/transactions")
def get_transactions():
    wallet_id = request.args.get("walletId", "")
    date      = request.args.get("date", "")

    security_names = {
        str(s["_id"]): s.get("beehusName", "")
        for s in db.securities.find({}, {"beehusName": 1})
    }

    proj = {
        "operationDate": 1, "liquidationDate": 1, "securityId": 1,
        "beehusTransactionType": 1, "quantity": 1, "price": 1,
        "balance": 1, "description": 1,
    }

    txns = []
    for txn in db.transactions.find(
        {"walletId": wallet_id, "liquidationDate": date}, proj
    ).sort("operationDate", 1):
        sid = str(txn.get("securityId", "") or "")
        txns.append({
            "operationDate":         str(txn.get("operationDate",   "") or "")[:10],
            "liquidationDate":       str(txn.get("liquidationDate", "") or "")[:10],
            "securityId":            sid,
            "securityName":          security_names.get(sid, ""),
            "beehusTransactionType": txn.get("beehusTransactionType", ""),
            "quantity":              txn.get("quantity"),
            "price":                 txn.get("price"),
            "balance":               txn.get("balance"),
            "description":           txn.get("description", ""),
        })

    return jsonify({"transactions": txns, "date": date})


# ── Diagnostic engine (V2 — 6-step sequential funnel) ──────────────────────────

_EVENT_TYPES    = {"amortization", "coupon"}
_TOLERANCE_ABS  = 0.01
_TOLERANCE_REL  = 0.01   # 1%


def _approx(a, b):
    """Return True if a ≈ b within absolute or relative tolerance."""
    if a is None or b is None:
        return False
    diff = abs(a - b)
    return diff <= _TOLERANCE_ABS or diff <= abs(b) * _TOLERANCE_REL


def _sum_cash(wallet_id, pos_date):
    """Sum cashAccounts.values for a wallet on a specific date."""
    if not pos_date:
        return None
    target = pos_date[:10]
    result = list(db.cashAccounts.aggregate([
        {"$match": {"walletId": wallet_id}},
        {"$unwind": "$values"},
        {"$match": {"$expr": {"$eq": [{"$substrBytes": [{"$toString": "$values.date"}, 0, 10]}, target]}}},
        {"$group": {"_id": None, "total": {"$sum": "$values.value"}}},
    ]))
    return float(result[0]["total"]) if result else None


@bp.route("/api/conciliacao/diagnose")
def diagnose():
    wallet_id = request.args.get("walletId", "")
    date      = request.args.get("date", "")

    # ── Data loading ────────────────────────────────────────────────────────────

    # NAV package
    nav_pkg = db.navPackages.find_one(
        {"walletId": wallet_id, "positionDate": date, "trashed": {"$ne": True}},
        {"returnNavPerShare": 1, "returnContribution": 1, "nav": 1,
         "navPerShare": 1, "inAndOutFlows": 1, "amount": 1, "formerNav": 1}
    )
    if not nav_pkg:
        return jsonify({"error": "navPackage não encontrado"}), 404

    return_nav_ps  = nav_pkg.get("returnNavPerShare", 0) or 0
    return_contrib = nav_pkg.get("returnContribution", 0) or 0
    former_nav     = nav_pkg.get("formerNav")

    if former_nav is None:
        prev_pkg   = db.navPackages.find_one(
            {"walletId": wallet_id, "positionDate": {"$lt": date}, "trashed": {"$ne": True}},
            {"nav": 1}, sort=[("positionDate", -1)]
        )
        former_nav = prev_pkg.get("nav") if prev_pkg else None

    gap_pct  = return_nav_ps - return_contrib
    gap_cash = round(gap_pct * former_nav, 2) if former_nav is not None else None

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 1 — Detect
    # ═══════════════════════════════════════════════════════════════════════════
    step1 = {
        "status":             "gap" if abs(gap_pct) > 1e-10 else "ok",
        "returnNavPerShare":  return_nav_ps,
        "returnContribution": return_contrib,
        "gapPct":             gap_pct,
        "gapCash":            gap_cash,
        "formerNav":          former_nav,
    }

    _skipped = {"status": "skipped"}
    if step1["status"] == "ok":
        return jsonify({
            "walletId": wallet_id, "date": date,
            "step1": step1, "step2": _skipped, "step3": _skipped,
            "step4": _skipped, "step5": _skipped, "step6": _skipped,
        })

    # ── Processed positions ─────────────────────────────────────────────────────
    pos_doc = db.processedPosition.find_one(
        {"walletId": wallet_id, "positionDate": date}, {"securities": 1}
    )
    former_doc = db.processedPosition.find_one(
        {"walletId": wallet_id, "positionDate": {"$lt": date}},
        {"securities": 1, "positionDate": 1}, sort=[("positionDate", -1)]
    )
    former_date = str(former_doc.get("positionDate", ""))[:10] if former_doc else None
    former_map = {
        str(s.get("securityId", "")): {"pu": s.get("pu"), "quantity": s.get("quantity")}
        for s in (former_doc or {}).get("securities", [])
    }

    # ── Transactions grouped by securityId ──────────────────────────────────────
    txns_by_security = {}   # {securityId: [{"type", "balance"}]}
    wallet_txns      = []   # transactions with no securityId
    all_txns_flat    = []   # every transaction (for Step 4 / Step 5)
    for doc in db.transactions.find(
        {"walletId": wallet_id, "liquidationDate": date},
        {"securityId": 1, "beehusTransactionType": 1, "balance": 1}
    ):
        entry = {"type": doc.get("beehusTransactionType"), "balance": doc.get("balance"),
                 "securityId": str(doc.get("securityId", "") or "")}
        all_txns_flat.append(entry)
        sid = entry["securityId"]
        if sid:
            txns_by_security.setdefault(sid, []).append(entry)
        else:
            wallet_txns.append(entry)

    # ── Security info (settlement days + securityType) ──────────────────────────
    current_secs   = (pos_doc or {}).get("securities", [])
    current_sec_ids = {str(s.get("securityId", "")) for s in current_secs}

    # Gather all securityIds we need info for (position + transactions)
    all_sec_ids_raw = set()
    for s in current_secs:
        if s.get("securityId"):
            all_sec_ids_raw.add(s["securityId"])
    for sid in txns_by_security:
        all_sec_ids_raw.add(sid)

    sec_ids_query = []
    for sid in all_sec_ids_raw:
        sec_ids_query.append(sid)
        try:
            sec_ids_query.append(_OID(str(sid)))
        except Exception:
            pass

    sec_info = {
        str(s["_id"]): s
        for s in db.securities.find(
            {"_id": {"$in": sec_ids_query}},
            {"redemptionNavDays": 1, "redemptionSettlementDays": 1,
             "subscriptionNavDays": 1, "subscriptionSettlementDays": 1,
             "securityType": 1, "beehusName": 1}
        )
    }

    # ── Active provisions ───────────────────────────────────────────────────────
    prov_map = {}    # {securityId: total active provision amount}
    for doc in db.provisions.aggregate([
        {"$match": {"walletId": wallet_id, "initialDate": {"$lte": date},
                    "liquidationDate": {"$gt": date}, "securityId": {"$ne": None}}},
        {"$group": {"_id": "$securityId", "total": {"$sum": {"$ifNull": ["$amount", 0]}}}},
    ]):
        sid_p = str(doc["_id"])
        if sid_p:
            prov_map[sid_p] = doc["total"]

    # Provisions created or liquidated on this date (for Step 2 condition c)
    prov_lifecycle_sids = set()
    for doc in db.provisions.find(
        {"walletId": wallet_id, "securityId": {"$ne": None},
         "$or": [{"initialDate": date}, {"liquidationDate": date}]},
        {"securityId": 1}
    ):
        prov_lifecycle_sids.add(str(doc["securityId"]))

    # ── Event transactions by security (for Step 3.2) ───────────────────────────
    event_txns_by_sec = {}   # {securityId: [{"type", "balance"}]}
    for t in all_txns_flat:
        if t["type"] in _EVENT_TYPES and t["securityId"]:
            event_txns_by_sec.setdefault(t["securityId"], []).append(t)

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 2 — Eliminate
    # ═══════════════════════════════════════════════════════════════════════════
    eliminated = []
    suspects   = []

    for sec in current_secs:
        sid  = str(sec.get("securityId", ""))
        pu   = sec.get("pu")
        qty  = sec.get("quantity")
        f    = former_map.get(sid, {})
        f_pu = f.get("pu")
        f_qty = f.get("quantity")
        f_bal = round(f_pu * f_qty, 6) if (f_pu is not None and f_qty is not None) else None
        amt_diff = round(qty - (f_qty or 0), 6) if qty is not None else None

        exec_price = sec.get("executionPrice")
        event_c    = sec.get("eventContribution") or 0
        total_c    = sec.get("totalContribution")

        # Compute rentab PU and rentab Contribution
        try:
            ret_pu = round(pu / f_pu - 1, 8) if (pu and f_pu) else None
        except (TypeError, ZeroDivisionError):
            ret_pu = None
        try:
            ret_c = round(total_c / f_bal, 8) if (total_c is not None and f_bal) else None
        except (TypeError, ZeroDivisionError):
            ret_c = None
        diff_rent = round(ret_pu - ret_c, 8) if (ret_pu is not None and ret_c is not None) else None

        sec_txns = txns_by_security.get(sid, [])

        # Elimination conditions (ALL must be true)
        cond_a = amt_diff is not None and amt_diff == 0     # no quantity change
        cond_b = not sec_txns                               # no transactions
        cond_c = sid not in prov_lifecycle_sids              # no provision lifecycle event
        cond_d = diff_rent is not None and diff_rent == 0   # rentab equal

        # If rentab differs, check if event txns explain it (coupon/amortization)
        if not cond_d and diff_rent and f_bal:
            ev_txns = event_txns_by_sec.get(sid, [])
            if ev_txns:
                ev_total = round(sum(float(t.get("balance", 0) or 0) for t in ev_txns), 2)
                expected_event_cash = round(-diff_rent * f_bal, 2)
                if _approx(ev_total, expected_event_cash):
                    cond_d = True   # explained by event transactions

        sec_entry = {
            "securityId":     sid,
            "name":           sec.get("beehusName", ""),
            "amountDiff":     amt_diff,
            "diffRent":       diff_rent,
            "formerBalance":  f_bal,
            "pu":             pu,
            "formerPu":       f_pu,
            "executionPrice": exec_price,
            "quantity":       qty,
            "formerQuantity": f_qty,
            "eventContribution": event_c,
            "totalContribution": total_c,
            "securityType":   sec_info.get(sid, {}).get("securityType", ""),
            "failedConditions": [],
        }

        if cond_a and cond_b and cond_c and cond_d:
            eliminated.append(sec_entry)
        else:
            if not cond_a:
                sec_entry["failedConditions"].append("amountDifference")
            if not cond_b:
                sec_entry["failedConditions"].append("hasTransactions")
            if not cond_c:
                sec_entry["failedConditions"].append("provisionLifecycle")
            if not cond_d:
                sec_entry["failedConditions"].append("rentabilityDifference")
            suspects.append(sec_entry)

    step2 = {
        "status":          "done",
        "eliminatedCount": len(eliminated),
        "suspectCount":    len(suspects),
        "suspects":        suspects,
    }

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 3 — Diagnose Securities
    # ═══════════════════════════════════════════════════════════════════════════
    step3_securities = []

    for sec_entry in suspects:
        sid        = sec_entry["securityId"]
        amt_diff   = sec_entry["amountDiff"]
        diff_rent  = sec_entry["diffRent"]
        f_bal      = sec_entry["formerBalance"]
        pu         = sec_entry["pu"]
        exec_price = sec_entry["executionPrice"]
        price      = exec_price or pu or 0
        sec_type   = sec_entry["securityType"]
        sec_txns   = txns_by_security.get(sid, [])

        diag = {
            "securityId": sid,
            "name":       sec_entry["name"],
            "amountDiff": amt_diff,
            "diffRent":   diff_rent,
            "eliminated": False,
            "step3_1":    None,
            "step3_2":    None,
            "step3_3":    None,
        }

        # ── 3.1 Amount Difference ──────────────────────────────────────────────
        if amt_diff:
            info   = sec_info.get(sid, {})
            settle = info.get("redemptionSettlementDays" if amt_diff < 0 else "subscriptionSettlementDays") or 0
            nav_d  = info.get("redemptionNavDays"        if amt_diff < 0 else "subscriptionNavDays")        or 0
            offset = settle - nav_d

            if offset == 0:
                # Expect transaction
                has_buysell = any(t.get("type") == "buySell" for t in sec_txns)
                if has_buysell:
                    diag["step3_1"] = {"status": "ok", "offset": offset,
                                       "detail": "Transação buySell encontrada"}
                else:
                    impact = round(abs(amt_diff) * price, 2)
                    diag["step3_1"] = {"status": "flag", "flag": "MISSING_TRANSACTION",
                                       "offset": offset, "impact": impact,
                                       "detail": "Liquidação imediata mas transação buySell não encontrada"}
            else:
                # Expect provision
                if sid in prov_map:
                    diag["step3_1"] = {"status": "ok", "offset": offset,
                                       "detail": "Provisão ativa encontrada",
                                       "provisionAmount": round(float(prov_map[sid]), 2)}
                else:
                    impact = round(abs(amt_diff) * price, 2)
                    flag_detail = "Liquidação futura" if offset > 0 else "Nav futuro"
                    # Compute provision dates
                    try:
                        pos_date_obj = _date.fromisoformat(date)
                        if offset > 0:
                            prov_initial = date
                            prov_liquidation = (pos_date_obj + timedelta(days=offset)).isoformat()
                        else:
                            prov_initial = (pos_date_obj + timedelta(days=offset)).isoformat()
                            prov_liquidation = date
                    except Exception:
                        prov_initial = date
                        prov_liquidation = date
                    prov_type = "buySell"
                    diag["step3_1"] = {"status": "flag", "flag": "MISSING_PROVISION",
                                       "offset": offset, "impact": impact,
                                       "detail": f"{flag_detail} (offset={offset}) mas provisão não encontrada",
                                       "provisionData": {
                                           "initialDate":    prov_initial,
                                           "liquidationDate": prov_liquidation,
                                           "provisionType":  prov_type,
                                           "balance":        impact if amt_diff > 0 else -impact,
                                       }}

        # ── 3.2 Rentability Difference ─────────────────────────────────────────
        if diff_rent and f_bal:
            expected_event_cash = round(-diff_rent * f_bal, 2)
            ev_txns = event_txns_by_sec.get(sid, [])
            ev_total = round(sum(float(t.get("balance", 0) or 0) for t in ev_txns), 2)

            if ev_txns and _approx(ev_total, expected_event_cash):
                diag["step3_2"] = {"status": "eliminated",
                                   "detail": "Diferença explicada por transação de evento",
                                   "expectedEventCash": expected_event_cash,
                                   "eventTransactionTotal": ev_total}
                diag["eliminated"] = True
            elif ev_txns:
                diag["step3_2"] = {"status": "flag", "flag": "WRONG_EVENT_BALANCE",
                                   "detail": "Transação de evento existe mas valor diverge",
                                   "expectedEventCash": expected_event_cash,
                                   "eventTransactionTotal": ev_total,
                                   "impact": round(abs(ev_total - expected_event_cash), 2)}
            elif sid in prov_map and _approx(float(prov_map[sid]), expected_event_cash):
                diag["step3_2"] = {"status": "eliminated",
                                   "detail": "Diferença explicada por provisão (provável evento anunciado)",
                                   "expectedEventCash": expected_event_cash,
                                   "provisionAmount": round(float(prov_map[sid]), 2)}
                diag["eliminated"] = True
            elif sid in prov_map:
                diag["step3_2"] = {"status": "flag", "flag": "WRONG_PROVISION_AMOUNT",
                                   "detail": "Provisão existe mas valor diverge do evento esperado",
                                   "expectedEventCash": expected_event_cash,
                                   "provisionAmount": round(float(prov_map[sid]), 2),
                                   "impact": round(abs(float(prov_map[sid]) - expected_event_cash), 2)}
            else:
                diag["step3_2"] = {"status": "flag", "flag": "MISSING_EVENT",
                                   "detail": "Sem transação de evento e sem provisão",
                                   "expectedEventCash": expected_event_cash,
                                   "impact": abs(expected_event_cash)}

        # ── 3.3 Withholding Tax / Execution Price ──────────────────────────────
        if amt_diff and not diag["eliminated"]:
            buysell_txns = [t for t in sec_txns if t.get("type") == "buySell"]
            if buysell_txns:
                actual_bal    = round(sum(float(t.get("balance", 0) or 0) for t in buysell_txns), 2)
                expected_val  = round(amt_diff * price, 2)
                if _approx(expected_val, actual_bal):
                    diag["step3_3"] = {"status": "ok",
                                       "detail": "Valor da transação confere com esperado",
                                       "expectedValue": expected_val,
                                       "actualBalance": round(actual_bal, 2)}
                else:
                    diff_val = round(abs(expected_val - actual_bal), 2)
                    if sec_type == "brazilianFund":
                        diag["step3_3"] = {"status": "flag", "flag": "WITHHOLDING_TAX",
                                           "detail": "Provável IR retido na fonte (brazilianFunds)",
                                           "expectedValue": expected_val,
                                           "actualBalance": round(actual_bal, 2),
                                           "impact": diff_val}
                    elif exec_price is None or (pu is not None and exec_price == pu):
                        # expectedExecPrice = actualBalance / amountDiff
                        expected_exec_price = round(-actual_bal / amt_diff, 6) if amt_diff else None
                        diag["step3_3"] = {"status": "flag", "flag": "MISSING_EXECUTION_PRICE",
                                           "detail": "Preço de execução ausente (sistema usou PU como fallback)",
                                           "expectedValue": expected_val,
                                           "actualBalance": round(actual_bal, 2),
                                           "pu": pu, "executionPrice": exec_price,
                                           "expectedExecPrice": expected_exec_price,
                                           "impact": diff_val}
                    else:
                        diag["step3_3"] = {"status": "flag", "flag": "WRONG_TRANSACTION_VALUE",
                                           "detail": "Valor da transação diverge do esperado",
                                           "expectedValue": expected_val,
                                           "actualBalance": round(actual_bal, 2),
                                           "impact": diff_val}

        step3_securities.append(diag)

    step3 = {
        "status":     "done",
        "securities": step3_securities,
    }

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 4 — Diagnose Transactions
    # ═══════════════════════════════════════════════════════════════════════════

    # 4.1 Unclassified transactions
    unclassified = [
        {"securityId": t["securityId"], "balance": t.get("balance")}
        for t in all_txns_flat if not t.get("type")
    ]

    # 4.2 Wrong security identification
    wrong_security = []
    for sid, txns in txns_by_security.items():
        if sid in current_sec_ids:
            continue
        # This securityId is in transactions but not in position
        info = sec_info.get(sid, {})
        has_provision = sid in prov_map

        # Check: new purchase with subscriptionNavDays > 0
        sub_nav = info.get("subscriptionNavDays") or 0
        if sub_nav > 0 and has_provision:
            verdict = "LEGITIMATE_NEW_PURCHASE"
            reason  = f"Compra nova com subscriptionNavDays={sub_nav} e provisão existente"
        else:
            # Check: sold security with offset > 0
            red_settle = info.get("redemptionSettlementDays") or 0
            red_nav    = info.get("redemptionNavDays") or 0
            offset     = red_settle - red_nav
            if offset > 0 and has_provision:
                verdict = "LEGITIMATE_POST_SALE"
                reason  = f"Venda com offset={offset} e provisão existente"
            elif has_provision:
                verdict = "LEGITIMATE_WITH_PROVISION"
                reason  = "Security não está na posição mas provisão existe"
            else:
                verdict = "WRONG_SECURITY"
                reason  = "Security não encontrado na posição e sem provisão correspondente"

        total_bal = round(sum(float(t.get("balance", 0) or 0) for t in txns), 2)
        wrong_security.append({
            "securityId":   sid,
            "securityName": info.get("beehusName", ""),
            "balance":      total_bal,
            "txnCount":     len(txns),
            "verdict":      verdict,
            "reason":       reason,
        })

    # 4.3 Probable misclassified transactions
    # Collect missing values from Step 3 flags, then for each transaction check if
    # its balance matches a missing value from a DIFFERENT security.
    misclassified = []
    step3_missing = {}  # {securityId: [(name, impact, flag)]}
    for diag in step3_securities:
        sid = diag["securityId"]
        name = diag["name"]
        for key in ("step3_1", "step3_2", "step3_3"):
            s = diag.get(key)
            if s and s.get("status") == "flag" and s.get("impact"):
                step3_missing.setdefault(sid, []).append(
                    (name, round(float(s["impact"]), 2), s["flag"]))

    if step3_missing:
        for t in all_txns_flat:
            t_bal = round(abs(float(t.get("balance", 0) or 0)), 2)
            if not t_bal:
                continue
            t_sid = t.get("securityId", "")
            t_type = t.get("type")
            matches = []
            for miss_sid, entries in step3_missing.items():
                if miss_sid == t_sid:
                    continue
                for miss_name, miss_impact, miss_flag in entries:
                    if _approx(t_bal, miss_impact):
                        matches.append({
                            "securityId":   miss_sid,
                            "securityName": miss_name,
                            "flag":         miss_flag,
                            "expectedValue": miss_impact,
                        })
            if matches:
                misclassified.append({
                    "txnBalance":    round(float(t.get("balance", 0) or 0), 2),
                    "txnSecurityId": t_sid or None,
                    "txnType":       t_type,
                    "matches":       matches,
                })

    step4 = {
        "status":        "done",
        "unclassified":  unclassified,
        "wrongSecurity": wrong_security,
        "misclassified": misclassified,
    }

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 5 — Cash Validation
    # ═══════════════════════════════════════════════════════════════════════════
    former_cash  = _sum_cash(wallet_id, former_date)
    current_cash = _sum_cash(wallet_id, date)
    total_txn_balance = round(sum(float(t.get("balance", 0) or 0) for t in all_txns_flat), 2)
    projected_cash = round(former_cash + total_txn_balance, 2) if former_cash is not None else None
    cash_diff = round(projected_cash - current_cash, 2) if (projected_cash is not None and current_cash is not None) else None

    if cash_diff is not None and round(cash_diff, 2) == 0:
        cash_diagnosis = "consistent"
        cash_status    = "ok"
    elif unclassified:
        cash_diagnosis = "unclassified_txns"
        cash_status    = "warning"
    elif not all_txns_flat:
        cash_diagnosis = "missing_cash_txn"
        cash_status    = "warning"
    elif cash_diff is not None:
        cash_diagnosis = "value_error"
        cash_status    = "warning"
    else:
        cash_diagnosis = "no_data"
        cash_status    = "ok"

    step5 = {
        "status":            cash_status,
        "formerCash":        former_cash,
        "currentCash":       current_cash,
        "totalTransactions": total_txn_balance,
        "projectedCash":     projected_cash,
        "cashDiff":          cash_diff,
        "diagnosis":         cash_diagnosis,
    }

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 6 — Rentability Anomalies
    # ═══════════════════════════════════════════════════════════════════════════

    # 6.1 Wallet-level 3-sigma check
    wallet_anomaly = None
    history = list(db.navPackages.find(
        {"walletId": wallet_id, "positionDate": {"$lt": date}, "trashed": {"$ne": True}},
        {"returnNavPerShare": 1},
    ).sort("positionDate", -1).limit(60))
    returns = [h["returnNavPerShare"] for h in history if h.get("returnNavPerShare") is not None]

    if len(returns) >= 3:
        mean   = statistics.mean(returns)
        stddev = statistics.stdev(returns)
        lower  = mean - 3 * stddev
        upper  = mean + 3 * stddev
        is_anomaly = return_nav_ps < lower or return_nav_ps > upper
        wallet_anomaly = {
            "isAnomaly":     is_anomaly,
            "currentReturn": return_nav_ps,
            "mean":          round(mean, 8),
            "stdDev":        round(stddev, 8),
            "lowerBound":    round(lower, 8),
            "upperBound":    round(upper, 8),
            "sampleSize":    len(returns),
        }

    # 6.2 Per-security anomalies (from rentability_thresholds.json)
    security_anomalies = []
    thresholds = _load_thresholds()
    if thresholds:
        for sec_entry in suspects:
            sid   = sec_entry["securityId"]
            th    = thresholds.get(sid)
            ret   = None
            # Compute rentabPU for this security
            pu    = sec_entry.get("pu")
            f_pu  = sec_entry.get("formerPu")
            try:
                ret = pu / f_pu - 1 if (pu and f_pu) else None
            except (TypeError, ZeroDivisionError):
                ret = None
            if th and ret is not None:
                lb = th.get("lowerBound")
                ub = th.get("upperBound")
                is_anom = (lb is not None and ub is not None) and (ret < lb or ret > ub)
                if is_anom:
                    security_anomalies.append({
                        "securityId":    sid,
                        "securityName":  sec_entry.get("name", ""),
                        "currentReturn": round(ret, 8),
                        "mean":          th.get("mean"),
                        "stdDev":        th.get("stdDev"),
                        "isAnomaly":     True,
                    })

    step6_status = "ok"
    if (wallet_anomaly and wallet_anomaly["isAnomaly"]) or any(a["isAnomaly"] for a in security_anomalies):
        step6_status = "warning"

    step6 = {
        "status":             step6_status,
        "walletAnomaly":      wallet_anomaly,
        "securityAnomalies":  security_anomalies,
    }

    # ═══════════════════════════════════════════════════════════════════════════
    # Response
    # ═══════════════════════════════════════════════════════════════════════════
    return jsonify({
        "walletId": wallet_id,
        "date":     date,
        "step1":    step1,
        "step2":    step2,
        "step3":    step3,
        "step4":    step4,
        "step5":    step5,
        "step6":    step6,
    })


@bp.route("/api/conciliacao/provisions")
def get_provisions():
    wallet_id = request.args.get("walletId", "")
    date      = request.args.get("date", "")

    # Fetch all active provisions for this wallet on this date
    # Active = initialDate <= date AND liquidationDate > date
    raw = list(db.provisions.find(
        {"walletId": wallet_id, "initialDate": {"$lte": date}, "liquidationDate": {"$gt": date}},
        {"securityId": 1, "initialDate": 1, "liquidationDate": 1,
         "amount": 1, "provisionType": 1, "description": 1}
    ))

    # Enrich with security names
    sec_ids = list({str(p.get("securityId", "")) for p in raw if p.get("securityId")})
    name_map = {}
    if sec_ids:
        oid_list = []
        for s in sec_ids:
            try:
                oid_list.append(_OID(s))
            except Exception:
                pass
        for sec in db.securities.find({"_id": {"$in": oid_list}}, {"beehusName": 1}):
            name_map[str(sec["_id"])] = sec.get("beehusName", "")

    provisions = []
    for p in raw:
        sid = str(p.get("securityId", "") or "")
        amt = p.get("amount")
        provisions.append({
            "securityId":    sid,
            "securityName":  name_map.get(sid, ""),
            "initialDate":   str(p.get("initialDate", ""))[:10],
            "liquidationDate": str(p.get("liquidationDate", ""))[:10],
            "amount":        float(amt) if amt is not None else None,
            "provisionType": p.get("provisionType", ""),
            "description":   p.get("description", ""),
        })

    provisions.sort(key=lambda p: (p["liquidationDate"], p["securityName"]))
    total = round(sum(p["amount"] for p in provisions if p["amount"] is not None), 2)

    return jsonify({"provisions": provisions, "total": total})


@bp.route("/api/conciliacao/diagnose/feedback", methods=["POST"])
def diagnose_feedback():
    data = request.get_json() or {}
    db.diagnosticFeedback.insert_one({
        "walletId":        data.get("walletId"),
        "date":            data.get("date"),
        "gapCash":         data.get("gapCash"),
        "scenarioIndex":   data.get("scenarioIndex"),
        "confirmed":       data.get("confirmed"),
        "userNote":        data.get("userNote", ""),
        "flagsInScenario": data.get("flagsInScenario", []),
        "resolvedAt":      _dt.now(timezone.utc).isoformat(),
    })
    return jsonify({"ok": True})


# ── Transaction type mapping per flag ──────────────────────────────────────────
_FLAG_TXN_TYPE = {
    "MISSING_TRANSACTION":      "buySell",
    "MISSING_PROVISION":        None,           # provision, not transaction
    "WRONG_EVENT_BALANCE":      None,           # existing txn has wrong value
    "WRONG_PROVISION_AMOUNT":   None,           # provision issue
    "MISSING_EVENT":            "coupon",       # amortization/coupon event
    "WITHHOLDING_TAX":          "buySell",
    "MISSING_EXECUTION_PRICE":  "buySell",
    "WRONG_TRANSACTION_VALUE":  "buySell",
    "WRONG_SECURITY":           None,
    "UNCLASSIFIED_TRANSACTION": None,           # existing txn needs reclassification
    "CASH_MISMATCH":            "gainsExpenses",
}

_FLAG_DESCRIPTIONS = {
    "MISSING_TRANSACTION":      "Correção: transação buySell faltante",
    "MISSING_EVENT":            "Correção: transação de evento faltante",
    "WITHHOLDING_TAX":          "Correção: ajuste IR retido na fonte",
    "MISSING_EXECUTION_PRICE":  "Correção: ajuste preço de execução",
    "WRONG_TRANSACTION_VALUE":  "Correção: valor de transação divergente",
    "CASH_MISMATCH":            "Correção: transação de caixa faltante",
}


@bp.route("/api/conciliacao/generate-transactions", methods=["POST"])
def generate_transactions():
    """Build a transaction file from accepted diagnostic items."""
    data      = request.get_json() or {}
    wallet_id = data.get("walletId", "")
    date      = data.get("date", "")
    items     = data.get("items", [])

    if not wallet_id or not date or not items:
        return jsonify({"error": "walletId, date e items são obrigatórios"}), 400

    # Fetch wallet info for entityId, currencyId, companyId
    wallet = db.wallets.find_one({"_id": wallet_id}, {"entityId": 1, "currencyId": 1, "companyId": 1})
    if not wallet:
        # Try with ObjectId
        try:
            wallet = db.wallets.find_one({"_id": _OID(wallet_id)}, {"entityId": 1, "currencyId": 1, "companyId": 1})
        except Exception:
            pass
    if not wallet:
        return jsonify({"error": "Wallet não encontrada"}), 404

    company_id  = str(wallet.get("companyId", ""))
    entity_id   = str(wallet.get("entityId", ""))
    currency_id = str(wallet.get("currencyId", "BRL"))

    transactions = []
    for item in items:
        flag     = item.get("flag", "")
        txn_type = _FLAG_TXN_TYPE.get(flag)

        # Skip flags that don't produce transactions
        if txn_type is None:
            continue

        sec_name = item.get("securityName", "")
        base_desc = _FLAG_DESCRIPTIONS.get(flag, f"Correção: {flag}")
        description = f"{base_desc} — {sec_name}" if sec_name and sec_name not in ("(carteira)", "(caixa)") else base_desc

        txn_entry = {
            "companyId":              company_id,
            "entityId":              entity_id,
            "walletId":              wallet_id,
            "currencyId":            currency_id,
            "operationDate":         date,
            "liquidationDate":       date,
            "balance":               item.get("impact") or 0,
            "description":           description,
            "inputType":             "sheets",
            "beehusTransactionType": txn_type,
            "hide":                  True,
            "comment":               "",
        }
        if item.get("securityId"):
            txn_entry["securityId"] = item["securityId"]
        transactions.append(txn_entry)

    return jsonify({
        "companyId":    company_id,
        "transactions": transactions,
    })


_PROVISION_FLAGS = {"MISSING_PROVISION", "WRONG_PROVISION_AMOUNT"}


@bp.route("/api/conciliacao/generate-provisions", methods=["POST"])
def generate_provisions():
    """Build provision rows (for clipboard) from accepted diagnostic items."""
    data      = request.get_json() or {}
    wallet_id = data.get("walletId", "")
    date      = data.get("date", "")
    items     = data.get("items", [])

    if not wallet_id or not date or not items:
        return jsonify({"error": "walletId, date e items são obrigatórios"}), 400

    # Fetch wallet info
    wallet = db.wallets.find_one({"_id": wallet_id}, {"currencyId": 1})
    if not wallet:
        try:
            wallet = db.wallets.find_one({"_id": _OID(wallet_id)}, {"currencyId": 1})
        except Exception:
            pass
    currency_id = str((wallet or {}).get("currencyId", "BRL"))

    provisions = []
    for item in items:
        flag = item.get("flag", "")
        if flag not in _PROVISION_FLAGS:
            continue

        prov_data = item.get("provisionData") or {}
        sec_name  = item.get("securityName", "")
        sec_id    = item.get("securityId", "")

        desc = f"Provisão gerada por conciliação — {sec_name}" if sec_name else "Provisão gerada por conciliação"

        provisions.append({
            "walletId":        wallet_id,
            "initialDate":     prov_data.get("initialDate", date),
            "liquidationDate": prov_data.get("liquidationDate", date),
            "provisionType":   prov_data.get("provisionType", "buySell"),
            "securityId":      sec_id,
            "balance":         prov_data.get("balance") or item.get("impact") or 0,
            "description":     desc,
            "provisionSource": "adjustments",
            "currencyId":      currency_id,
        })

    return jsonify({"provisions": provisions})


@bp.route("/api/conciliacao/replicate-scenario", methods=["POST"])
def replicate_scenario():
    """Clone wallet + position + transactions from source to target wallet+date."""
    data = request.get_json() or {}
    source_wallet  = data.get("sourceWalletId", "")
    source_date    = data.get("sourceDate", "")
    target_company = data.get("targetCompanyId", "")
    target_wallet  = data.get("targetWalletId", "")
    target_date    = data.get("targetDate", "")

    if not all([source_wallet, source_date, target_company, target_wallet, target_date]):
        return jsonify({"error": "Todos os campos são obrigatórios"}), 400

    # ── 1. Wallet ──────────────────────────────────────────────────────────────
    wallet_doc = db.wallets.find_one({"_id": source_wallet})
    if not wallet_doc:
        try:
            wallet_doc = db.wallets.find_one({"_id": _OID(source_wallet)})
        except Exception:
            pass
    if not wallet_doc:
        return jsonify({"error": "Wallet de origem não encontrada"}), 404

    wallet_json = [{
        "name":                    wallet_doc.get("name", ""),
        "hasDailyPosition":        wallet_doc.get("hasDailyPosition", True),
        "companyId":               target_company,
        "currency":                str(wallet_doc.get("currencyId", "BRL")),
        "startDateConsolidation":  target_date,
        "startDateReturn":         target_date,
        "entityId":                str(wallet_doc.get("entityId", "")),
        "accountCode":             wallet_doc.get("accountCode", wallet_doc.get("name", "")),
        "consumptionIdentifiers":  wallet_doc.get("consumptionIdentifiers", []),
        "securitiesForExplosion":  wallet_doc.get("securitiesForExplosion", []),
    }]

    # ── 2. Positions (current + former) ──────────────────────────────────────────
    pos_doc = db.processedPosition.find_one(
        {"walletId": source_wallet, "positionDate": source_date},
        {"securities": 1}
    )
    former_doc = db.processedPosition.find_one(
        {"walletId": source_wallet, "positionDate": {"$lt": source_date}},
        {"securities": 1, "positionDate": 1},
        sort=[("positionDate", -1)]
    )

    # Compute target former date preserving the same day offset as source
    former_source_date = str(former_doc.get("positionDate", ""))[:10] if former_doc else None
    if former_source_date:
        try:
            src_delta = (_date.fromisoformat(source_date) - _date.fromisoformat(former_source_date)).days
            target_former_date = (_date.fromisoformat(target_date) - timedelta(days=src_delta)).isoformat()
        except Exception:
            target_former_date = former_source_date
    else:
        target_former_date = None

    default_currency = str(wallet_doc.get("currencyId", "BRL"))
    positions_json = {"companyId": target_company, "unprocessedSecurities": []}

    # Former position securities
    if former_doc and target_former_date:
        for sec in former_doc.get("securities", []):
            pu  = sec.get("pu")
            qty = sec.get("quantity")
            positions_json["unprocessedSecurities"].append({
                "date":         target_former_date,
                "walletId":     target_wallet,
                "security":     sec.get("beehusName", ""),
                "quantity":     qty,
                "pu":           pu,
                "balance":      round(pu * qty, 2) if (pu is not None and qty is not None) else None,
                "currencyId":   sec.get("currencyId") or default_currency,
                "cashAccount":  "Sim" if sec.get("cashAccount") else "Nao",
            })

    # Current position securities
    for sec in (pos_doc or {}).get("securities", []):
        pu  = sec.get("pu")
        qty = sec.get("quantity")
        positions_json["unprocessedSecurities"].append({
            "date":         target_date,
            "walletId":     target_wallet,
            "security":     sec.get("beehusName", ""),
            "quantity":     qty,
            "pu":           pu,
            "balance":      round(pu * qty, 2) if (pu is not None and qty is not None) else None,
            "currencyId":   sec.get("currencyId") or default_currency,
            "cashAccount":  "Sim" if sec.get("cashAccount") else "Nao",
        })

    # ── 3. Transactions ────────────────────────────────────────────────────────
    entity_id   = str(wallet_doc.get("entityId", ""))
    currency_id = str(wallet_doc.get("currencyId", "BRL"))

    txns_json = {"companyId": target_company, "transactions": []}
    for txn in db.transactions.find(
        {"walletId": source_wallet, "liquidationDate": source_date},
        {"securityId": 1, "beehusTransactionType": 1, "balance": 1,
         "description": 1, "quantity": 1, "price": 1}
    ):
        txn_entry = {
            "companyId":              target_company,
            "entityId":              entity_id,
            "walletId":              target_wallet,
            "currencyId":            currency_id,
            "operationDate":         target_date,
            "liquidationDate":       target_date,
            "balance":               txn.get("balance"),
            "description":           txn.get("description", ""),
            "inputType":             "sheets",
            "beehusTransactionType": txn.get("beehusTransactionType"),
            "hide":                  False,
            "comment":               "",
        }
        sid = str(txn.get("securityId", "") or "")
        if sid:
            txn_entry["securityId"] = sid
        txns_json["transactions"].append(txn_entry)

    return jsonify({
        "wallet":       wallet_json,
        "positions":    positions_json,
        "transactions": txns_json,
    })
