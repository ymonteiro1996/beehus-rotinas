import json
import os
import time
from datetime import date, datetime
from collections import defaultdict

from flask import Blueprint, jsonify, render_template, request

import db as db_module
from pages.rotinas_diarias import (  # pylint: disable=import-error
    _load_templates, _biz_days, _load_settings, _load_row_config,
    _build_template_rows, _mongo_status,
)

bp = Blueprint("saude_operacional", __name__)

TIME_LOG_FILE    = os.path.join(os.path.dirname(__file__), "..", "data", "op_time_log.json")
NOTES_FILE       = os.path.join(os.path.dirname(__file__), "..", "data", "company_notes.json")
DEMANDS_FILE     = os.path.join(os.path.dirname(__file__), "..", "data", "company_demands.json")
ONBOARDING_FILE  = os.path.join(os.path.dirname(__file__), "..", "data", "company_onboarding.json")

MONTH_NAMES = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]

_cache: dict = {}

def _cache_get(key):
    entry = _cache.get(key)
    if entry and time.monotonic() < entry[1]:
        return entry[0], True
    return None, False

def _cache_set(key, value, ttl):
    _cache[key] = (value, time.monotonic() + ttl)


def _load_time_log():
    cached, hit = _cache_get("time_log")
    if hit:
        return cached
    if not os.path.exists(TIME_LOG_FILE):
        return []
    with open(TIME_LOG_FILE, "r", encoding="utf-8") as fh:
        entries = json.load(fh)
    for i, e in enumerate(entries):
        if "id" not in e:
            e["id"] = f"{e.get('date', 'x')}_{i:04d}"
    _cache_set("time_log", entries, 10)
    return entries


def _save_time_log(data):
    with open(TIME_LOG_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    _cache.pop("time_log", None)


def _load_company_notes():
    cached, hit = _cache_get("company_notes")
    if hit:
        return cached
    if not os.path.exists(NOTES_FILE):
        return {}
    with open(NOTES_FILE, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    _cache_set("company_notes", data, 30)
    return data


def _save_company_notes(data):
    with open(NOTES_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    _cache.pop("company_notes", None)


def _load_demands():
    cached, hit = _cache_get("demands")
    if hit:
        return cached
    if not os.path.exists(DEMANDS_FILE):
        return {}
    with open(DEMANDS_FILE, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    _cache_set("demands", data, 30)
    return data


def _save_demands(data):
    with open(DEMANDS_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    _cache.pop("demands", None)


def _load_onboarding():
    cached, hit = _cache_get("onboarding")
    if hit:
        return cached
    if not os.path.exists(ONBOARDING_FILE):
        return {}
    with open(ONBOARDING_FILE, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    _cache_set("onboarding", data, 30)
    return data


def _save_onboarding(data):
    with open(ONBOARDING_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    _cache.pop("onboarding", None)


def _last_published_by_company():
    """Return max positionDate per companyId from publishedPositionSecurities."""
    cached, hit = _cache_get("last_pub")
    if hit:
        return cached
    if not db_module.db._ready():
        return {}
    pipeline = [
        {"$group": {
            "_id":       "$companyId",
            "last_date": {"$max": "$positionDate"},
        }},
    ]
    result = {}
    try:
        for doc in db_module.db.publishedPositionSecurities.aggregate(pipeline):
            if not doc["_id"] or not doc.get("last_date"):
                continue
            result[str(doc["_id"])] = str(doc["last_date"])[:10]
    except Exception:  # pylint: disable=broad-except
        pass
    _cache_set("last_pub", result, 300)
    return result


def _build_last_pub_by_partner(last_published_by_company):
    """Map parceiro name → last published date using wallet→companyId lookup."""
    cached, hit = _cache_get("last_pub_by_partner")
    if hit:
        return cached
    templates  = _load_templates()
    row_config = _load_row_config()
    carga, proc_pos, proc_tx, check_rent, _ = _build_template_rows(
        templates, row_config
    )
    wallet_to_pair, _ = db_module.build_wallet_map()
    result = {}
    for row in carga + proc_pos + proc_tx + check_rent:
        partner = row["parceiro"]
        if partner in result:
            continue
        for wid in row.get("wallet_ids", []):
            if wid in wallet_to_pair:
                cid = str(wallet_to_pair[wid][0])
                pub = last_published_by_company.get(cid, "")
                if pub:
                    result[partner] = pub
                break
    _cache_set("last_pub_by_partner", result, 300)
    return result


def _partners_from_filter():
    """Return sorted partner names for every company in company_filter.

    Prefers the parceiro label from wallet templates; falls back to
    db.companies.name for companies that have no configured template.
    """
    cached, hit = _cache_get("partners_filter")
    if hit:
        return cached

    settings = _load_settings()
    cf = settings.get("company_filter", [])
    if not cf:
        return []

    # companyId → parceiro from template rows
    templates  = _load_templates()
    row_config = _load_row_config()
    carga, proc_pos, proc_tx, check_rent, _ = _build_template_rows(
        templates, row_config
    )
    wallet_to_pair, _ = db_module.build_wallet_map()
    cid_to_partner: dict = {}
    for row in carga + proc_pos + proc_tx + check_rent:
        for wid in row.get("wallet_ids", []):
            if wid in wallet_to_pair:
                cid = str(wallet_to_pair[wid][0])
                if cid not in cid_to_partner:
                    cid_to_partner[cid] = row["parceiro"]

    # Fall back to db.companies.name for any unmapped IDs
    unmapped = [cid for cid in cf if cid not in cid_to_partner]
    if unmapped and db_module.db._ready():
        try:
            for doc in db_module.db.companies.find(
                {"_id": {"$in": unmapped}}, {"name": 1}
            ):
                cid = str(doc["_id"])
                if cid not in cid_to_partner:
                    cid_to_partner[cid] = doc.get("name") or cid
        except Exception:  # pylint: disable=broad-except
            pass

    excluded = set(settings.get("saude_excluded_partners", []))

    names = []
    seen: set = set()
    for cid in cf:
        name = cid_to_partner.get(cid)
        if name and name not in seen and name not in excluded:
            names.append(name)
            seen.add(name)
    result = sorted(names)
    _cache_set("partners_filter", result, 600)
    return result


def _sentiment_history(all_notes, partner, year, month):
    """Return last 3 months of sentiment for a partner, oldest first."""
    hist = []
    cur_y, cur_m = year, month
    for _ in range(3):
        key  = f"{cur_y}-{cur_m:02d}"
        note = all_notes.get(key, {}).get(partner, {})
        hist.append({
            "month": key,
            "op":    note.get("status_operacional", "none"),
            "sent":  note.get("status_sentimento",  "none"),
        })
        cur_y, cur_m = (
            (cur_y - 1, 12) if cur_m == 1 else (cur_y, cur_m - 1)
        )
    hist.reverse()
    return hist


def _company_health(year, month):
    """Compute per-partner health using business days D-3 to D-8."""
    cache_key = f"health_{year}_{month}"
    cached, hit = _cache_get(cache_key)
    if hit:
        return cached

    days     = _biz_days(year, month)
    today_s  = date.today().strftime("%Y-%m-%d")
    past_all = [d for d in days if d <= today_s]
    past     = past_all[-7:-2]
    if not past:
        return {}, past

    templates  = _load_templates()
    row_config = _load_row_config()
    carga, proc_pos, proc_tx, check_rent, wdm = _build_template_rows(templates, row_config)

    elapsed    = {d: db_module.biz_days_elapsed(d) for d in past}
    settings   = _load_settings()
    rent_thr   = settings.get("rent_threshold", 0.01)
    mongo_rows = carga + proc_pos + proc_tx + check_rent

    row_st, _ = _mongo_status(mongo_rows, past, year, month, wdm, elapsed, rent_thr)

    health = {}
    for row in mongo_rows:
        partner = row["parceiro"]
        cat     = row["categoria"]
        if partner not in health:
            health[partner] = {}

        st_days = row_st.get(row["id"], {})
        counts  = defaultdict(int)
        for d in past:
            s = st_days.get(d, "")
            if s and s not in ("future", "not_expected"):
                counts[s] += 1

        total   = sum(counts.values())
        done    = counts["done"]
        partial = counts["partial"]

        if total == 0:
            score = "none"
        elif done == total:
            score = "green"
        elif (done + partial) >= total * 0.7:
            score = "yellow"
        else:
            score = "red"

        health[partner][cat] = {
            "score": score,
            "done": done, "partial": partial,
            "missing": counts["missing"], "total": total,
        }

    # Overall per partner
    order = {"green": 0, "yellow": 1, "red": 2, "none": 3}
    for partner, cats in health.items():
        scores = [v["score"] for v in cats.values() if v["score"] != "none"]
        if not scores:
            overall = "none"
        else:
            overall = sorted(scores, key=lambda s: order[s])[-1]
        health[partner]["_overall"] = overall

    result = (health, past)
    _cache_set(cache_key, result, 300)
    return result


def _prev_month(y, m): return (y - 1, 12) if m == 1 else (y, m - 1)
def _next_month(y, m): return (y + 1, 1)  if m == 12 else (y, m + 1)


_so_page_cache: dict = {}


@bp.route("/saude-operacional")
def index():
    today = date.today()
    year  = int(request.args.get("year",  today.year))
    month = int(request.args.get("month", today.month))

    _pkey = (year, month)
    _pe   = _so_page_cache.get(_pkey)
    if _pe and time.monotonic() < _pe[0]:
        return _pe[1]

    health, past_days = _company_health(year, month)

    month_key  = f"{year}-{month:02d}"
    all_notes  = _load_company_notes()
    company_notes = all_notes.get(month_key, {})

    # Last published date per partner (from MongoDB groupings)
    last_pub_by_partner = _build_last_pub_by_partner(_last_published_by_company())

    demands = _load_demands()

    # all_partners: companies from filter first, then any extras from notes/demands
    excluded        = set(_load_settings().get("saude_excluded_partners", []))
    filter_partners = _partners_from_filter()
    extra_partners  = sorted(
        (set(health.keys()) | set(company_notes.keys()) | set(demands.keys()))
        - set(filter_partners) - excluded
    )
    all_partners = filter_partners + extra_partners

    sentiment_history = {
        p: _sentiment_history(all_notes, p, year, month)
        for p in all_partners
    }

    # Time log for this month
    all_entries = _load_time_log()
    prefix  = f"{year}-{month:02d}"
    entries = [e for e in all_entries if e.get("date", "").startswith(prefix)]
    entries.sort(key=lambda e: e["date"], reverse=True)

    total_minutes = sum(e.get("minutes", e.get("hours", 0)) for e in entries)
    total_manual  = sum(e.get("manual_count", 0) for e in entries)
    biz_count     = len(_biz_days(year, month))
    avg_minutes   = round(total_minutes / len(entries), 1) if entries else 0

    _html = render_template(
        "saude_operacional.html",
        active="saude_operacional",
        health=health,
        past_days=past_days,
        year=year,
        month=month,
        month_name=MONTH_NAMES[month],
        prev_m=_prev_month(year, month),
        next_m=_next_month(year, month),
        today=today.strftime("%Y-%m-%d"),
        entries=entries,
        total_minutes=total_minutes,
        total_manual=total_manual,
        avg_minutes=avg_minutes,
        biz_count=biz_count,
        company_notes=company_notes,
        all_partners=all_partners,
        last_pub_by_partner=last_pub_by_partner,
        sentiment_history=sentiment_history,
        demands=demands,
        onboarding=_load_onboarding(),
    )
    _so_page_cache[_pkey] = (time.monotonic() + 30, _html)
    return _html


@bp.route("/api/saude/time-log", methods=["GET"])
def list_time_log():
    year  = int(request.args.get("year",  date.today().year))
    month = int(request.args.get("month", date.today().month))
    all_e = _load_time_log()
    prefix = f"{year}-{month:02d}"
    return jsonify([e for e in all_e if e.get("date", "").startswith(prefix)])


@bp.route("/api/saude/time-log", methods=["POST"])
def add_time_log():
    data = request.json or {}
    day  = data.get("date", "").strip()
    if not day:
        return jsonify({"ok": False, "error": "date required"}), 400

    mins     = int(data.get("minutes", 0))
    pessoa   = data.get("pessoa", "").strip()
    ts       = datetime.utcnow().isoformat()
    entry_id = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    all_e    = _load_time_log()
    additions = [{"minutes": mins, "timestamp": ts, "pessoa": pessoa}] if mins else []
    all_e.append({
        "id":           entry_id,
        "date":         day,
        "minutes":      mins,
        "pessoa":       pessoa,
        "notes":        data.get("notes", "").strip(),
        "additions":    additions,
        "updated_at":   ts,
    })

    _save_time_log(all_e)
    return jsonify({"ok": True})


@bp.route("/api/saude/time-log/<entry_id>/add", methods=["POST"])
def append_time_log(entry_id):
    data = request.json or {}
    mins = int(data.get("minutes", 0))
    if not mins:
        return jsonify({"ok": False, "error": "minutes required"}), 400

    pessoa = data.get("pessoa", "").strip()
    ts    = datetime.utcnow().isoformat()
    all_e = _load_time_log()
    idx   = next((i for i, e in enumerate(all_e) if e.get("id") == entry_id), None)
    if idx is None:
        return jsonify({"ok": False, "error": "entry not found"}), 404

    entry = all_e[idx]
    additions = entry.get("additions", [])
    if not additions and entry.get("minutes", 0):
        additions = [{"minutes": entry["minutes"], "timestamp": entry.get("updated_at", ts), "pessoa": entry.get("pessoa", "")}]
    additions.append({"minutes": mins, "timestamp": ts, "pessoa": pessoa})
    entry["additions"]  = additions
    entry["minutes"]    = sum(a["minutes"] for a in additions)
    entry["updated_at"] = ts

    _save_time_log(all_e)
    return jsonify({"ok": True, "total_minutes": entry["minutes"]})


@bp.route("/api/saude/time-log/<entry_id>", methods=["DELETE"])
def delete_time_log(entry_id):
    all_e = [e for e in _load_time_log() if e.get("id") != entry_id]
    _save_time_log(all_e)
    return jsonify({"ok": True})


@bp.route("/api/saude/company-notes", methods=["POST"])
def save_company_notes():
    data    = request.get_json(force=True) or {}
    partner = data.get("partner", "").strip()
    if not partner:
        return jsonify({"ok": False, "error": "partner required"}), 400

    year      = int(data.get("year",  date.today().year))
    month     = int(data.get("month", date.today().month))
    month_key = f"{year}-{month:02d}"

    all_notes = _load_company_notes()
    all_notes.setdefault(month_key, {})[partner] = {
        "demandas":           data.get("demandas",           ""),
        "pendencias":         data.get("pendencias",         ""),
        "status_operacional": data.get("status_operacional", "none"),
        "status_sentimento":  data.get("status_sentimento",  "none"),
        "override_overall":   data.get("override_overall",   ""),
    }
    _save_company_notes(all_notes)
    return jsonify({"ok": True})


@bp.route("/api/saude/demands", methods=["POST"])
def add_demand():
    data    = request.get_json(force=True) or {}
    partner = data.get("partner", "").strip()
    title   = data.get("title",   "").strip()
    if not partner or not title:
        return jsonify({"ok": False, "error": "partner and title required"}), 400

    demand = {
        "id":         datetime.utcnow().strftime("%Y%m%d%H%M%S%f"),
        "title":      title,
        "sla":        data.get("sla", ""),
        "created_at": date.today().isoformat(),
        "closed":     False,
    }
    all_d = _load_demands()
    all_d.setdefault(partner, []).append(demand)
    _save_demands(all_d)
    return jsonify({"ok": True, "demand": demand})


@bp.route("/api/saude/demands/<partner>/<demand_id>", methods=["DELETE"])
def close_demand(partner, demand_id):
    all_d = _load_demands()
    for dem in all_d.get(partner, []):
        if dem.get("id") == demand_id:
            dem["closed"]    = True
            dem["closed_at"] = date.today().isoformat()
            break
    _save_demands(all_d)
    return jsonify({"ok": True})


# ── Onboarding ─────────────────────────────────────────────────────────────────

@bp.route("/api/saude/onboarding/fragment")
def onboarding_fragment():
    return render_template(
        "_onboarding_partial.html",
        onboarding=_load_onboarding(),
        today=date.today().strftime("%Y-%m-%d"),
    )


@bp.route("/api/saude/onboarding/companies", methods=["POST"])
def ob_add_company():
    data    = request.get_json(force=True) or {}
    company = data.get("company", "").strip()
    if not company:
        return jsonify({"ok": False}), 400
    all_ob = _load_onboarding()
    if company not in all_ob:
        all_ob[company] = []
    _save_onboarding(all_ob)
    return jsonify({"ok": True})


@bp.route("/api/saude/onboarding/companies/<company>", methods=["DELETE"])
def ob_remove_company(company):
    all_ob = _load_onboarding()
    all_ob.pop(company, None)
    _save_onboarding(all_ob)
    return jsonify({"ok": True})


@bp.route("/api/saude/onboarding/demands", methods=["POST"])
def ob_add_demand():
    data    = request.get_json(force=True) or {}
    company = data.get("company", "").strip()
    title   = data.get("title",   "").strip()
    if not company or not title:
        return jsonify({"ok": False}), 400
    demand = {
        "id":         datetime.utcnow().strftime("%Y%m%d%H%M%S%f"),
        "title":      title,
        "sla":        data.get("sla", ""),
        "done":       False,
        "created_at": date.today().isoformat(),
        "checklist":  [],
    }
    all_ob = _load_onboarding()
    all_ob.setdefault(company, []).append(demand)
    _save_onboarding(all_ob)
    return jsonify({"ok": True})


@bp.route("/api/saude/onboarding/demands/<company>/<demand_id>", methods=["PATCH"])
def ob_toggle_demand(company, demand_id):
    all_ob = _load_onboarding()
    for dem in all_ob.get(company, []):
        if dem.get("id") == demand_id:
            dem["done"] = not dem.get("done", False)
            break
    _save_onboarding(all_ob)
    return jsonify({"ok": True})


@bp.route("/api/saude/onboarding/demands/<company>/<demand_id>", methods=["DELETE"])
def ob_delete_demand(company, demand_id):
    all_ob = _load_onboarding()
    all_ob[company] = [
        d for d in all_ob.get(company, []) if d.get("id") != demand_id
    ]
    _save_onboarding(all_ob)
    return jsonify({"ok": True})


@bp.route("/api/saude/onboarding/checklist", methods=["POST"])
def ob_add_item():
    data      = request.get_json(force=True) or {}
    company   = data.get("company",   "").strip()
    demand_id = data.get("demand_id", "").strip()
    text      = data.get("text",      "").strip()
    if not company or not demand_id or not text:
        return jsonify({"ok": False}), 400
    item = {
        "id":   datetime.utcnow().strftime("%Y%m%d%H%M%S%f"),
        "text": text,
        "sla":  data.get("sla", ""),
        "done": False,
    }
    all_ob = _load_onboarding()
    for dem in all_ob.get(company, []):
        if dem.get("id") == demand_id:
            dem.setdefault("checklist", []).append(item)
            break
    _save_onboarding(all_ob)
    return jsonify({"ok": True})


@bp.route(
    "/api/saude/onboarding/checklist/<company>/<demand_id>/<item_id>",
    methods=["PATCH"],
)
def ob_toggle_item(company, demand_id, item_id):
    all_ob = _load_onboarding()
    for dem in all_ob.get(company, []):
        if dem.get("id") == demand_id:
            for itm in dem.get("checklist", []):
                if itm.get("id") == item_id:
                    itm["done"] = not itm.get("done", False)
                    break
            break
    _save_onboarding(all_ob)
    return jsonify({"ok": True})


@bp.route(
    "/api/saude/onboarding/checklist/<company>/<demand_id>/<item_id>",
    methods=["DELETE"],
)
def ob_delete_item(company, demand_id, item_id):
    all_ob = _load_onboarding()
    for dem in all_ob.get(company, []):
        if dem.get("id") == demand_id:
            dem["checklist"] = [
                i for i in dem.get("checklist", []) if i.get("id") != item_id
            ]
            break
    _save_onboarding(all_ob)
    return jsonify({"ok": True})


# ── Onboarding Esteira (v2) ────────────────────────────────────────────────────

ESTEIRA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "onboarding_esteira.json")

ESTEIRA_PHASES = [
    {"n": 1, "name": "Definição e Preparação",   "resp": "Parceiro + CS Beehus", "wait": "parceiro"},
    {"n": 2, "name": "Arquivos e Estrutura",      "resp": "Parceiro + Beehus",    "wait": "parceiro"},
    {"n": 3, "name": "Processamento",             "resp": "Beehus (Operações)",   "wait": "beehus"},
    {"n": 4, "name": "Configuração e Relatórios", "resp": "Parceiro + CS Beehus", "wait": "parceiro"},
    {"n": 5, "name": "Validação",                 "resp": "Parceiro",             "wait": "parceiro"},
    {"n": 6, "name": "Conclusão",                 "resp": "Beehus",               "wait": "beehus"},
]

ESTEIRA_DELS = [
    [
        "Cadastro da empresa e do responsável na plataforma (CNPJ, país, endereço, admin)",
        "Definição do período histórico a processar",
        "Escolha do modelo de processamento dos dados históricos",
        "Volumetria inicial — instituições, famílias, clientes, agrupamentos e carteiras",
    ],
    [
        "Mapeamento das instituições financeiras e forma de acesso (scraping, API, XML, PDF)",
        "Árvore de classificação de ativos (até 5 níveis hierárquicos)",
        "Mapeamento e cadastro de carteiras e agrupamentos",
        "Estruturas e particularidades de precificação (curva, preços de terceiros, derivativos)",
        "Envio dos arquivos com dados históricos para validação operacional",
    ],
    [
        "Validação dos arquivos recebidos pelo time operacional",
        "Processamento dos dados históricos (fechamentos mensais + consolidação diária)",
        "Verificação da integridade dos dados processados",
    ],
    [
        "Identidade visual — logos PNG/SVG, manual de marca, capa e disclaimer",
        "Configuração do sistema white label",
        "Lista de usuários e hierarquia de acesso",
        "Definição dos relatórios que serão gerados",
        "Frequência de geração (mensal, trimestral, sob demanda)",
    ],
    [
        "Revisão dos dados publicados pelo parceiro",
        "Registro de inconsistências",
        "Ajustes pela Beehus conforme apontamentos",
        "Sign-off do parceiro",
    ],
    [
        "Ativação do processamento diário",
        "Onboarding marcado como concluído",
    ],
]


def _load_esteira():
    if not os.path.exists(ESTEIRA_FILE):
        return []
    with open(ESTEIRA_FILE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _save_esteira(data):
    with open(ESTEIRA_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def _make_esteira_phases():
    phases = []
    for meta in ESTEIRA_PHASES:
        n = len(ESTEIRA_DELS[meta["n"] - 1])
        phases.append({
            "dels":      [False] * n,
            "details":   [""] * n,
            "completed": None,
            "due":       None,
            "overdue":   0,
            "notes":     "",
        })
    return phases


@bp.route("/api/saude/onboarding/esteira", methods=["GET"])
def esteira_list():
    return jsonify(_load_esteira())


@bp.route("/api/saude/onboarding/esteira", methods=["POST"])
def esteira_create():
    data = request.get_json(force=True) or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "error": "name required"}), 400

    phases = _make_esteira_phases()
    if data.get("due_fase1"):
        phases[0]["due"] = data["due_fase1"]

    ob = {
        "id":        datetime.utcnow().strftime("%Y%m%d%H%M%S%f"),
        "name":      name,
        "cnpj":      data.get("cnpj", "").strip(),
        "inicio":    data.get("inicio", date.today().isoformat()),
        "current":   1,
        "atrasado":  False,
        "waiting":   ESTEIRA_PHASES[0]["wait"],
        "concluded": False,
        "phases":    phases,
        "carteiras": [],
        "todos":     [],
    }
    all_ob = _load_esteira()
    all_ob.append(ob)
    _save_esteira(all_ob)
    return jsonify({"ok": True, "ob": ob})


@bp.route("/api/saude/onboarding/esteira/<ob_id>", methods=["PATCH"])
def esteira_patch(ob_id):
    data   = request.get_json(force=True) or {}
    action = data.get("action")
    all_ob = _load_esteira()
    ob     = next((x for x in all_ob if x["id"] == ob_id), None)
    if not ob:
        return jsonify({"ok": False, "error": "not found"}), 404

    if action == "toggle_deliver":
        pi = int(data["phase_idx"])
        di = int(data["del_idx"])
        ob["phases"][pi]["dels"][di] = not ob["phases"][pi]["dels"][di]

    elif action == "save_detail":
        pi = int(data["phase_idx"])
        di = int(data["del_idx"])
        if "details" not in ob["phases"][pi]:
            ob["phases"][pi]["details"] = [""] * len(ob["phases"][pi]["dels"])
        ob["phases"][pi]["details"][di] = data.get("text", "").strip()

    elif action == "save_notes":
        pi = int(data["phase_idx"])
        ob["phases"][pi]["notes"] = data.get("notes", "")

    elif action == "advance":
        cur = ob["current"]
        if cur < 6:
            ph = ob["phases"][cur - 1]
            if not all(ph["dels"]):
                return jsonify({"ok": False, "error": "deliverables incomplete"}), 400
            ph["completed"] = date.today().isoformat()
            ob["current"]   = cur + 1
            ob["atrasado"]  = False
            ob["waiting"]   = ESTEIRA_PHASES[cur]["wait"]
        else:
            carteiras = ob.get("carteiras", [])
            if not carteiras or not all(c["status"] == "apta" for c in carteiras):
                return jsonify({"ok": False, "error": "carteiras not all apt"}), 400
            ob["phases"][5]["dels"]      = [True] * len(ob["phases"][5]["dels"])
            ob["phases"][5]["completed"] = date.today().isoformat()
            ob["concluded"] = True

    elif action == "toggle_todo":
        ti = int(data["todo_idx"])
        ob["todos"][ti]["done"] = not ob["todos"][ti]["done"]

    elif action == "add_todo":
        text = data.get("text", "").strip()
        if not text:
            return jsonify({"ok": False}), 400
        ob["todos"].append({
            "text":  text,
            "owner": data.get("owner", "beehus"),
            "due":   data.get("due", "a definir"),
            "done":  False,
        })

    elif action == "add_carteira":
        ob["carteiras"].append({
            "name":   data.get("name", "").strip(),
            "inst":   data.get("inst", "").strip(),
            "cur":    data.get("cur", "BRL"),
            "status": "pendente",
        })

    elif action == "mark_carteira_apt":
        ci = int(data["carteira_idx"])
        ob["carteiras"][ci]["status"] = "apta"

    _save_esteira(all_ob)
    return jsonify({"ok": True, "ob": ob})


@bp.route("/api/saude/onboarding/esteira/<ob_id>", methods=["DELETE"])
def esteira_delete(ob_id):
    all_ob = [x for x in _load_esteira() if x["id"] != ob_id]
    _save_esteira(all_ob)
    return jsonify({"ok": True})
