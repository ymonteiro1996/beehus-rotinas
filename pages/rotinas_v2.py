import time
from datetime import date

from flask import Blueprint, render_template, request

import db as db_module
from pages.rotinas_diarias import (
    _biz_days, _biz_weeks, _year_months,
    _load_templates, _load_row_config, _build_template_rows,
    _mongo_status, _load_status, _load_settings, _load_custom_rows,
    _load_weekly_rows, _load_monthly_rows,
    _load_weekly_status, _load_monthly_status,
    _prev_month, _next_month,
    MANUAL_ROWS, MONTH_NAMES, AUTOMATIZACAO_OPTS,
    _page_generation,
)

bp = Blueprint("rotinas_v2", __name__)

_page_cache: dict = {}


@bp.route("/rotinas-v2")
def index():
    today = date.today()
    year  = int(request.args.get("year",  today.year))
    month = int(request.args.get("month", today.month))

    _pkey = (year, month, _page_generation[0])
    _pe   = _page_cache.get(_pkey)
    if _pe and time.monotonic() < _pe[0]:
        return _pe[1]
    days  = _biz_days(year, month)

    templates  = _load_templates()
    row_config = _load_row_config()

    carga_rows, proc_pos_rows, proc_tx_rows, check_rent_rows, wallet_delay_map = \
        _build_template_rows(templates, row_config)
    template_proc_pos_ids   = {r["id"] for r in proc_pos_rows}
    template_proc_tx_ids    = {r["id"] for r in proc_tx_rows}
    template_check_rent_ids = {r["id"] for r in check_rent_rows}


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

    # Extended pool of past biz days for 5d/10d/30d selector (cross-month)
    biz_days_pool = db_module.get_biz_dates(35)

    # Use the full pool for STATUS so 10d/30d views have correct historical data
    elapsed = {day: db_module.biz_days_elapsed(day) for day in biz_days_pool}

    # parceiro → companyId mapping used by Processamento + Conciliação views
    wallet_to_pair, _ = db_module.build_wallet_map()
    co_company_map: dict = {}
    for row in proc_pos_rows + proc_tx_rows + check_rent_rows:
        parceiro = row["parceiro"]
        if parceiro not in co_company_map:
            for wid in row.get("wallet_ids", []):
                if wid in wallet_to_pair:
                    co_company_map[parceiro] = wallet_to_pair[wid][0]
                    break

    row_st, wallet_st = _mongo_status(
        mongo_rows, biz_days_pool, year, month, wallet_delay_map, elapsed, rent_thr,
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
            status[row["id"]] = {
                day: manual_st.get(f"{row['id']}_{day}", "")
                for day in biz_days_pool
            }

    weeks        = _biz_weeks(year, month)
    year_mons    = _year_months(year)
    weekly_rows  = _load_weekly_rows()
    monthly_rows = _load_monthly_rows()
    weekly_st_raw  = _load_weekly_status()
    monthly_st_raw = _load_monthly_status()

    status_w = {
        row["id"]: {week["id"]: weekly_st_raw.get(f"{row['id']}_{week['id']}", "") for week in weeks}
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
        row["id"]: {mon["id"]: monthly_st_raw.get(f"{row['id']}_{mon['id']}", "") for mon in year_mons}
        for row in monthly_rows
    }
    comments_m = {}
    for key, val in monthly_st_raw.items():
        if key.startswith("comment_"):
            rest   = key[len("comment_"):]
            col_id = rest[-7:]
            rid    = rest[:-8]
            comments_m.setdefault(rid, {})[col_id] = val

    _html = render_template(
        "rotinas_v2.html",
        active="rotinas_v2",
        rows=all_rows,
        co_company_map=co_company_map,
        biz_days_pool=biz_days_pool,
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
    _page_cache[_pkey] = (time.monotonic() + 120, _html)
    return _html
