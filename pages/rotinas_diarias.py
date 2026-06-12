import json
import os
import re
import uuid
import calendar
from datetime import date, datetime, timedelta
from collections import defaultdict

from bson import ObjectId
from flask import Blueprint, jsonify, render_template, request

import db as db_module

bp = Blueprint("rotinas_diarias", __name__)

TEMPLATES_FILE      = os.path.join(os.path.dirname(__file__), "..", "data", "wallet_templates.json")
STATUS_FILE         = os.path.join(os.path.dirname(__file__), "..", "data", "rotinas_status.json")
ROW_CONFIG_FILE     = os.path.join(os.path.dirname(__file__), "..", "data", "rotinas_row_config.json")
CUSTOM_ROWS_FILE    = os.path.join(os.path.dirname(__file__), "..", "data", "rotinas_custom_rows.json")
WEEKLY_ROWS_FILE    = os.path.join(os.path.dirname(__file__), "..", "data", "rotinas_weekly_rows.json")
MONTHLY_ROWS_FILE   = os.path.join(os.path.dirname(__file__), "..", "data", "rotinas_monthly_rows.json")
WEEKLY_STATUS_FILE  = os.path.join(os.path.dirname(__file__), "..", "data", "rotinas_weekly_status.json")
MONTHLY_STATUS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "rotinas_monthly_status.json")

MONTH_NAMES = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]

_PROC_RESP = {
    "Oikos": "Victor", "Mira": "Victor",
    "Blue3": "Yuri",   "SMig": "Victor",
}

MANUAL_ROWS = [
    ("proc_pos_blue3",  "Processamento Posições",    "Blue3", "", "Yuri"),
    ("proc_pos_smig",   "Processamento Posições",    "SMig",  "", "Victor"),
    ("proc_tx_mira",    "Processamento Transações",  "Mira",  "", "Victor"),
    ("proc_tx_blue3",   "Processamento Transações",  "Blue3", "", "Yuri"),
    ("proc_tx_oikos",   "Processamento Transações",  "Oikos", "", "Victor"),
    ("proc_tx_smig",    "Processamento Transações",  "SMig",  "", "Victor"),
    ("check_mira",      "Check Rent",                "Mira",  "", "Victor"),
    ("check_blue3",     "Check Rent",                "Blue3", "", "Yuri"),
    ("check_oikos",     "Check Rent",                "Oikos", "", "Yuri/Theo"),
    ("check_smig",      "Check Rent",                "SMig",  "", "Victor"),
]

AUTOMATIZACAO_OPTS = ["", "Automatizado", "Semi Automatizado", "Manual"]


# ── Storage helpers ────────────────────────────────────────────────────────────

def _load_templates():
    if not os.path.exists(TEMPLATES_FILE):
        return []
    with open(TEMPLATES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_status():
    if not os.path.exists(STATUS_FILE):
        return {}
    with open(STATUS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_status(data):
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_row_config():
    if not os.path.exists(ROW_CONFIG_FILE):
        return {}
    with open(ROW_CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_row_config(data):
    with open(ROW_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_custom_rows():
    if not os.path.exists(CUSTOM_ROWS_FILE):
        return []
    with open(CUSTOM_ROWS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_custom_rows(data):
    with open(CUSTOM_ROWS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_weekly_rows():
    if not os.path.exists(WEEKLY_ROWS_FILE):
        return []
    with open(WEEKLY_ROWS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_weekly_rows(data):
    with open(WEEKLY_ROWS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_monthly_rows():
    if not os.path.exists(MONTHLY_ROWS_FILE):
        return []
    with open(MONTHLY_ROWS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_monthly_rows(data):
    with open(MONTHLY_ROWS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_weekly_status():
    if not os.path.exists(WEEKLY_STATUS_FILE):
        return {}
    with open(WEEKLY_STATUS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_weekly_status(data):
    with open(WEEKLY_STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_monthly_status():
    if not os.path.exists(MONTHLY_STATUS_FILE):
        return {}
    with open(MONTHLY_STATUS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_monthly_status(data):
    with open(MONTHLY_STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_settings():
    return db_module.load_settings()


def _biz_days(year, month):
    holidays = set(_load_settings().get("holidays", []))
    result = []
    for d in range(1, calendar.monthrange(year, month)[1] + 1):
        dt = date(year, month, d)
        if dt.weekday() < 5:
            ds = dt.strftime("%Y-%m-%d")
            if ds not in holidays:
                result.append(ds)
    return result


def _biz_weeks(year, month):
    """Week column dicts (keyed by ISO Monday date) for weeks with biz days in the month."""
    biz = _biz_days(year, month)
    seen: dict = {}
    for d_str in biz:
        dt     = date.fromisoformat(d_str)
        monday = dt - timedelta(days=dt.weekday())
        key    = monday.strftime("%Y-%m-%d")
        seen.setdefault(key, []).append(d_str)
    result = []
    for i, (key, days) in enumerate(sorted(seen.items()), 1):
        s, e = days[0], days[-1]
        result.append({
            "id":      key,
            "label":   f"S{i}",
            "start":   s,
            "end":     e,
            "tooltip": f"{s[8:]}/{s[5:7]} – {e[8:]}/{e[5:7]}",
        })
    return result


_MONTH_SHORT = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"]


def _year_months(year):
    return [
        {"id": f"{year}-{m:02d}", "label": _MONTH_SHORT[m - 1]}
        for m in range(1, 13)
    ]


_NAME_KEYWORDS = [
    (("ALTERNATIVES", "VTEX", "IMOBILIZADO", " DAF", "- DAF"), "Alternatives"),
    (("JEFFERIES",),                                             "Jefferies"),
    (("JPM",),                                                   "JPM"),
    (("GOLDMAN", " GS "),                                        "Goldman Sachs"),
    (("UBS",),                                                   "UBS"),
    (("ITAU", "ITAÚ", "KINEA"),                                  "Itaú"),
    (("DAYCOVAL",),                                              "Daycoval"),
    (("SAFRA",),                                                 "Safra"),
    (("BRADESCO",),                                              "Bradesco"),
    (("SANTANDER",),                                             "Santander"),
    (("BNY",),                                                   "BNY Mellon"),
    (("ORIGINAL",),                                              "Original"),
    (("ACESSO DIRETO",),                                         "Acesso Direto"),
    (("MORGAN STANLEY", "MS - US", "MS USD"),                    "Morgan Stanley"),
]

_CODE_PREFIXES = [
    ("INTRAG", "Itaú"),   # Intrag Distribuidora = custodiante Itaú
    ("INFRA",  "Itaú"),   # códigos de fundo infra do Itaú
]


def _btg_sub(upper_name: str) -> str:
    if "KY" in upper_name or "CAYMAN" in upper_name:
        return "BTG Cayman"
    if " US" in upper_name:
        return "BTG US"
    if "OFFSHORE" in upper_name:
        return "BTG Offshore"
    return "BTG BR"


def _institution(name: str, account_code: str = "", note: str = "") -> str:
    if note and note.strip():
        return note.strip()

    upper_name = name.upper()
    upper_code = account_code.upper()

    if "BTG" in upper_name:
        return _btg_sub(upper_name)

    if " MS " in upper_name:
        return "Morgan Stanley"

    for keywords, label in _NAME_KEYWORDS:
        if any(kw in upper_name for kw in keywords):
            return label

    for prefix, label in _CODE_PREFIXES:
        if upper_code.startswith(prefix):
            return label

    # Avenue: account codes follow 6XX##### pattern (e.g. 6FM09578, 6FO37420)
    if re.search(r'6[A-Z]{2}\d{4,}', upper_name) or re.match(r'^6[A-Z]{2}\d', upper_code):
        return "Avenue"

    # XP Offshore / XP International: QXR prefix
    if "QXR" in upper_code or "QXR" in upper_name:
        return "XP Offshore"

    # XP Brazil: pure numeric account codes (7-8 digits typical)
    if re.match(r'^\d{5,}$', upper_code):
        return "XP"

    return "Outros"


def _build_template_rows(templates, row_config):
    carga_rows        = []
    proc_pos_rows     = []
    proc_tx_rows      = []
    check_rent_rows   = []
    wallet_delay_map  = {}  # walletId -> delay (business days)

    for tmpl in templates:
        partner     = tmpl["name"].split(" - ")[0].strip()
        all_wallets = tmpl.get("wallets", [])
        all_wids    = [w["walletId"] for w in all_wallets]

        for w in all_wallets:
            wallet_delay_map[w["walletId"]] = int(w.get("delay", 0) or 0)

        groups: dict = defaultdict(list)
        for w in all_wallets:
            groups[_institution(w["name"], w.get("accountCode", ""), w.get("note", ""))].append(w)

        for inst, wallets in sorted(groups.items()):
            resps = [w.get("responsible", "") for w in wallets if w.get("responsible", "")]
            resp  = max(set(resps), key=resps.count) if resps else ""
            notes = list({w.get("note", "") for w in wallets if w.get("note", "")})
            pid   = (
                f"carga_{partner.lower().replace(' ','_')}"
                f"_{inst.lower().replace(' ','_').replace('/','_')}"
            )
            cfg = row_config.get(pid, {})
            carga_rows.append({
                "id":            pid,
                "categoria":     "Carga",
                "parceiro":      partner,
                "instituicao":   inst,
                "obs":           notes[0] if notes else "",
                "responsavel":   cfg.get("responsavel", resp),
                "automatizacao": cfg.get("automatizacao", ""),
                "type":          "carga",
                "wallet_ids":    [w["walletId"] for w in wallets],
                "children":      [
                    {
                        "id":    w["walletId"],
                        "name":  w["name"],
                        "note":  w.get("note", ""),
                        "delay": int(w.get("delay", 0) or 0),
                    }
                    for w in wallets
                ],
                "custom": False,
            })

        children_all = [
            {
                "id":    w["walletId"],
                "name":  w["name"],
                "note":  "",
                "delay": int(w.get("delay", 0) or 0),
            }
            for w in all_wallets
        ]

        pid = f"proc_pos_{partner.lower().replace(' ','_')}"
        cfg = row_config.get(pid, {})
        proc_pos_rows.append({
            "id":            pid,
            "categoria":     "Processamento Posições",
            "parceiro":      partner,
            "instituicao":   "",
            "obs":           "",
            "responsavel":   cfg.get("responsavel", _PROC_RESP.get(partner, "")),
            "automatizacao": cfg.get("automatizacao", ""),
            "type":          "proc_pos",
            "wallet_ids":    all_wids,
            "children":      children_all,
            "custom":        False,
        })

        pid_tx = f"proc_tx_{partner.lower().replace(' ','_')}"
        cfg_tx = row_config.get(pid_tx, {})
        proc_tx_rows.append({
            "id":            pid_tx,
            "categoria":     "Processamento Transações",
            "parceiro":      partner,
            "instituicao":   "",
            "obs":           "",
            "responsavel":   cfg_tx.get("responsavel", _PROC_RESP.get(partner, "")),
            "automatizacao": cfg_tx.get("automatizacao", ""),
            "type":          "proc_tx",
            "wallet_ids":    all_wids,
            "children":      children_all,
            "custom":        False,
        })

        pid_cr = f"check_rent_{partner.lower().replace(' ','_')}"
        cfg_cr = row_config.get(pid_cr, {})
        check_rent_rows.append({
            "id":            pid_cr,
            "categoria":     "Check Rent",
            "parceiro":      partner,
            "instituicao":   "",
            "obs":           "",
            "responsavel":   cfg_cr.get("responsavel", _PROC_RESP.get(partner, "")),
            "automatizacao": cfg_cr.get("automatizacao", ""),
            "type":          "check_rent",
            "wallet_ids":    all_wids,
            "children":      children_all,
            "custom":        False,
        })

    return carga_rows, proc_pos_rows, proc_tx_rows, check_rent_rows, wallet_delay_map


def _agg_wid_date(collection, wids, past_days):
    """Aggregate (walletId, date) pairs from collection. Returns defaultdict(set)."""
    result: dict = defaultdict(set)
    try:
        for doc in collection.aggregate([
            {"$match": {"walletId": {"$in": wids}, "positionDate": {"$in": past_days}}},
            {"$group": {"_id": {"w": "$walletId", "d": "$positionDate"}}},
        ]):
            dt = str(doc["_id"]["d"])[:10]
            if dt:
                result[str(doc["_id"]["w"])].add(dt)
    except Exception:
        pass
    if not result:
        try:
            oids = [ObjectId(x) for x in wids]
            for doc in collection.aggregate([
                {"$match": {"walletId": {"$in": oids}, "positionDate": {"$in": past_days}}},
                {"$group": {"_id": {"w": "$walletId", "d": "$positionDate"}}},
            ]):
                dt = str(doc["_id"]["d"])[:10]
                if dt:
                    result[str(doc["_id"]["w"])].add(dt)
        except Exception:
            pass
    return result


def _agg_rent_ok(wids, past_days, threshold):
    """Returns ws_rent_ok[wid] = set of dates where rent check passes."""
    result: dict = defaultdict(set)
    if not wids:
        return result

    def _run(wid_list):
        for doc in db_module.db.navPackages.aggregate([
            {"$match": {
                "walletId":    {"$in": wid_list},
                "positionDate": {"$in": past_days},
                "trashed":     {"$ne": True},
            }},
            {"$addFields": {"diff": {"$abs": {"$subtract": [
                {"$ifNull": ["$returnNavPerShare",   0]},
                {"$ifNull": ["$returnContribution",  0]},
            ]}}}},
            {"$group": {
                "_id":     {"w": "$walletId", "d": "$positionDate"},
                "maxDiff": {"$max": "$diff"},
            }},
        ]):
            dt = str(doc["_id"]["d"])[:10]
            if dt and doc.get("maxDiff") is not None and doc["maxDiff"] < threshold:
                result[str(doc["_id"]["w"])].add(dt)

    try:
        _run(wids)
    except Exception:
        pass
    if not result:
        try:
            _run([ObjectId(x) for x in wids])
        except Exception:
            pass
    return result


def _mongo_status(mongo_rows, biz_days, year, month,
                  wallet_delay_map=None, elapsed=None, rent_threshold=0.01):
    """
    carga      → unprocessedSecurityPositions (delay-aware)
    proc_pos   → processedPosition            (delay-aware)
    proc_tx    → transactions beehusTransactionType check
    check_rent → navPackages returnNavPerShare vs returnContribution
    """
    if not db_module.db._ready() or not mongo_rows:
        return {}, {}

    today     = date.today().strftime("%Y-%m-%d")
    past_days = [d for d in biz_days if d <= today]
    if not past_days:
        return {r["id"]: {d: "future" for d in biz_days} for r in mongo_rows}, {}

    wdm = wallet_delay_map or {}
    elp = elapsed or {}

    pos_wids  = list({wid for r in mongo_rows if r["type"] in ("carga", "proc_pos")
                      for wid in r["wallet_ids"]})
    tx_wids   = list({wid for r in mongo_rows if r["type"] == "proc_tx"
                      for wid in r["wallet_ids"]})
    rent_wids = list({wid for r in mongo_rows if r["type"] == "check_rent"
                      for wid in r["wallet_ids"]})

    ws_unproc = _agg_wid_date(db_module.db.unprocessedSecurityPositions, pos_wids, past_days)
    ws_proc   = _agg_wid_date(db_module.db.processedPosition,            pos_wids, past_days)

    ws_tx_empty: dict = defaultdict(set)
    if tx_wids:
        try:
            for doc in db_module.db.transactions.aggregate([
                {"$match": {
                    "walletId": {"$in": tx_wids},
                    "liquidationDate": {"$in": past_days},
                    "$or": [
                        {"beehusTransactionType": None},
                        {"beehusTransactionType": ""},
                        {"beehusTransactionType": {"$exists": False}},
                    ],
                }},
                {"$group": {"_id": {"w": "$walletId", "d": "$liquidationDate"}}},
            ]):
                dt = str(doc["_id"]["d"])[:10]
                if dt:
                    ws_tx_empty[str(doc["_id"]["w"])].add(dt)
        except Exception:
            pass

    rent_ok = _agg_rent_ok(rent_wids, past_days, rent_threshold / 100.0)

    row_status    = {}
    wallet_status = {}

    for row in mongo_rows:
        rd    = {}
        rtype = row["type"]
        wids  = row["wallet_ids"]

        if rtype == "proc_tx":
            for d in biz_days:
                if d > today:
                    rd[d] = "future"
                elif any(d in ws_tx_empty[wid] for wid in wids):
                    rd[d] = "partial"
                else:
                    rd[d] = "done"
            row_status[row["id"]] = rd
            rw = {}
            for wid in wids:
                wd = {}
                for d in biz_days:
                    if d > today:          wd[d] = "future"
                    elif d in ws_tx_empty[wid]: wd[d] = "partial"
                    else:                  wd[d] = "done"
                rw[wid] = wd
            wallet_status[row["id"]] = rw

        elif rtype == "check_rent":
            for d in biz_days:
                if d > today:
                    rd[d] = "future"
                    continue
                if not wids:
                    rd[d] = "missing"
                    continue
                exp_wids = [wid for wid in wids if elp.get(d, 999) >= wdm.get(wid, 0)]
                if not exp_wids:
                    rd[d] = "not_expected"
                    continue
                hit = sum(1 for wid in exp_wids if d in rent_ok[wid])
                tot = len(exp_wids)
                if hit == tot:  rd[d] = "done"
                elif hit > 0:   rd[d] = "partial"
                else:           rd[d] = "missing"
            row_status[row["id"]] = rd
            rw = {}
            for wid in wids:
                wd = {}
                for d in biz_days:
                    if d > today:         wd[d] = "future"
                    elif d in rent_ok[wid]: wd[d] = "done"
                    else:                 wd[d] = "missing"
                rw[wid] = wd
            wallet_status[row["id"]] = rw

        else:
            src = ws_proc if rtype == "proc_pos" else ws_unproc
            for d in biz_days:
                if d > today:
                    rd[d] = "future"
                    continue
                if not wids:
                    rd[d] = "missing"
                    continue
                exp_wids = [wid for wid in wids if elp.get(d, 999) >= wdm.get(wid, 0)]
                if not exp_wids:
                    rd[d] = "not_expected"
                    continue
                hit = sum(1 for wid in exp_wids if d in src[wid])
                tot = len(exp_wids)
                if hit == tot:  rd[d] = "done"
                elif hit > 0:   rd[d] = "partial"
                else:           rd[d] = "missing"
            row_status[row["id"]] = rd
            rw = {}
            for wid in wids:
                wd = {}
                for d in biz_days:
                    if d > today:      wd[d] = "future"
                    elif d in src[wid]: wd[d] = "done"
                    else:              wd[d] = "missing"
                rw[wid] = wd
            wallet_status[row["id"]] = rw

    return row_status, wallet_status


def _prev_month(y, m): return (y - 1, 12) if m == 1 else (y, m - 1)
def _next_month(y, m): return (y + 1, 1)  if m == 12 else (y, m + 1)


# ── Routes ─────────────────────────────────────────────────────────────────────

@bp.route("/rotinas-diarias")
def index():
    today = date.today()
    year  = int(request.args.get("year",  today.year))
    month = int(request.args.get("month", today.month))
    days  = _biz_days(year, month)

    templates  = _load_templates()
    row_config = _load_row_config()

    carga_rows, proc_pos_rows, proc_tx_rows, check_rent_rows, wallet_delay_map = \
        _build_template_rows(templates, row_config)
    template_proc_pos_ids   = {r["id"] for r in proc_pos_rows}
    template_proc_tx_ids    = {r["id"] for r in proc_tx_rows}
    template_check_rent_ids = {r["id"] for r in check_rent_rows}

    elapsed = {day: db_module.biz_days_elapsed(day) for day in days}

    manual_rows = []
    for r in MANUAL_ROWS:
        if r[1] == "Processamento Posições" and f"proc_pos_{r[2].lower()}" in template_proc_pos_ids:
            continue
        if r[1] == "Processamento Transações" and f"proc_tx_{r[2].lower()}" in template_proc_tx_ids:
            continue
        if r[1] == "Check Rent" and f"check_rent_{r[2].lower()}" in template_check_rent_ids:
            continue
        cfg = row_config.get(r[0], {})
        manual_rows.append({
            "id":            r[0],
            "categoria":     r[1],
            "parceiro":      r[2],
            "instituicao":   "",
            "obs":           r[3],
            "responsavel":   cfg.get("responsavel", r[4]),
            "automatizacao": cfg.get("automatizacao", ""),
            "type":          "manual",
            "wallet_ids":    [],
            "children":      [],
            "custom":        False,
        })

    custom_rows = []
    for cr in _load_custom_rows():
        cfg = row_config.get(cr["id"], {})
        custom_rows.append({
            "id":            cr["id"],
            "categoria":     cr.get("categoria", ""),
            "parceiro":      cr.get("parceiro", ""),
            "instituicao":   cr.get("instituicao", ""),
            "obs":           cr.get("obs", ""),
            "responsavel":   cfg.get("responsavel", cr.get("responsavel", "")),
            "automatizacao": cfg.get("automatizacao", cr.get("automatizacao", "")),
            "type":          "manual",
            "wallet_ids":    [],
            "children":      [],
            "custom":        True,
        })

    settings   = _load_settings()
    rent_thr   = settings.get("rent_threshold", 0.01)
    mongo_rows = carga_rows + proc_pos_rows + proc_tx_rows + check_rent_rows
    all_rows   = mongo_rows + manual_rows + custom_rows

    row_st, wallet_st = _mongo_status(
        mongo_rows, days, year, month, wallet_delay_map, elapsed, rent_thr,
    )
    manual_st = _load_status()

    comments_map = {}
    for key, val in manual_st.items():
        if key.startswith("comment_"):
            rest   = key[len("comment_"):]
            d_part = rest[-10:]
            rid    = rest[:-11]
            comments_map.setdefault(rid, {})[d_part] = val

    auto_types = {"carga", "proc_pos", "proc_tx", "check_rent"}
    status = {}
    for row in all_rows:
        if row["type"] in auto_types:
            status[row["id"]] = row_st.get(row["id"], {})
        else:
            status[row["id"]] = {day: manual_st.get(f"{row['id']}_{day}", "") for day in days}

    # ── Weekly / Monthly ──────────────────────────────────────────────────
    weeks        = _biz_weeks(year, month)
    year_mons    = _year_months(year)
    weekly_rows  = _load_weekly_rows()
    monthly_rows = _load_monthly_rows()
    weekly_st_raw  = _load_weekly_status()
    monthly_st_raw = _load_monthly_status()

    status_w = {
        row["id"]: {
            week["id"]: weekly_st_raw.get(f"{row['id']}_{week['id']}", "")
            for week in weeks
        }
        for row in weekly_rows
    }
    comments_w = {}
    for key, val in weekly_st_raw.items():
        if key.startswith("comment_"):
            rest   = key[len("comment_"):]
            col_id = rest[-10:]
            rid    = rest[:-11]
            comments_w.setdefault(rid, {})[col_id] = val

    status_m = {
        row["id"]: {
            mon["id"]: monthly_st_raw.get(f"{row['id']}_{mon['id']}", "")
            for mon in year_mons
        }
        for row in monthly_rows
    }
    comments_m = {}
    for key, val in monthly_st_raw.items():
        if key.startswith("comment_"):
            rest   = key[len("comment_"):]
            col_id = rest[-7:]   # YYYY-MM = 7 chars
            rid    = rest[:-8]
            comments_m.setdefault(rid, {})[col_id] = val

    return render_template(
        "rotinas_diarias.html",
        active="rotinas_diarias",
        rows=all_rows,
        biz_days=days,
        status=status,
        wallet_status=wallet_st,
        comments_map=comments_map,
        year=year,
        month=month,
        month_name=MONTH_NAMES[month],
        today=today.strftime("%Y-%m-%d"),
        prev_m=_prev_month(year, month),
        next_m=_next_month(year, month),
        automatizacao_opts=AUTOMATIZACAO_OPTS,
        rent_threshold=settings.get("rent_threshold", 0.01),
        biz_weeks=weeks,
        year_months=year_mons,
        weekly_rows=weekly_rows,
        monthly_rows=monthly_rows,
        status_w=status_w,
        status_m=status_m,
        comments_w=comments_w,
        comments_m=comments_m,
    )


@bp.route("/api/rotinas/status/<row_id>/<date_str>", methods=["POST"])
def set_status(row_id, date_str):
    data   = request.json or {}
    new_st = data.get("status", "done")
    manual = _load_status()
    key    = f"{row_id}_{date_str}"
    if not new_st or manual.get(key) == new_st:
        manual.pop(key, None)
    else:
        manual[key] = new_st
    _save_status(manual)
    return jsonify({"ok": True, "status": manual.get(key, "")})


@bp.route("/api/rotinas/row-config/<row_id>", methods=["POST"])
def set_row_config(row_id):
    data   = request.json or {}
    cfg    = _load_row_config()
    entry  = cfg.get(row_id, {})
    if "responsavel"   in data: entry["responsavel"]   = data["responsavel"]
    if "automatizacao" in data: entry["automatizacao"] = data["automatizacao"]
    cfg[row_id] = entry
    _save_row_config(cfg)
    return jsonify({"ok": True})


@bp.route("/api/rotinas/comments/<row_id>/<date_str>", methods=["GET"])
def get_comments(row_id, date_str):
    manual = _load_status()
    key    = f"comment_{row_id}_{date_str}"
    return jsonify(manual.get(key, []))


@bp.route("/api/rotinas/comments/<row_id>/<date_str>", methods=["POST"])
def add_comment(row_id, date_str):
    data   = request.json or {}
    text   = data.get("text", "").strip()
    if not text:
        return jsonify({"ok": False, "error": "empty"}), 400
    manual = _load_status()
    key    = f"comment_{row_id}_{date_str}"
    comments = manual.get(key, [])
    comments.append({"text": text, "created_at": datetime.utcnow().isoformat()})
    manual[key] = comments
    _save_status(manual)
    return jsonify({"ok": True, "comments": comments})


@bp.route("/api/rotinas/comments/<row_id>/<date_str>/<int:idx>", methods=["DELETE"])
def delete_comment(row_id, date_str, idx):
    manual = _load_status()
    key    = f"comment_{row_id}_{date_str}"
    comments = manual.get(key, [])
    if 0 <= idx < len(comments):
        comments.pop(idx)
    if comments:
        manual[key] = comments
    else:
        manual.pop(key, None)
    _save_status(manual)
    return jsonify({"ok": True})


@bp.route("/api/rotinas/rows", methods=["POST"])
def add_custom_row():
    data = request.json or {}
    rows = _load_custom_rows()
    row  = {
        "id":            f"custom_{uuid.uuid4().hex[:8]}",
        "categoria":     data.get("categoria", ""),
        "parceiro":      data.get("parceiro", ""),
        "instituicao":   data.get("instituicao", ""),
        "obs":           data.get("obs", ""),
        "responsavel":   data.get("responsavel", ""),
        "automatizacao": data.get("automatizacao", ""),
    }
    rows.append(row)
    _save_custom_rows(rows)
    return jsonify({"ok": True, "id": row["id"]})


@bp.route("/api/rotinas/rows/<row_id>", methods=["DELETE"])
def delete_custom_row(row_id):
    rows = _load_custom_rows()
    rows = [r for r in rows if r["id"] != row_id]
    _save_custom_rows(rows)
    return jsonify({"ok": True})


@bp.route("/api/rotinas/debug/<row_id>")
def debug_row(row_id):
    """Diagnostic: check what MongoDB actually returns for a row's wallets."""
    templates  = _load_templates()
    row_config = _load_row_config()
    carga_rows, proc_pos_rows, proc_tx_rows, _, _ = \
        _build_template_rows(templates, row_config)
    all_rows = carga_rows + proc_pos_rows + proc_tx_rows

    row = next((r for r in all_rows if r["id"] == row_id), None)
    if not row:
        return jsonify({"error": f"row {row_id!r} not found"})

    wids = row["wallet_ids"][:5]  # limit to first 5 wallets for brevity
    today = date.today()
    # last 5 business days
    sample_days = _biz_days(today.year, today.month)
    sample_days = [d for d in sample_days if d <= today.strftime("%Y-%m-%d")][-5:]

    result = {"row_id": row_id, "type": row["type"], "wallet_ids": wids, "sample_days": sample_days}

    if not db_module.db._ready():
        result["error"] = "DB not connected"
        return jsonify(result)

    coll = db_module.db.unprocessedSecurityPositions if row["type"] == "carga" else db_module.db.processedPosition

    # Approach A: string walletIds + string dates
    try:
        docs_a = list(coll.aggregate([
            {"$match": {"walletId": {"$in": wids}, "positionDate": {"$in": sample_days}}},
            {"$group": {"_id": {"w": "$walletId", "d": "$positionDate"}}},
        ]))
        result["A_string_wid_string_date"] = [{"w": str(d["_id"]["w"]), "d": str(d["_id"]["d"])[:10]} for d in docs_a]
    except Exception as e:
        result["A_error"] = str(e)

    # Approach B: ObjectId walletIds + string dates
    try:
        oids = [ObjectId(x) for x in wids]
        docs_b = list(coll.aggregate([
            {"$match": {"walletId": {"$in": oids}, "positionDate": {"$in": sample_days}}},
            {"$group": {"_id": {"w": "$walletId", "d": "$positionDate"}}},
        ]))
        result["B_oid_wid_string_date"] = [{"w": str(d["_id"]["w"]), "d": str(d["_id"]["d"])[:10]} for d in docs_b]
    except Exception as e:
        result["B_error"] = str(e)

    # Approach C: string walletIds + datetime dates
    try:
        dt_days = [datetime.strptime(d, "%Y-%m-%d") for d in sample_days]
        docs_c = list(coll.aggregate([
            {"$match": {"walletId": {"$in": wids}, "positionDate": {"$in": dt_days}}},
            {"$group": {"_id": {"w": "$walletId", "d": "$positionDate"}}},
        ]))
        result["C_string_wid_datetime_date"] = [{"w": str(d["_id"]["w"]), "d": str(d["_id"]["d"])[:10]} for d in docs_c]
    except Exception as e:
        result["C_error"] = str(e)

    # Approach D: ObjectId walletIds + datetime dates
    try:
        oids2    = [ObjectId(x) for x in wids]
        dt_days2 = [datetime.strptime(d, "%Y-%m-%d") for d in sample_days]
        docs_d = list(coll.aggregate([
            {"$match": {"walletId": {"$in": oids2}, "positionDate": {"$in": dt_days2}}},
            {"$group": {"_id": {"w": "$walletId", "d": "$positionDate"}}},
        ]))
        result["D_oid_wid_datetime_date"] = [{"w": str(d["_id"]["w"]), "d": str(d["_id"]["d"])[:10]} for d in docs_d]
    except Exception as e:
        result["D_error"] = str(e)

    # Approach E: no walletId filter at all (just check if dates match)
    try:
        docs_e = list(coll.aggregate([
            {"$match": {"positionDate": {"$in": sample_days}}},
            {"$limit": 3},
            {"$project": {"walletId": 1, "positionDate": 1}},
        ]))
        result["E_sample_any_wallet_string_date"] = [{"w": str(d.get("walletId", "")), "d": str(d.get("positionDate", ""))[:20]} for d in docs_e]
    except Exception as e:
        result["E_error"] = str(e)

    # Approach F: no walletId filter + datetime dates
    try:
        dt_days3 = [datetime.strptime(d, "%Y-%m-%d") for d in sample_days]
        docs_f = list(coll.aggregate([
            {"$match": {"positionDate": {"$in": dt_days3}}},
            {"$limit": 3},
            {"$project": {"walletId": 1, "positionDate": 1}},
        ]))
        result["F_sample_any_wallet_datetime_date"] = [{"w": str(d.get("walletId", "")), "d": str(d.get("positionDate", ""))[:20]} for d in docs_f]
    except Exception as e:
        result["F_error"] = str(e)

    # Raw sample from collection (first 3 docs for a wallet)
    try:
        raw = list(coll.find({"walletId": {"$in": wids}}, {"walletId": 1, "positionDate": 1}).limit(3))
        result["raw_sample"] = [{"w": str(d.get("walletId", "")), "d": repr(d.get("positionDate", ""))} for d in raw]
    except Exception as e:
        result["raw_error"] = str(e)

    return jsonify(result)


@bp.route("/api/rotinas/settings", methods=["POST"])
def save_rotinas_settings():
    data = request.json or {}
    settings = db_module.load_settings()
    if "rent_threshold" in data:
        settings["rent_threshold"] = float(data["rent_threshold"])
    import json as _json
    with open(db_module.SETTINGS_FILE, "w", encoding="utf-8") as f:
        _json.dump(settings, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True})


# ── Issues summary ─────────────────────────────────────────────────────────────

@bp.route("/api/rotinas/issues-count")
def get_issues_count():
    """
    Returns issue counts per walletId per date.
    Filters: type != 'missing_unprocessed_position' AND status != 'solved'.
    Response: {walletId: {date: count}}
    """
    limit = int(request.args.get("limit", 30))
    dates = db_module.get_biz_dates(limit)

    result = {}
    try:
        pipeline = [
            {"$match": {
                "type":         {"$ne": "missing_unprocessed_position"},
                "status":       {"$ne": "solved"},
                "positionDate": {"$in": dates},
            }},
            {"$group": {
                "_id":   {"w": "$walletId", "d": "$positionDate"},
                "count": {"$sum": 1},
            }},
        ]
        for doc in db_module.db.issues.aggregate(pipeline):
            wid  = str(doc["_id"]["w"])
            dstr = str(doc["_id"]["d"])[:10]
            result.setdefault(wid, {})[dstr] = doc["count"]
    except Exception as exc:  # pylint: disable=broad-except
        return jsonify({"error": str(exc)}), 500

    return jsonify(result)


@bp.route("/api/rotinas/nav-check-total")
def get_nav_check_total():
    """
    navPackages per positionDate: total calculated and count where
    abs(returnNavPerShare - returnContribution) > threshold.
    Response: {date: {total, above}}
    """
    limit     = int(request.args.get("limit",     30))
    threshold = float(request.args.get("threshold", 0.01))
    dates     = db_module.get_biz_dates(limit)

    result = {}
    try:
        diff_expr = {"$abs": {"$subtract": [
            "$returnNavPerShare",
            {"$ifNull": ["$returnContribution", 0]},
        ]}}
        pipeline = [
            {"$match": {
                "positionDate":      {"$in": dates},
                "returnNavPerShare": {"$ne": None},
            }},
            # Stage 1: one row per wallet per day — take max diff to avoid double-counting
            {"$group": {
                "_id":     {"walletId": "$walletId", "date": "$positionDate"},
                "maxDiff": {"$max": diff_expr},
            }},
            # Stage 2: count wallets (not documents) per day
            {"$group": {
                "_id":   "$_id.date",
                "total": {"$sum": 1},
                "above": {"$sum": {"$cond": [
                    {"$gt": ["$maxDiff", threshold]},
                    1, 0,
                ]}},
            }},
        ]
        for doc in db_module.db.navPackages.aggregate(pipeline):
            dstr = str(doc["_id"])[:10]
            result[dstr] = {"total": doc["total"], "above": doc["above"]}
    except Exception as exc:  # pylint: disable=broad-except
        return jsonify({"error": str(exc)}), 500

    return jsonify(result)


@bp.route("/api/rotinas/nav-check-by-company")
def get_nav_check_by_company():
    """
    navPackages per companyId per positionDate: unique wallet count and those above threshold.
    Response: {companyId: {date: {total, above}}}
    """
    limit     = int(request.args.get("limit",     30))
    threshold = float(request.args.get("threshold", 0.01))
    dates     = db_module.get_biz_dates(limit)

    result = {}
    try:
        diff_expr = {"$abs": {"$subtract": [
            "$returnNavPerShare",
            {"$ifNull": ["$returnContribution", 0]},
        ]}}
        pipeline = [
            {"$match": {
                "positionDate":      {"$in": dates},
                "returnNavPerShare": {"$ne": None},
            }},
            # Stage 1: one row per wallet per day — carry companyId through
            {"$group": {
                "_id": {
                    "companyId": "$companyId",
                    "walletId":  "$walletId",
                    "date":      "$positionDate",
                },
                "maxDiff":   {"$max": diff_expr},
            }},
            # Stage 2: count unique wallets per company per day
            {"$group": {
                "_id":   {"companyId": "$_id.companyId", "date": "$_id.date"},
                "total": {"$sum": 1},
                "above": {"$sum": {"$cond": [
                    {"$gt": ["$maxDiff", threshold]},
                    1, 0,
                ]}},
            }},
        ]
        for doc in db_module.db.navPackages.aggregate(pipeline):
            cid  = str(doc["_id"]["companyId"])
            dstr = str(doc["_id"]["date"])[:10]
            result.setdefault(cid, {})[dstr] = {"total": doc["total"], "above": doc["above"]}
    except Exception as exc:  # pylint: disable=broad-except
        return jsonify({"error": str(exc)}), 500

    return jsonify(result)


@bp.route("/api/rotinas/transactions-count")
def get_transactions_count():
    """
    Total transaction count per liquidationDate where beehusTransactionType is null/missing.
    Response: {date: count}
    """
    limit = int(request.args.get("limit", 30))
    dates = db_module.get_biz_dates(limit)

    result = {}
    try:
        pipeline = [
            {"$match": {
                "liquidationDate":       {"$in": dates},
                "beehusTransactionType": None,
            }},
            {"$group": {
                "_id":   "$liquidationDate",
                "count": {"$sum": 1},
            }},
        ]
        for doc in db_module.db.transactions.aggregate(pipeline):
            dstr = str(doc["_id"])[:10]
            result[dstr] = doc["count"]
    except Exception as exc:  # pylint: disable=broad-except
        return jsonify({"error": str(exc)}), 500

    return jsonify(result)


@bp.route("/api/rotinas/transactions-by-company")
def get_transactions_by_company():
    """
    Transaction count per companyId per liquidationDate where beehusTransactionType is null/missing.
    Response: {companyId: {date: count}}
    """
    limit = int(request.args.get("limit", 30))
    dates = db_module.get_biz_dates(limit)

    result = {}
    try:
        pipeline = [
            {"$match": {
                "liquidationDate":       {"$in": dates},
                "beehusTransactionType": None,
            }},
            {"$group": {
                "_id":   {"companyId": "$companyId", "date": "$liquidationDate"},
                "count": {"$sum": 1},
            }},
        ]
        for doc in db_module.db.transactions.aggregate(pipeline):
            cid  = str(doc["_id"]["companyId"])
            dstr = str(doc["_id"]["date"])[:10]
            result.setdefault(cid, {})[dstr] = doc["count"]
    except Exception as exc:  # pylint: disable=broad-except
        return jsonify({"error": str(exc)}), 500

    return jsonify(result)


@bp.route("/api/rotinas/issues-positions-by-company")
def get_issues_positions_by_company():
    """
    Issue counts per companyId per date.
    Same query as issues-positions-count but grouped by companyId.
    Response: {companyId: {date: count}}
    """
    limit = int(request.args.get("limit", 30))
    dates = db_module.get_biz_dates(limit)

    result = {}
    try:
        pipeline = [
            {"$match": {
                "status": {"$ne": "solved"},
                "type":   {"$nin": ["missing_unprocessed_position", "missing_wallet"]},
                "date":   {"$in": dates},
            }},
            {"$group": {
                "_id":   {"companyId": "$companyId", "date": "$date"},
                "count": {"$sum": 1},
            }},
        ]
        for doc in db_module.db.issues.aggregate(pipeline):
            cid  = str(doc["_id"]["companyId"])
            dstr = str(doc["_id"]["date"])[:10]
            result.setdefault(cid, {})[dstr] = doc["count"]
    except Exception as exc:  # pylint: disable=broad-except
        return jsonify({"error": str(exc)}), 500

    return jsonify(result)


@bp.route("/api/rotinas/issues-positions-count")
def get_issues_positions_count():
    """
    Total issue count per date for Processamento Posições view.
    Query: {date: <date>, status: {$ne: 'solved'}, type: {$nin: ['missing_unprocessed_position', 'missing_wallet']}}
    Response: {date_str: count}
    """
    limit = int(request.args.get("limit", 30))
    dates = db_module.get_biz_dates(limit)

    result = {}
    try:
        pipeline = [
            {"$match": {
                "status": {"$ne": "solved"},
                "type":   {"$nin": ["missing_unprocessed_position", "missing_wallet"]},
                "date":   {"$in": dates},
            }},
            {"$group": {
                "_id":   "$date",
                "count": {"$sum": 1},
            }},
        ]
        for doc in db_module.db.issues.aggregate(pipeline):
            dstr = str(doc["_id"])[:10]
            result[dstr] = doc["count"]
    except Exception as exc:  # pylint: disable=broad-except
        return jsonify({"error": str(exc)}), 500

    return jsonify(result)


@bp.route("/api/rotinas/groupings-publication-total")
def get_groupings_publication_total():
    """
    Groupings per positionDate: total within rent threshold and count not yet published.
    'Not published' means published field is not True.
    Response: {date: {total, not_published}}
    """
    limit     = int(request.args.get("limit",     30))
    threshold = float(request.args.get("threshold", 0.01))
    dates     = db_module.get_biz_dates(limit)

    result = {}
    try:
        pipeline = [
            {"$match": {
                "positionDate":      {"$in": dates},
                "returnNavPerShare": {"$ne": None},
                "$expr": {"$lte": [
                    {"$abs": {"$subtract": [
                        "$returnNavPerShare",
                        {"$ifNull": ["$returnContribution", 0]},
                    ]}},
                    threshold,
                ]},
            }},
            {"$group": {
                "_id":           "$positionDate",
                "total":         {"$sum": 1},
                "not_published": {"$sum": {"$cond": [
                    {"$ne": ["$published", True]},
                    1, 0,
                ]}},
            }},
        ]
        for doc in db_module.db.groupings.aggregate(pipeline):
            dstr = str(doc["_id"])[:10]
            result[dstr] = {"total": doc["total"], "not_published": doc["not_published"]}
    except Exception as exc:  # pylint: disable=broad-except
        return jsonify({"error": str(exc)}), 500

    return jsonify(result)


@bp.route("/api/rotinas/groupings-publication-by-company")
def get_groupings_publication_by_company():
    """
    Groupings per companyId per positionDate: total within threshold and count not published.
    Response: {companyId: {date: {total, not_published}}}
    """
    limit     = int(request.args.get("limit",     30))
    threshold = float(request.args.get("threshold", 0.01))
    dates     = db_module.get_biz_dates(limit)

    result = {}
    try:
        pipeline = [
            {"$match": {
                "positionDate":      {"$in": dates},
                "returnNavPerShare": {"$ne": None},
                "$expr": {"$lte": [
                    {"$abs": {"$subtract": [
                        "$returnNavPerShare",
                        {"$ifNull": ["$returnContribution", 0]},
                    ]}},
                    threshold,
                ]},
            }},
            {"$group": {
                "_id":           {"companyId": "$companyId", "date": "$positionDate"},
                "total":         {"$sum": 1},
                "not_published": {"$sum": {"$cond": [
                    {"$ne": ["$published", True]},
                    1, 0,
                ]}},
            }},
        ]
        for doc in db_module.db.groupings.aggregate(pipeline):
            cid  = str(doc["_id"]["companyId"])
            dstr = str(doc["_id"]["date"])[:10]
            result.setdefault(cid, {})[dstr] = {
                "total": doc["total"], "not_published": doc["not_published"],
            }
    except Exception as exc:  # pylint: disable=broad-except
        return jsonify({"error": str(exc)}), 500

    return jsonify(result)


# ── Weekly rows ────────────────────────────────────────────────────────────────

@bp.route("/api/rotinas/rows/weekly", methods=["POST"])
def add_weekly_row():
    data = request.json or {}
    rows = _load_weekly_rows()
    row  = {
        "id":          f"w_{uuid.uuid4().hex[:8]}",
        "categoria":   data.get("categoria", ""),
        "parceiro":    data.get("parceiro", ""),
        "responsavel": data.get("responsavel", ""),
        "obs":         data.get("obs", ""),
    }
    rows.append(row)
    _save_weekly_rows(rows)
    return jsonify({"ok": True, "id": row["id"]})


@bp.route("/api/rotinas/rows/weekly/<row_id>", methods=["DELETE"])
def delete_weekly_row(row_id):
    rows = [r for r in _load_weekly_rows() if r["id"] != row_id]
    _save_weekly_rows(rows)
    return jsonify({"ok": True})


@bp.route("/api/rotinas/weekly/status/<row_id>/<col_id>", methods=["POST"])
def set_weekly_status(row_id, col_id):
    data   = request.json or {}
    new_st = data.get("status", "done")
    store  = _load_weekly_status()
    key    = f"{row_id}_{col_id}"
    if not new_st or store.get(key) == new_st:
        store.pop(key, None)
    else:
        store[key] = new_st
    _save_weekly_status(store)
    return jsonify({"ok": True, "status": store.get(key, "")})


@bp.route("/api/rotinas/weekly/comments/<row_id>/<col_id>", methods=["GET"])
def get_weekly_comments(row_id, col_id):
    return jsonify(_load_weekly_status().get(f"comment_{row_id}_{col_id}", []))


@bp.route("/api/rotinas/weekly/comments/<row_id>/<col_id>", methods=["POST"])
def add_weekly_comment(row_id, col_id):
    text = (request.json or {}).get("text", "").strip()
    if not text:
        return jsonify({"ok": False}), 400
    store    = _load_weekly_status()
    key      = f"comment_{row_id}_{col_id}"
    comments = store.get(key, [])
    comments.append({"text": text, "created_at": datetime.utcnow().isoformat()})
    store[key] = comments
    _save_weekly_status(store)
    return jsonify({"ok": True, "comments": comments})


@bp.route("/api/rotinas/weekly/comments/<row_id>/<col_id>/<int:idx>", methods=["DELETE"])
def delete_weekly_comment(row_id, col_id, idx):
    store    = _load_weekly_status()
    key      = f"comment_{row_id}_{col_id}"
    comments = store.get(key, [])
    if 0 <= idx < len(comments):
        comments.pop(idx)
    if comments:
        store[key] = comments
    else:
        store.pop(key, None)
    _save_weekly_status(store)
    return jsonify({"ok": True})


# ── Monthly rows ───────────────────────────────────────────────────────────────

@bp.route("/api/rotinas/rows/monthly", methods=["POST"])
def add_monthly_row():
    data = request.json or {}
    rows = _load_monthly_rows()
    row  = {
        "id":          f"m_{uuid.uuid4().hex[:8]}",
        "categoria":   data.get("categoria", ""),
        "parceiro":    data.get("parceiro", ""),
        "responsavel": data.get("responsavel", ""),
        "obs":         data.get("obs", ""),
    }
    rows.append(row)
    _save_monthly_rows(rows)
    return jsonify({"ok": True, "id": row["id"]})


@bp.route("/api/rotinas/rows/monthly/<row_id>", methods=["DELETE"])
def delete_monthly_row(row_id):
    rows = [r for r in _load_monthly_rows() if r["id"] != row_id]
    _save_monthly_rows(rows)
    return jsonify({"ok": True})


@bp.route("/api/rotinas/monthly/status/<row_id>/<col_id>", methods=["POST"])
def set_monthly_status(row_id, col_id):
    data   = request.json or {}
    new_st = data.get("status", "done")
    store  = _load_monthly_status()
    key    = f"{row_id}_{col_id}"
    if not new_st or store.get(key) == new_st:
        store.pop(key, None)
    else:
        store[key] = new_st
    _save_monthly_status(store)
    return jsonify({"ok": True, "status": store.get(key, "")})


@bp.route("/api/rotinas/monthly/comments/<row_id>/<col_id>", methods=["GET"])
def get_monthly_comments(row_id, col_id):
    return jsonify(_load_monthly_status().get(f"comment_{row_id}_{col_id}", []))


@bp.route("/api/rotinas/monthly/comments/<row_id>/<col_id>", methods=["POST"])
def add_monthly_comment(row_id, col_id):
    text = (request.json or {}).get("text", "").strip()
    if not text:
        return jsonify({"ok": False}), 400
    store    = _load_monthly_status()
    key      = f"comment_{row_id}_{col_id}"
    comments = store.get(key, [])
    comments.append({"text": text, "created_at": datetime.utcnow().isoformat()})
    store[key] = comments
    _save_monthly_status(store)
    return jsonify({"ok": True, "comments": comments})


@bp.route("/api/rotinas/monthly/comments/<row_id>/<col_id>/<int:idx>", methods=["DELETE"])
def delete_monthly_comment(row_id, col_id, idx):
    store    = _load_monthly_status()
    key      = f"comment_{row_id}_{col_id}"
    comments = store.get(key, [])
    if 0 <= idx < len(comments):
        comments.pop(idx)
    if comments:
        store[key] = comments
    else:
        store.pop(key, None)
    _save_monthly_status(store)
    return jsonify({"ok": True})
