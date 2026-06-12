from flask import Blueprint, render_template, jsonify, request
from db import db, get_biz_dates, get_company_filter, valid_wallet_ids
from bson import ObjectId as _OID
import json, os, statistics

bp = Blueprint("validacao_rentabilidades", __name__)

_NUM_DATES = 10
THRESHOLDS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "rentability_thresholds.json")


def _load_thresholds():
    if not os.path.exists(THRESHOLDS_FILE):
        return {}
    with open(THRESHOLDS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_thresholds(data):
    with open(THRESHOLDS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


_valid_wallet_ids = valid_wallet_ids


# ── Routes ─────────────────────────────────────────────────────────────────────

@bp.route("/validacao-rentabilidades")
def index():
    companies = sorted(
        [{"id": str(c["_id"]), "name": c.get("name", str(c["_id"]))}
         for c in db.companies.find({}, {"name": 1})],
        key=lambda c: c["name"],
    )
    cf = get_company_filter()
    if cf:
        companies = [c for c in companies if c["id"] in cf]
    return render_template("validacao_rentabilidades.html", companies=companies)


@bp.route("/api/validacao-rentabilidades/dates")
def get_dates():
    company_id = request.args.get("companyId", "")
    end_date   = request.args.get("endDate") or None
    wallet_ids = _valid_wallet_ids()

    # Auto-detect most recent date from navPackages
    if not end_date:
        latest = db.navPackages.find_one(
            {"companyId": company_id, "walletId": {"$in": list(wallet_ids)}, "trashed": {"$ne": True}},
            {"positionDate": 1},
            sort=[("positionDate", -1)]
        )
        if latest and latest.get("positionDate"):
            end_date = str(latest["positionDate"])[:10]

    dates = get_biz_dates(_NUM_DATES, end_date)

    # Count wallets with processedPosition per date
    totals = {}
    for doc in db.processedPosition.aggregate([
        {"$match": {"walletId": {"$in": list(wallet_ids)}, "positionDate": {"$in": dates}}},
        {"$group": {"_id": "$positionDate", "n": {"$sum": 1}}},
    ]):
        d = str(doc["_id"])[:10]
        if d:
            totals[d] = doc["n"]

    cards = [{"date": d, "total": totals.get(d, 0)} for d in dates]
    return jsonify({"cards": cards})


@bp.route("/api/validacao-rentabilidades/securities")
def get_securities():
    """Return rentability data for all securities across all wallets for a company+date."""
    company_id = request.args.get("companyId", "")
    date       = request.args.get("date", "")
    wallet_ids = _valid_wallet_ids()

    # Get all navPackages for this company+date to know which wallets have data
    nav_wallets = set()
    for doc in db.navPackages.find(
        {"companyId": company_id, "walletId": {"$in": list(wallet_ids)},
         "positionDate": date, "trashed": {"$ne": True}},
        {"walletId": 1}
    ):
        nav_wallets.add(str(doc["walletId"]))

    if not nav_wallets:
        return jsonify({"securities": [], "thresholdsAvailable": False})

    # Load thresholds
    thresholds = _load_thresholds()

    # Fetch all processedPositions for these wallets on this date
    results = []
    for pos_doc in db.processedPosition.find(
        {"walletId": {"$in": list(nav_wallets)}, "positionDate": date},
        {"walletId": 1, "securities": 1}
    ):
        wallet_id = str(pos_doc.get("walletId", ""))

        # Get former position
        former_doc = db.processedPosition.find_one(
            {"walletId": wallet_id, "positionDate": {"$lt": date}},
            {"securities": 1, "positionDate": 1},
            sort=[("positionDate", -1)]
        )
        former_date = str(former_doc.get("positionDate", ""))[:10] if former_doc else None
        former_map = {}
        for s in (former_doc or {}).get("securities", []):
            sid = str(s.get("securityId", ""))
            former_map[sid] = {"pu": s.get("pu"), "quantity": s.get("quantity")}

        for sec in pos_doc.get("securities", []):
            sid       = str(sec.get("securityId", ""))
            pu        = sec.get("pu")
            qty       = sec.get("quantity")
            event_c   = sec.get("eventContribution") or 0
            total_c   = sec.get("totalContribution")

            f      = former_map.get(sid, {})
            f_pu   = f.get("pu")
            f_qty  = f.get("quantity")
            f_bal  = round(f_pu * f_qty, 6) if (f_pu is not None and f_qty is not None) else None

            # Rentability PU
            try:
                event_per_unit = (event_c / qty) if (qty and qty != 0) else 0
                ret_pu = round((pu + event_per_unit) / f_pu - 1, 8) if (pu and f_pu) else None
            except (TypeError, ZeroDivisionError):
                ret_pu = None

            # Rentability Contribution
            try:
                ret_c = round(total_c / f_bal, 8) if (total_c is not None and f_bal) else None
            except (TypeError, ZeroDivisionError):
                ret_c = None

            # Threshold
            sec_threshold = thresholds.get(sid)
            is_anomaly = False
            if sec_threshold and ret_pu is not None:
                lb = sec_threshold.get("lowerBound")
                ub = sec_threshold.get("upperBound")
                if lb is not None and ub is not None:
                    is_anomaly = ret_pu < lb or ret_pu > ub

            results.append({
                "walletId":           wallet_id,
                "securityId":         sid,
                "mainId":             sec.get("mainId", ""),
                "beehusName":         sec.get("beehusName", ""),
                "pricingType":        sec.get("pricingType", ""),
                "formerDate":         former_date,
                "eventContribution":  event_c,
                "rentabPU":           ret_pu,
                "rentabContribution": ret_c,
                "threshold":          sec_threshold,
                "isAnomaly":          is_anomaly,
            })

    # Sort: anomalies first, then by name
    results.sort(key=lambda r: (not r["isAnomaly"], r["beehusName"]))

    return jsonify({
        "securities":          results,
        "thresholdsAvailable": bool(thresholds),
    })


@bp.route("/api/validacao-rentabilidades/security-detail")
def security_detail():
    """Return detailed position info for a security on current and former dates."""
    wallet_id   = request.args.get("walletId", "")
    security_id = request.args.get("securityId", "")
    date        = request.args.get("date", "")

    # Current position
    pos_doc = db.processedPosition.find_one(
        {"walletId": wallet_id, "positionDate": date},
        {"securities": 1}
    )
    current_sec = None
    for s in (pos_doc or {}).get("securities", []):
        if str(s.get("securityId", "")) == security_id:
            current_sec = s
            break

    # Former position
    former_doc = db.processedPosition.find_one(
        {"walletId": wallet_id, "positionDate": {"$lt": date}},
        {"securities": 1, "positionDate": 1},
        sort=[("positionDate", -1)]
    )
    former_date = str(former_doc.get("positionDate", ""))[:10] if former_doc else None
    former_sec  = None
    for s in (former_doc or {}).get("securities", []):
        if str(s.get("securityId", "")) == security_id:
            former_sec = s
            break

    # Last 10 PUs: look at the last 11 processedPositions (need n+1 to compute rentab for n)
    raw_history = []
    for hist_doc in db.processedPosition.find(
        {"walletId": wallet_id, "positionDate": {"$lte": date}},
        {"securities": 1, "positionDate": 1}
    ).sort("positionDate", -1).limit(11):
        hist_date = str(hist_doc.get("positionDate", ""))[:10]
        for s in hist_doc.get("securities", []):
            if str(s.get("securityId", "")) == security_id:
                raw_history.append({
                    "date":              hist_date,
                    "pu":                s.get("pu"),
                    "quantity":          s.get("quantity"),
                    "eventContribution": s.get("eventContribution") or 0,
                    "totalContribution": s.get("totalContribution"),
                })
                break

    # Compute rentabilities (need former row for each)
    pu_history = []
    for i in range(len(raw_history) - 1):
        cur = raw_history[i]
        fmr = raw_history[i + 1]
        pu      = cur["pu"]
        f_pu    = fmr["pu"]
        qty     = cur["quantity"]
        f_qty   = fmr["quantity"]
        event_c = cur["eventContribution"]
        total_c = cur["totalContribution"]
        f_bal   = round(f_pu * f_qty, 6) if (f_pu is not None and f_qty is not None) else None

        try:
            event_per_unit = (event_c / qty) if (qty and qty != 0) else 0
            ret_pu = round((pu + event_per_unit) / f_pu - 1, 8) if (pu and f_pu) else None
        except (TypeError, ZeroDivisionError):
            ret_pu = None
        try:
            ret_c = round(total_c / f_bal, 8) if (total_c is not None and f_bal) else None
        except (TypeError, ZeroDivisionError):
            ret_c = None

        pu_history.append({
            "date":              cur["date"],
            "pu":                pu,
            "quantity":          qty,
            "rentabPU":          ret_pu,
            "rentabContribution": ret_c,
        })

    def _sec_to_dict(sec, label):
        if not sec:
            return None
        return {
            "label":              label,
            "pu":                 sec.get("pu"),
            "quantity":           sec.get("quantity"),
            "executionPrice":     sec.get("executionPrice"),
            "totalContribution":  sec.get("totalContribution"),
            "dailyContribution":  sec.get("dailyContribution"),
            "intradayContribution": sec.get("intradayContribution"),
            "eventContribution":  sec.get("eventContribution"),
            "pricingType":        sec.get("pricingType"),
            "mainId":             sec.get("mainId"),
        }

    return jsonify({
        "current":    _sec_to_dict(current_sec, date),
        "former":     _sec_to_dict(former_sec, former_date),
        "puHistory":  pu_history,
    })


@bp.route("/api/validacao-rentabilidades/calculate-thresholds", methods=["POST"])
def calculate_thresholds():
    """Calculate 3-sigma thresholds for all securities based on historical rentabilities."""
    data       = request.get_json() or {}
    company_id = data.get("companyId", "")
    num_days   = data.get("numDays", 60)
    wallet_ids = _valid_wallet_ids()

    # Get wallets for this company
    nav_wallets = set()
    for doc in db.navPackages.find(
        {"companyId": company_id, "walletId": {"$in": list(wallet_ids)}, "trashed": {"$ne": True}},
        {"walletId": 1}
    ):
        nav_wallets.add(str(doc["walletId"]))

    if not nav_wallets:
        return jsonify({"error": "Nenhuma carteira encontrada", "count": 0}), 404

    # Get the last num_days positions for each wallet, collect rentabilities per securityId
    rentab_by_sec = {}  # {securityId: [rentabPU values]}

    for wallet_id in nav_wallets:
        positions = list(db.processedPosition.find(
            {"walletId": wallet_id},
            {"securities": 1, "positionDate": 1}
        ).sort("positionDate", -1).limit(num_days + 1))

        # Build a timeline of {securityId: {date: pu}} to compute returns
        for i in range(len(positions) - 1):
            current = positions[i]
            former  = positions[i + 1]

            former_map = {
                str(s.get("securityId", "")): {"pu": s.get("pu"), "quantity": s.get("quantity")}
                for s in former.get("securities", [])
            }

            for sec in current.get("securities", []):
                sid     = str(sec.get("securityId", ""))
                pu      = sec.get("pu")
                qty     = sec.get("quantity")
                event_c = sec.get("eventContribution") or 0

                f    = former_map.get(sid, {})
                f_pu = f.get("pu")

                if not pu or not f_pu:
                    continue

                try:
                    event_per_unit = (event_c / qty) if (qty and qty != 0) else 0
                    ret_pu = (pu + event_per_unit) / f_pu - 1
                except (TypeError, ZeroDivisionError):
                    continue

                rentab_by_sec.setdefault(sid, []).append(ret_pu)

    # Calculate thresholds
    thresholds = {}
    for sid, returns in rentab_by_sec.items():
        if len(returns) < 3:
            continue
        mean   = statistics.mean(returns)
        stddev = statistics.stdev(returns)
        thresholds[sid] = {
            "mean":       round(mean, 10),
            "stdDev":     round(stddev, 10),
            "lowerBound": round(mean - 3 * stddev, 10),
            "upperBound": round(mean + 3 * stddev, 10),
            "sampleSize": len(returns),
        }

    _save_thresholds(thresholds)

    return jsonify({"ok": True, "count": len(thresholds)})
