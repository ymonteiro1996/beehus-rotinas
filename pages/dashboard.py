from flask import Blueprint, render_template, jsonify, request
from db import (db, get_biz_dates, get_biz_dates_range, load_config_full, load_settings,
                get_company_filter,
                biz_days_elapsed as _biz_days_elapsed,
                cell_cls as _cell_cls, wallet_cls as _wallet_cls,
                build_wallet_map as _build_wallet_map,
                catalog_companies, catalog_company_names, catalog_entity_names,
                catalog_wallets, filter_wallets,
                api_unprocessed_positions, api_processed_positions,
                api_processed_positions_multi, api_nav_results, api_nav_results_multi,
                api_transactions, normalize_id)
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


# ── Helpers de agregação (API) ────────────────────────────────────────────────
# Substituem os antigos aggregates Mongo ($group por walletId/positionDate) —
# agora recebem listas/dicts já buscados via db.py (API Beehus, CLAUDE.md §8).

def _count_by_pair_date_flat(docs, dates, wallet_to_pair, pairs):
    """Contexto:
    Conta documentos de posição por (par empresa/entidade, data) a partir de uma
    lista PLANA já buscada via API (ex.: api_unprocessed_positions, que devolve
    tudo de uma janela de datas numa lista só) — substitui o antigo aggregate
    Mongo `_count_collection` (agrupava por walletId/positionDate e depois somava
    por par). Usada em /api/rows.

    Pseudocódigo:
      1. Filtra os documentos para as datas de interesse.
      2. Resolve o par (companyId, entityId) da carteira do documento.
      3. Soma 1 por documento em counts[(par, data)] quando o par está em `pairs`.
    """
    dates_set = set(dates)
    counts = {}
    for doc in docs:
        d = str(doc.get("positionDate", ""))[:10]
        if d not in dates_set:
            continue
        wid  = doc.get("walletId", "")
        pair = wallet_to_pair.get(wid)
        if pair and pair in pairs:
            counts[(pair, d)] = counts.get((pair, d), 0) + 1
    return counts


def _count_by_pair_date_multi(docs_by_date, wallet_to_pair, pairs):
    """Contexto:
    Mesma contagem de `_count_by_pair_date_flat`, mas a partir de um dict
    {date: [docs]} (retorno de api_processed_positions_multi, que só aceita 1
    data por chamada e já devolve os docs agrupados por data). Usada em
    /api/processed/rows.

    Pseudocódigo:
      1. Para cada data e cada documento dessa data, resolve o par da carteira.
      2. Soma 1 por documento em counts[(par, data)] quando o par está em `pairs`.
    """
    counts = {}
    for d, docs in docs_by_date.items():
        for doc in docs:
            wid  = normalize_id(doc.get("walletId", ""))
            pair = wallet_to_pair.get(wid)
            if pair and pair in pairs:
                counts[(pair, d)] = counts.get((pair, d), 0) + 1
    return counts


def _wallet_counts_by_date_flat(docs, dates):
    """Contexto:
    Conta documentos de posição por (walletId, data) a partir de uma lista PLANA
    já buscada via API — substitui os aggregates Mongo $group por (w,d) com
    $sum usados nas grades por carteira (Cargas). Usada em /api/detail-grid,
    /api/wallet/rows (modo cargas) e /api/wallet/template-rows (modo cargas).

    Pseudocódigo:
      1. Filtra os documentos para as datas de interesse.
      2. Soma 1 por documento em counts[(walletId, data)].
    """
    dates_set = set(dates)
    counts = {}
    for doc in docs:
        d = str(doc.get("positionDate", ""))[:10]
        if d not in dates_set:
            continue
        wid = doc.get("walletId", "")
        counts[(wid, d)] = counts.get((wid, d), 0) + 1
    return counts


def _wallets_present_by_date_multi(docs_by_date):
    """Contexto:
    A partir de um dict {date: [docs]} (retorno de api_processed_positions_multi
    ou api_nav_results_multi), monta {date: set(walletId)} — quais carteiras têm
    dado em cada data. Substitui os aggregates Mongo $group por (w,d) sem $sum
    (checagem de existência, ex.: processedPosition/navPackages). Usada em
    /api/processed/detail-grid, /api/wallet/rows (modo processed/nav) e
    /api/wallet/template-rows (modo processed/nav).

    Pseudocódigo:
      1. Para cada data, extrai o walletId normalizado de cada documento.
    """
    return {d: {normalize_id(doc.get("walletId", "")) for doc in docs} for d, docs in docs_by_date.items()}


def _resolve_companies_for_wallets(wid_list):
    """Contexto:
    Resolve a quais companyIds pertence uma lista de walletIds vinda de um
    template salvo (que pode misturar carteiras de empresas diferentes) — usada
    pra restringir as chamadas de posição/transação/NAV via API a essas
    empresas em vez de buscar em todas. Retorna (company_ids, wallet_by_id).

    Pseudocódigo:
      1. Busca o catálogo completo de carteiras (todas as empresas visíveis).
      2. Filtra pro subconjunto de walletIds do template.
      3. Extrai a lista de companyIds distintos e o dict walletId -> doc.
    """
    wid_set = set(wid_list)
    wallet_by_id = {w["_id"]: w for w in catalog_wallets() if w["_id"] in wid_set}
    company_ids = sorted({w["companyId"] for w in wallet_by_id.values() if w.get("companyId")})
    return company_ids, wallet_by_id


# ── Routes ─────────────────────────────────────────────────────────────────────

@bp.route("/")
def index():
    return render_template("dashboard.html")


@bp.route("/api/rows")
def get_rows():
    """Contexto:
    Grade principal do Dashboard (Cargas) — uma linha por par empresa/entidade,
    uma coluna por data, com a contagem de carteiras que já subiram posição não
    processada (unprocessedSecurityPositions) via API Beehus. Chamada pela tela
    inicial do Dashboard.

    Pseudocódigo:
      1. Resolve as últimas `limit` datas úteis e os nomes de empresas/entidades.
      2. Monta o mapa carteira -> par (companyId, entityId) e o total de carteiras
         por par, já filtrado pelos toggles de settings.
      3. Restringe os pares aos selecionados em config.json e ao filtro de empresa.
      4. Busca as posições não processadas de todas as empresas na janela de
         datas e conta quantas há por (par, data).
      5. Monta as linhas com os rótulos "count/total" e a classe de cor da célula.
    """
    limit         = int(request.args.get("limit", 10))
    dates         = get_biz_dates(limit)
    company_names = catalog_company_names()
    entity_names  = catalog_entity_names()

    wallet_to_pair, pair_total = _build_wallet_map(load_settings())

    pairs = set(wallet_to_pair.values())
    selected, delays, methods, responsible = load_config_full()
    if selected:
        pairs = pairs & selected
    cf = get_company_filter()
    if cf:
        pairs = {p for p in pairs if p[0] in cf}

    elapsed = {d: _biz_days_elapsed(d) for d in dates}
    docs    = api_unprocessed_positions(dates[0], dates[-1], company_ids=None) if dates else []
    counts  = _count_by_pair_date_flat(docs, dates, wallet_to_pair, pairs)

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
    """Contexto:
    Drill-down do Dashboard (Cargas): lista as carteiras de um par empresa/
    entidade numa data específica, com a contagem de posições não processadas
    de cada uma. Chamada ao clicar numa célula da grade principal.

    Pseudocódigo:
      1. Filtra as carteiras do par via catálogo (API) + settings.
      2. Busca as posições não processadas da empresa na data pedida.
      3. Conta quantas posições cada carteira tem e monta o detalhe ordenado por nome.
    """
    cid = request.args.get("companyId")
    eid = request.args.get("entityId")
    d   = request.args.get("date")

    wallets_list = filter_wallets(catalog_wallets(company_ids=[cid]), company_id=cid, entity_id=eid,
                                   settings=load_settings()) if cid else []
    wallets = {w["_id"]: {"name": w.get("name") or w["_id"], "accountCode": w.get("accountCode", "")}
               for w in wallets_list}

    counts = {wid: 0 for wid in wallets}
    docs = api_unprocessed_positions(d, d, company_ids=[cid]) if (cid and d) else []
    for pos in docs:
        wid = pos.get("walletId", "")
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
    """Contexto:
    Grade de datas x carteiras do Dashboard (Cargas) pra um par empresa/entidade
    — contagem de posições não processadas de cada carteira em cada data.

    Pseudocódigo:
      1. Filtra as carteiras do par via catálogo (API) + settings.
      2. Busca as posições não processadas da empresa na janela de datas.
      3. Conta por (carteira, data) e monta a grade ordenada por nome.
    """
    cid   = request.args.get("companyId")
    eid   = request.args.get("entityId")
    limit = int(request.args.get("limit", 10))
    dates = get_biz_dates(limit)

    wallets_list = filter_wallets(catalog_wallets(company_ids=[cid]), company_id=cid, entity_id=eid,
                                   settings=load_settings()) if cid else []
    wallets = {w["_id"]: {"name": w.get("name") or w["_id"], "accountCode": w.get("accountCode", "")}
               for w in wallets_list}

    docs   = api_unprocessed_positions(dates[0], dates[-1], company_ids=[cid]) if (cid and dates) else []
    counts = _wallet_counts_by_date_flat(docs, dates)

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
    """Contexto:
    Grade principal do Dashboard (Processado) — mesma ideia de `get_rows`, mas
    contando posições processadas (processedPosition) via API Beehus.

    Pseudocódigo:
      1. Resolve as últimas `limit` datas úteis e os nomes de empresas/entidades.
      2. Monta o mapa carteira -> par e o total de carteiras por par.
      3. Restringe os pares aos selecionados em config.json e ao filtro de empresa.
      4. Busca as posições processadas de todas as empresas, uma chamada por
         data (a API não aceita faixa pra posição processada), e conta por
         (par, data).
      5. Monta as linhas com os rótulos "count/total" e a classe de cor da célula.
    """
    limit         = int(request.args.get("limit", 10))
    dates         = get_biz_dates(limit)
    company_names = catalog_company_names()
    entity_names  = catalog_entity_names()

    wallet_to_pair, pair_total = _build_wallet_map(load_settings())

    pairs = set(wallet_to_pair.values())
    selected, delays, methods, responsible = load_config_full()
    if selected:
        pairs = pairs & selected
    cf = get_company_filter()
    if cf:
        pairs = {p for p in pairs if p[0] in cf}

    elapsed      = {d: _biz_days_elapsed(d) for d in dates}
    docs_by_date = api_processed_positions_multi(dates, company_ids=None) if dates else {}
    counts       = _count_by_pair_date_multi(docs_by_date, wallet_to_pair, pairs)

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
    """Contexto:
    Drill-down do Dashboard (Processado): lista as carteiras de um par empresa/
    entidade numa data específica, indicando se cada uma tem posição processada.

    Pseudocódigo:
      1. Filtra as carteiras do par via catálogo (API) + settings.
      2. Busca as posições processadas da empresa na data pedida.
      3. Marca quais carteiras aparecem no resultado e monta o detalhe.
    """
    cid = request.args.get("companyId")
    eid = request.args.get("entityId")
    d   = request.args.get("date")

    wallets_list = filter_wallets(catalog_wallets(company_ids=[cid]), company_id=cid, entity_id=eid,
                                   settings=load_settings()) if cid else []
    wallets = {w["_id"]: {"name": w.get("name") or w["_id"], "accountCode": w.get("accountCode", "")}
               for w in wallets_list}

    docs = api_processed_positions(d, company_ids=[cid]) if (cid and d) else []
    wids_with_pp = {doc.get("walletId", "") for doc in docs if doc.get("walletId", "") in wallets}

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
    """Contexto:
    Grade de datas x carteiras do Dashboard (Processado) pra um par empresa/
    entidade — existência de posição processada de cada carteira em cada data.

    Pseudocódigo:
      1. Filtra as carteiras do par via catálogo (API) + settings.
      2. Busca as posições processadas da empresa, uma chamada por data.
      3. Marca presença por (carteira, data) e monta a grade ordenada por nome.
    """
    cid   = request.args.get("companyId")
    eid   = request.args.get("entityId")
    limit = int(request.args.get("limit", 10))
    dates = get_biz_dates(limit)

    wallets_list = filter_wallets(catalog_wallets(company_ids=[cid]), company_id=cid, entity_id=eid,
                                   settings=load_settings()) if cid else []
    wallets = {w["_id"]: {"name": w.get("name") or w["_id"], "accountCode": w.get("accountCode", "")}
               for w in wallets_list}

    docs_by_date = api_processed_positions_multi(dates, company_ids=[cid]) if (cid and dates) else {}
    wids_by_date = _wallets_present_by_date_multi(docs_by_date)

    rows = sorted([
        {
            "walletId":    wid,
            "name":        wallets[wid]["name"],
            "accountCode": wallets[wid]["accountCode"],
            "cells": [
                {"label": "✓" if wid in wids_by_date.get(d, set()) else "—",
                 "cls":   _wallet_cls(1 if wid in wids_by_date.get(d, set()) else 0)}
                for d in dates
            ],
        }
        for wid in wallets
    ], key=lambda x: x["name"])

    return jsonify({"rows": rows, "dates": dates})


# ── Wallet-level view ─────────────────────────────────────────────────────────

@bp.route("/api/wallet/list")
def wallet_list():
    """Contexto:
    Lightweight: retorna as carteiras de uma empresa (id, nome, accountCode),
    opcionalmente restritas a uma entidade. Usada pra popular o seletor de
    carteiras da tela de Carteiras.

    Pseudocódigo:
      1. Sem companyId, devolve lista vazia.
      2. Filtra as carteiras da empresa (+ entidade, se informada) via catálogo.
    """
    company_id = request.args.get("companyId", "").strip()
    entity_id  = request.args.get("entityId", "").strip()
    if not company_id:
        return jsonify([])
    wallets_list = filter_wallets(catalog_wallets(company_ids=[company_id]), company_id=company_id,
                                   entity_id=entity_id, settings=load_settings())
    wallets = sorted([
        {"walletId": w["_id"], "name": w.get("name") or w["_id"], "accountCode": w.get("accountCode", "")}
        for w in wallets_list
    ], key=lambda w: w["name"])
    return jsonify(wallets)


@bp.route("/api/wallet/companies")
def wallet_companies():
    """Contexto:
    Lista as empresas visíveis (respeitando o filtro de empresa das settings)
    pra popular o seletor de empresa da tela de Carteiras.

    Pseudocódigo:
      1. Busca o catálogo de empresas via API e ordena por nome.
      2. Aplica o filtro de empresa configurado, se houver.
    """
    companies = sorted(
        [{"id": c["_id"], "name": c.get("name", "")} for c in catalog_companies()],
        key=lambda c: c["name"],
    )
    cf = get_company_filter()
    if cf:
        companies = [c for c in companies if c["id"] in cf]
    return jsonify(companies)


@bp.route("/api/wallet/entities")
def wallet_entities():
    """Contexto:
    Lista as entidades distintas presentes nas carteiras de uma empresa —
    alimenta o segundo filtro (Entidade) da tela de Carteiras. Antes lia
    `db.wallets.distinct` + `db.entities.find` com `$in` de ObjectId; agora
    resolve tudo a partir do catálogo (API), já com ids em string.

    Pseudocódigo:
      1. Filtra as carteiras da empresa via catálogo + settings.
      2. Coleta os entityId distintos presentes nessas carteiras.
      3. Resolve os nomes via catalog_entity_names() e ordena por nome.
    """
    company_id = request.args.get("companyId", "").strip()
    if not company_id:
        return jsonify([])
    wallets_list = filter_wallets(catalog_wallets(company_ids=[company_id]), company_id=company_id,
                                   settings=load_settings())
    entity_ids   = {w["entityId"] for w in wallets_list if w.get("entityId")}
    entity_names = catalog_entity_names()
    entities = sorted(
        [{"id": eid, "name": entity_names.get(eid, "")} for eid in entity_ids],
        key=lambda e: e["name"],
    )
    return jsonify(entities)


@bp.route("/api/wallet/rows")
def get_wallet_rows():
    """Contexto:
    Grade de datas x carteiras da tela de Carteiras (Cargas, Processado ou NAV),
    pra uma empresa (opcionalmente restrita a uma entidade). Chamada ao trocar
    empresa/entidade/modo/período na tela.

    Pseudocódigo:
      1. Resolve as datas (janela explícita ou últimas `limit` datas úteis).
      2. Filtra as carteiras da empresa (+ entidade) via catálogo + settings.
      3. Modo "processed"/"nav": busca via API por data (fan-out por dia) e
         marca presença por (carteira, data).
      4. Modo "cargas": busca as posições não processadas na janela e conta
         por (carteira, data).
      5. Monta a grade com entidade/instituição e as células formatadas.
    """
    company_id = request.args.get("companyId", "").strip()
    entity_id  = request.args.get("entityId", "").strip()
    limit      = int(request.args.get("limit", 10))
    mode       = request.args.get("mode", "cargas").strip()
    start_date = request.args.get("startDate", "").strip()
    end_date   = request.args.get("endDate", "").strip()

    if not company_id:
        return jsonify({"rows": [], "dates": []})

    dates = get_biz_dates_range(start_date, end_date) if (start_date and end_date) else get_biz_dates(limit)

    wallets_list = filter_wallets(catalog_wallets(company_ids=[company_id]), company_id=company_id,
                                   entity_id=entity_id, settings=load_settings())
    wallets = {
        w["_id"]: {
            "name":        w.get("name") or w["_id"],
            "accountCode": w.get("accountCode", ""),
            "entityId":    w.get("entityId", ""),
        }
        for w in wallets_list
    }

    if not wallets:
        return jsonify({"rows": [], "dates": dates})

    entity_names = catalog_entity_names()

    if mode in ("processed", "nav"):
        if mode == "processed":
            docs_by_date = api_processed_positions_multi(dates, company_ids=[company_id]) if dates else {}
        else:
            docs_by_date = api_nav_results_multi(dates, company_ids=[company_id]) if dates else {}
        wids_by_date = _wallets_present_by_date_multi(docs_by_date)

        rows = sorted([{
            "walletId": wid, "name": wallets[wid]["name"],
            "accountCode": wallets[wid]["accountCode"],
            "entity": entity_names.get(wallets[wid]["entityId"], ""),
            "institution": entity_names.get(wallets[wid]["entityId"], ""),
            "cells": [{"label": "✓" if wid in wids_by_date.get(d, set()) else "—",
                        "cls": _wallet_cls(1 if wid in wids_by_date.get(d, set()) else 0)} for d in dates],
        } for wid in wallets], key=lambda x: x["name"])
    else:
        docs   = api_unprocessed_positions(dates[0], dates[-1], company_ids=[company_id]) if dates else []
        counts = _wallet_counts_by_date_flat(docs, dates)

        rows = sorted([{
            "walletId": wid, "name": wallets[wid]["name"],
            "accountCode": wallets[wid]["accountCode"],
            "entity": entity_names.get(wallets[wid]["entityId"], ""),
            "institution": entity_names.get(wallets[wid]["entityId"], ""),
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
    """Contexto:
    Carrega um template salvo (que pode misturar carteiras de várias empresas)
    e devolve a grade carteira x data (Cargas/Processado/NAV) pra essas
    carteiras. Chamada ao selecionar um template salvo na tela de Carteiras.

    Pseudocódigo:
      1. Localiza o template e monta a lista/mapa de walletIds salvos.
      2. Resolve a(s) empresa(s) dessas carteiras via catálogo completo (API).
      3. Busca posições/NAV via API restrito a essas empresas.
      4. Monta a grade aplicando o delay configurado por carteira no template.
    """
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

        company_ids, wallet_by_id = _resolve_companies_for_wallets(wid_list)
        _wallet_entity = {wid: wallet_by_id[wid].get("entityId", "") for wid in wid_list if wid in wallet_by_id}
        _ent_inst = catalog_entity_names()

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
            if not company_ids or not dates:
                docs_by_date = {}
            elif mode == "processed":
                docs_by_date = api_processed_positions_multi(dates, company_ids=company_ids)
            else:
                docs_by_date = api_nav_results_multi(dates, company_ids=company_ids)
            wids_by_date = _wallets_present_by_date_multi(docs_by_date)
            for d in dates:
                wids_by_date.setdefault(d, set())

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
            docs = api_unprocessed_positions(dates[0], dates[-1], company_ids=company_ids) if (company_ids and dates) else []
            counts = _wallet_counts_by_date_flat(docs, dates)

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
    """Contexto:
    Devolve o detalhe diário de pendências (issues, transações não
    identificadas, posição processada, NAV) das carteiras de um template numa
    data específica. Chamada ao abrir o drill-down diário de um template.

    Pseudocódigo:
      1. Localiza o template e a lista de walletIds salvos.
      2. Resolve a(s) empresa(s) dessas carteiras via catálogo completo (API).
      3. Conta issues pendentes por carteira/tipo (fallback Mongo, sem endpoint).
      4. Conta transações não identificadas via API na data.
      5. Verifica existência de posição processada e de NAV via API na data,
         calculando a divergência rentabilidade NAV x Contribuição.
      6. Monta as linhas com todos os indicadores por carteira.
    """
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

    wid_set = set(wid_list)
    company_ids, _ = _resolve_companies_for_wallets(wid_list)

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
    # Gap confirmado [2026-08-10]: sem endpoint de issues na API — fallback Mongo (CLAUDE.md §8).
    for doc in db.issues.aggregate([
        {"$match": {"walletId": {"$in": wid_list}, "date": date_str, "type": {"$in": issue_types}, "status": "pending"}},
        {"$group": {"_id": {"w": "$walletId", "t": "$type"}, "n": {"$sum": 1}}},
    ]):
        key = (str(doc["_id"]["w"]), doc["_id"]["t"])
        if key in counts:
            counts[key] = doc["n"]

    # Unidentified transactions (beehusTransactionType is null) per wallet
    unidentified = {wid: 0 for wid in wid_list}
    if company_ids:
        for doc in api_transactions(date_str, date_str, company_ids=company_ids, date_type="liquidation"):
            wid = doc.get("walletId", "")
            if wid in wid_set and doc.get("beehusTransactionType") is None:
                unidentified[wid] += 1

    # Processed position existence per wallet
    pp_wids = set()
    if company_ids:
        for doc in api_processed_positions(date_str, company_ids=company_ids):
            wid = doc.get("walletId", "")
            if wid in wid_set:
                pp_wids.add(wid)

    # NAV data per wallet (existence + returnNavPerShare / returnContribution)
    nav_wids = set()
    nav_dif  = {}  # walletId -> difRent value (or None)
    if company_ids:
        for doc in api_nav_results(date_str, company_ids=company_ids):
            wid = normalize_id(doc.get("walletId", ""))
            if wid not in wid_set:
                continue
            nav_wids.add(wid)
            nav_dif[wid] = (doc.get("returnNavPerShare") or 0) - (doc.get("returnContribution") or 0)

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
