from pymongo import MongoClient
from datetime import date, timedelta
import json, os, certifi, time
from beehus_api import list_companies, list_entities, partner_wallets

# ── In-process TTL cache ──────────────────────────────────────────────────────
_db_cache: dict = {}

def _c_get(key):
    e = _db_cache.get(key)
    if e and time.monotonic() < e[0]:
        return True, e[1]
    return False, None

def _c_set(key, val, ttl):
    _db_cache[key] = (time.monotonic() + ttl, val)

DB_NAME                = "Beehus"
CONFIG_FILE            = os.path.join(os.path.dirname(__file__), "data", "config.json")
SETTINGS_FILE          = os.path.join(os.path.dirname(__file__), "data", "settings.json")
DEFAULT_BLACKLIST_FILE = os.path.join(os.path.dirname(__file__), "data", "default_blacklist.json")
NAV_SETTINGS_FILE      = os.path.join(os.path.dirname(__file__), "data", "nav_settings.json")
USER_CONNECTIONS_FILE  = os.path.join(os.path.dirname(__file__), "data", "user_connections.json")


# ── Windows user ────────────────────────────────────────────────────────────

def get_windows_user():
    """Returns the current Windows username in lowercase."""
    return os.environ.get("USERNAME", "unknown").lower()


# ── Per-user connection storage ─────────────────────────────────────────────

def load_user_connections():
    if not os.path.exists(USER_CONNECTIONS_FILE):
        return {}
    with open(USER_CONNECTIONS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_user_connections(conns):
    with open(USER_CONNECTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(conns, f, indent=2)


# ── DB proxy ─────────────────────────────────────────────────────────────────
# A single object whose internal reference can be swapped after registration,
# so all existing `from db import db` imports see the live database immediately.

class _DbProxy:
    def __init__(self):
        self._db = None

    def _init(self, mongo_db):
        self._db = mongo_db

    def _ready(self):
        return self._db is not None

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if self._db is None:
            raise RuntimeError("Database not initialized – please register at /setup.")
        return getattr(self._db, name)

    def __getitem__(self, name):
        if self._db is None:
            raise RuntimeError("Database not initialized – please register at /setup.")
        return self._db[name]


db     = _DbProxy()
client = None

# Try to connect for the current Windows user
_user  = get_windows_user()
_conns = load_user_connections()
if _user in _conns:
    client = MongoClient(_conns[_user], tlsCAFile=certifi.where())
    db._init(client[DB_NAME])

# Fallback: connect via MONGO_URI environment variable (Railway/cloud deployments)
if not db._ready():
    _uri_env = os.environ.get("MONGO_URI", "")
    if _uri_env:
        client = MongoClient(_uri_env, tlsCAFile=certifi.where())
        db._init(client[DB_NAME])


def ensure_indexes():
    """Create compound indexes for heavy queries (idempotent)."""
    if not db._ready():
        return
    try:
        db.unprocessedSecurityPositions.create_index(
            [("walletId", 1), ("positionDate", 1)])
        db.processedPosition.create_index(
            [("walletId", 1), ("positionDate", 1)])
        db.navPackages.create_index(
            [("walletId", 1), ("positionDate", 1), ("trashed", 1)])
        db.wallets.create_index(
            [("companyId", 1), ("entityId", 1)])
        db.transactions.create_index(
            [("walletId", 1), ("liquidationDate", 1)])
        db.issues.create_index(
            [("walletId", 1), ("date", 1), ("type", 1), ("status", 1)])
        db.issues.create_index(
            [("status", 1), ("date", 1)])
        db.provisions.create_index(
            [("walletId", 1), ("initialDate", 1), ("liquidationDate", 1)])
        db.cashAccounts.create_index(
            [("walletId", 1)])
    except Exception as exc:
        import logging
        logging.warning("ensure_indexes failed (non-critical): %s", exc)

ensure_indexes()


# ── Helpers ──────────────────────────────────────────────────────────────────

def valid_wallet_ids():
    """Returns the set of walletId strings that exist in the wallets collection."""
    return {w["_id"] for w in catalog_wallets()}


def get_biz_dates(limit, end_date=None):
    """Last `limit` business days (Mon-Fri, excluding holidays) ending on end_date (or today), oldest → newest."""
    key = ("biz_dates", limit, end_date)
    hit, val = _c_get(key)
    if hit:
        return val
    holidays = set(load_settings().get("holidays", []))
    result  = []
    current = date.fromisoformat(end_date) if end_date else date.today()
    while len(result) < limit:
        if current.weekday() < 5 and current.strftime("%Y-%m-%d") not in holidays:
            result.append(current.strftime("%Y-%m-%d"))
        current -= timedelta(days=1)
    result = list(reversed(result))
    _c_set(key, result, 60)
    return result


def get_biz_dates_range(start_date, end_date):
    """All business days (Mon-Fri, excluding holidays) between start_date and end_date inclusive, oldest → newest."""
    holidays = set(load_settings().get("holidays", []))
    start   = date.fromisoformat(start_date)
    end     = date.fromisoformat(end_date)
    result  = []
    current = start
    while current <= end:
        if current.weekday() < 5 and current.strftime("%Y-%m-%d") not in holidays:
            result.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return result


def load_config_full():
    """Read config.json once; return (selected, delays, methods, responsible)."""
    if not os.path.exists(CONFIG_FILE):
        return set(), {}, {}, {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        items = json.load(f)
    selected    = {(i["companyId"], i["entityId"]) for i in items}
    delays      = {(i["companyId"], i["entityId"]): int(i.get("delay", 0))   for i in items}
    methods     = {(i["companyId"], i["entityId"]): i.get("method", "")      for i in items}
    responsible = {(i["companyId"], i["entityId"]): i.get("responsible", "") for i in items}
    return selected, delays, methods, responsible


def load_config():
    selected, _, _, _ = load_config_full()
    return selected


def load_config_delays():
    """Returns {(companyId, entityId): delay_in_biz_days}"""
    _, delays, _, _ = load_config_full()
    return delays


def load_config_methods():
    """Returns {(companyId, entityId): method_string}"""
    _, _, methods, _ = load_config_full()
    return methods


def load_config_responsible():
    """Returns {(companyId, entityId): responsible_string}"""
    _, _, _, responsible = load_config_full()
    return responsible


def _load_default_blacklist():
    try:
        with open(DEFAULT_BLACKLIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

def load_settings():
    hit, val = _c_get("settings")
    if hit:
        return val
    defaults = {"only_daily_position": False, "only_with_consumption": False,
                "wizard_blacklist": _load_default_blacklist(), "company_filter": []}
    if not os.path.exists(SETTINGS_FILE):
        _c_set("settings", defaults, 30)
        return defaults
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("wizard_blacklist", _load_default_blacklist())
    _c_set("settings", data, 30)
    return data


def load_nav_settings():
    defaults = {"only_daily_position": False, "only_with_consumption": False}
    if not os.path.exists(NAV_SETTINGS_FILE):
        return defaults
    with open(NAV_SETTINGS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("only_daily_position", False)
    data.setdefault("only_with_consumption", False)
    return data


def get_company_filter():
    """Returns set of visible company ID strings. Empty set means show all."""
    return set(load_settings().get("company_filter", []))


def wallet_filter_query(settings):
    """Build a MongoDB filter dict based on active settings toggles."""
    q = {}
    if settings.get("only_daily_position"):
        q["hasDailyPosition"] = True
    if settings.get("only_with_consumption"):
        q["consumptionIdentifiers"] = {"$exists": True, "$not": {"$size": 0}}
    return q


# ── Catálogo (companies/entities/wallets) — API-first (CLAUDE.md §8) ────────
# Substitui db.companies.find/db.entities.find/db.wallets.find: companies,
# entities e wallets já têm endpoint homologado (beehus_api.partner) e passam
# a vir 100% da API Beehus, nunca mais do Mongo direto. Cacheados 120s (mesma
# ordem de grandeza do TTL que os apps-irmãos usam pra catálogo — coleções
# pequenas e estáveis, não vale bater na API a cada request de página).
_CATALOG_TTL = 120


def catalog_companies():
    """Contexto:
    Lista de empresas visíveis pelo token atual — substitui `db.companies.find`.
    Usada por toda tela que precisa resolver companyId -> nome. Retorna
    [{"_id","name"}].

    Pseudocódigo:
      1. Cache 120s (chave fixa, sem parâmetro).
      2. beehus_api.list_companies() traz o documento COMPLETO da empresa,
         incluindo credenciais sensíveis (`externalAuths[].clientSecret`/
         `certificate`/`key` de integrações como Itaú) — por isso extraímos
         SÓ `_id`/`name` aqui e descartamos o resto imediatamente. Esses
         campos nunca devem ser repassados a um `jsonify`/template.
    """
    hit, val = _c_get("catalog_companies")
    if hit:
        return val
    result = [{"_id": c.get("_id", ""), "name": c.get("name", "")} for c in list_companies()]
    _c_set("catalog_companies", result, _CATALOG_TTL)
    return result


def catalog_company_names():
    """{companyId: name} — atalho mais usado nas páginas."""
    return {c["_id"]: c["name"] for c in catalog_companies()}


def catalog_entities():
    """Contexto:
    Lista de entidades (instituições/carteiras-mãe) visíveis pelo token atual
    — substitui `db.entities.find`. Retorna [{"_id","name"}].

    Pseudocódigo:
      1. Cache 120s.
      2. beehus_api.list_entities() -> extrai _id/name.
    """
    hit, val = _c_get("catalog_entities")
    if hit:
        return val
    result = [{"_id": e.get("_id", ""), "name": e.get("name", "")} for e in list_entities()]
    _c_set("catalog_entities", result, _CATALOG_TTL)
    return result


def catalog_entity_names():
    """{entityId: name} — atalho mais usado nas páginas."""
    return {e["_id"]: e["name"] for e in catalog_entities()}


def catalog_wallets(company_ids=None):
    """Contexto:
    Carteiras (normalizadas ao shape que as páginas já esperam de
    `db.wallets.find`) — substitui a leitura direta da collection `wallets`.
    `company_ids` restringe o fan-out à API (1 chamada por empresa); default
    é todo o catálogo de empresas visíveis pelo token.

    Pseudocódigo:
      1. Resolve a lista de companyIds alvo (parâmetro ou catalog_companies()).
      2. Cache 120s por conjunto de companyIds.
      3. 1 chamada `beehus_api.partner_wallets(companyId)` por empresa —
         `companyId`/`entityId` voltam POPULADOS (objetos); normaliza pra
         string id, igual ao shape do Mongo que o resto do app consome.
    """
    cids = tuple(sorted(company_ids)) if company_ids is not None else tuple(
        sorted(c["_id"] for c in catalog_companies())
    )
    cache_key = ("catalog_wallets", cids)
    hit, val = _c_get(cache_key)
    if hit:
        return val

    wallets = []
    for cid in cids:
        for w in partner_wallets(cid):
            company = w.get("companyId") or {}
            entity  = w.get("entityId") or {}
            wallets.append({
                "_id":                    w.get("_id", ""),
                "name":                   w.get("name", ""),
                "accountCode":            w.get("accountCode", ""),
                "companyId":              company.get("_id", "") if isinstance(company, dict) else str(company or ""),
                "entityId":               entity.get("_id", "")  if isinstance(entity, dict)  else str(entity or ""),
                "hasDailyPosition":       w.get("hasDailyPosition", False),
                "consumptionIdentifiers": w.get("consumptionIdentifiers", []),
                "trashed":                w.get("trashed", False),
                "startDateConsolidation": w.get("startDateConsolidation"),
            })
    _c_set(cache_key, wallets, _CATALOG_TTL)
    return wallets


def filter_wallets(wallets, *, company_id=None, entity_id=None, wallet_ids=None, settings=None):
    """Contexto:
    Aplica em memória os mesmos filtros que antes iam direto num `find(wq)`
    do Mongo (companyId/entityId/settings de exibição) — usado depois de
    `catalog_wallets()` já ter trazido os documentos via API.

    Pseudocódigo:
      1. Filtra por companyId/entityId/conjunto de walletIds quando informados.
      2. Aplica wallet_filter_query(settings): hasDailyPosition e/ou
         consumptionIdentifiers não-vazio.
    """
    settings = settings or {}
    only_daily = bool(settings.get("only_daily_position"))
    only_cons  = bool(settings.get("only_with_consumption"))
    wid_set    = set(wallet_ids) if wallet_ids is not None else None
    result = []
    for w in wallets:
        if company_id and w["companyId"] != str(company_id):
            continue
        if entity_id and w["entityId"] != str(entity_id):
            continue
        if wid_set is not None and w["_id"] not in wid_set:
            continue
        if only_daily and not w.get("hasDailyPosition"):
            continue
        if only_cons and not w.get("consumptionIdentifiers"):
            continue
        result.append(w)
    return result


# ── Posições/transações/NAV via API — API-first (CLAUDE.md §8) ──────────────
# A API é por-empresa (nunca cross-empresa como o Mongo) e a de posição
# processada só aceita 1 data por chamada — migrar essas telas troca 1 query
# Mongo por N chamadas HTTP (empresas × datas). Aceito explicitamente pelo
# usuário [2026-08-10] com cache agressivo como mitigação (mesma janela curta
# do catálogo, 60s — dado do dia ainda está sendo carregado ao longo do dia).
import concurrent.futures
from beehus_api import (get_unprocessed_security_positions, get_processed_position,
                        get_nav_results, list_transactions, list_securities, list_groupings)

_POSITIONS_TTL      = 60     # data de hoje/futura — ainda sendo carregada ao longo do dia
_POSITIONS_TTL_PAST = 3600   # [2026-08-10] data passada e já assentada não muda mais — cache
                             # bem mais longo. Necessário: telas com janela longa (Banda de
                             # Rentabilidade, até ~1 ano de histórico) medem 75-103s numa
                             # busca "fria" (1 chamada HTTP por data, sem endpoint de faixa
                             # pra processed-position/nav-results) — sem isso, TODA troca de
                             # data/empresa pagaria esse custo de novo a cada 60s.


def _ttl_for_date(date_str):
    """TTL mais longo pra datas estritamente passadas (não mudam mais)."""
    try:
        return _POSITIONS_TTL_PAST if date_str < date.today().isoformat() else _POSITIONS_TTL
    except TypeError:
        return _POSITIONS_TTL


def normalize_id(value):
    """Contexto:
    Vários endpoints da API devolvem `walletId`/`entityId`/`companyId`
    POPULADOS (um objeto `{"_id": ..., "name": ...}`) em vez do id cru que o
    Mongo devolvia — esta função extrai o id como string em ambos os casos,
    usada em toda leitura de posições/transações/NAV via API.

    Pseudocódigo:
      1. dict -> pega "_id".
      2. outro tipo -> str() direto.
    """
    if isinstance(value, dict):
        value = value.get("_id", "")
    return str(value or "")


def _company_ids_or_all(company_ids):
    return tuple(sorted(company_ids)) if company_ids is not None else tuple(sorted(c["_id"] for c in catalog_companies()))


def _fan_out_companies(fetch_one, company_ids, max_workers=10):
    """Contexto:
    Chama `fetch_one(company_id)` para cada empresa em paralelo e concatena as
    listas devolvidas — o padrão comum a toda leitura de posições/transações/
    NAV via API (que só aceita 1 empresa por chamada, ao contrário do Mongo
    que cruzava todas numa query só).

    Pseudocódigo:
      1. Sem empresas -> lista vazia.
      2. ThreadPoolExecutor dispara 1 chamada por empresa (erro em qualquer
         uma propaga — falha parcial é tratada como falha total).
      3. Concatena os resultados na ordem das empresas.
    """
    if not company_ids:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers, len(company_ids))) as ex:
        out = []
        for chunk in ex.map(fetch_one, company_ids):
            out.extend(chunk or [])
        return out


def _normalize_ids(doc):
    """Contexto:
    Vários endpoints devolvem `walletId`/`entityId` POPULADOS (objeto) em
    lugar do id cru — normaliza os dois pra string, no MESMO doc, pra bater
    com o shape que as páginas já esperavam do Mongo (`str(doc["walletId"])`).
    `companyId` normalmente já vem cru nesses endpoints, mas é normalizado
    aqui também por segurança. Muta e retorna o próprio doc.

    Pseudocódigo:
      1. Para cada campo (walletId/entityId/companyId) presente, aplica
         normalize_id().
    """
    for field in ("walletId", "entityId", "companyId"):
        if field in doc:
            doc[field] = normalize_id(doc[field])
    return doc


def _wallet_ids_by_company(company_ids):
    """{companyId: [walletId, ...]} a partir do catálogo — usado para sempre
    passar `wallet_ids` explícito nas chamadas de posição (ver nota abaixo)."""
    result = {}
    for w in catalog_wallets(company_ids=company_ids):
        result.setdefault(w["companyId"], []).append(w["_id"])
    return result


def api_unprocessed_positions(initial_date, final_date, company_ids=None):
    """Contexto:
    unprocessedSecurityPositions de uma janela de datas — substitui
    `db.unprocessedSecurityPositions.find/aggregate`. A API aceita faixa de
    datas nativamente (1 chamada por empresa, não por empresa×data).

    Pseudocódigo:
      1. Cache 60s por (initial_date, final_date, company_ids).
      2. Resolve walletIds de cada empresa via catálogo — o endpoint NÃO
         trata `walletIds` omitido/vazio como "empresa toda": devolve 0
         (confirmado em produção). É preciso passar a lista explícita.
      3. 1 chamada get_unprocessed_security_positions por empresa (fan-out).
    """
    cids = _company_ids_or_all(company_ids)
    key = ("api_unprocessed", initial_date, final_date, cids)
    hit, val = _c_get(key)
    if hit:
        return val
    wids_by_company = _wallet_ids_by_company(cids)

    def _one(cid):
        wids = wids_by_company.get(cid)
        if not wids:
            return []
        docs = get_unprocessed_security_positions(
            company_id=cid, initial_date=initial_date, final_date=final_date, wallet_ids=wids)
        return [_normalize_ids(d) for d in docs]

    result = _fan_out_companies(_one, cids)
    _c_set(key, result, _ttl_for_date(final_date))
    return result


def api_processed_positions(position_date, company_ids=None):
    """Contexto:
    Posições processadas (bloco `position` do envelope) de UMA data —
    substitui `db.processedPosition.find/aggregate`. A API só aceita 1 data
    por chamada (sem faixa, ao contrário do endpoint de unprocessed) — para
    grades de N dias, o chamador faz N chamadas a esta função, uma por dia,
    cada uma já cacheada.

    Pseudocódigo:
      1. Cache 60s por (position_date, company_ids).
      2. Resolve walletIds de cada empresa via catálogo (mesma cautela do
         unprocessed — sempre explícito, nunca confia em "vazio = tudo").
      3. 1 chamada get_processed_position por empresa (fan-out); extrai só o
         bloco `position` de cada envelope (`provisions`/`cashAccounts` não
         são usados por nenhuma tela migrada hoje — descartados aqui).
    """
    cids = _company_ids_or_all(company_ids)
    key = ("api_processed", position_date, cids)
    hit, val = _c_get(key)
    if hit:
        return val
    wids_by_company = _wallet_ids_by_company(cids)

    def _one(cid):
        wids = wids_by_company.get(cid)
        if not wids:
            return []
        envelopes = get_processed_position(company_id=cid, date=position_date, wallet_ids=wids)
        return [_normalize_ids(env["position"]) for env in envelopes if isinstance(env, dict) and env.get("position")]

    result = _fan_out_companies(_one, cids)
    _c_set(key, result, _ttl_for_date(position_date))
    return result


def api_nav_results(position_date, company_ids=None):
    """Contexto:
    Consolidado de NAV por empresa+data (lista de carteiras com nav calculado)
    — substitui `db.navPackages.find/aggregate`. A API já devolve o resultado
    AGREGADO por empresa (`walletsWithNavDetailed` já traz returnNavPerShare/
    returnContribution/returnDifference calculados, sem precisar reagregar).

    Pseudocódigo:
      1. Cache 60s por (position_date, company_ids).
      2. 1 chamada get_nav_results por empresa (fan-out) — este endpoint não
         tem o gotcha do wallet_ids (não é filtrável por carteira).
      3. Concatena `walletsWithNavDetailed` de todas as empresas numa lista só.
    """
    cids = _company_ids_or_all(company_ids)
    key = ("api_nav_results", position_date, cids)
    hit, val = _c_get(key)
    if hit:
        return val

    def _one(cid):
        out = get_nav_results(company_id=cid, position_date=position_date)
        return out.get("walletsWithNavDetailed", []) if isinstance(out, dict) else []

    result = _fan_out_companies(_one, cids)
    _c_set(key, result, _ttl_for_date(position_date))
    return result


def api_transactions(initial_date, final_date, company_ids=None, date_type="liquidation"):
    """Contexto:
    Transações de uma janela de datas — substitui `db.transactions.find/
    aggregate`. A API aceita faixa de datas nativamente (1 chamada por
    empresa); diferente do endpoint de unprocessed, omitir `wallet_ids`
    devolve a empresa toda normalmente (confirmado em produção).

    Pseudocódigo:
      1. Cache 60s por (initial_date, final_date, company_ids, date_type).
      2. 1 chamada list_transactions por empresa (fan-out).
    """
    cids = _company_ids_or_all(company_ids)
    key = ("api_transactions", initial_date, final_date, cids, date_type)
    hit, val = _c_get(key)
    if hit:
        return val
    def _one(cid):
        docs = list_transactions(company_id=cid, initial_date=initial_date, final_date=final_date, date_type=date_type)
        return [_normalize_ids(d) for d in docs]

    result = _fan_out_companies(_one, cids)
    _c_set(key, result, _ttl_for_date(final_date))
    return result


def api_processed_positions_multi(dates, company_ids=None):
    """Contexto:
    Posições processadas de VÁRIAS datas de uma vez (a API só aceita 1 data
    por chamada) — usado por telas que precisam de uma janela de datas para
    uma única empresa, como a Banda de Rentabilidade (histórico de até ~1
    ano). Retorna {date: [position, ...]}.

    Pseudocódigo:
      1. Cache por (dates, company_ids).
      2. 1 chamada `api_processed_positions(d, company_ids)` por data, em
         paralelo — cada uma já é cacheada individualmente 60s, então uma
         data repetida entre chamadas diferentes já sai do cache.
    """
    cids = _company_ids_or_all(company_ids)
    key = ("api_processed_multi", tuple(dates), cids)
    hit, val = _c_get(key)
    if hit:
        return val
    result = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(api_processed_positions, d, cids): d for d in dates}
        for fut in concurrent.futures.as_completed(futs):
            result[futs[fut]] = fut.result()
    _c_set(key, result, _POSITIONS_TTL)
    return result


def api_nav_results_multi(dates, company_ids=None):
    """Contexto:
    Resultado de NAV (lista por carteira) de VÁRIAS datas de uma vez — mesma
    ideia de `api_processed_positions_multi`, para `api_nav_results`. Usado
    por telas com janela longa (Banda de Rentabilidade, até ~1 ano). Retorna
    {date: [walletsWithNavDetailed item, ...]}.

    Pseudocódigo:
      1. Cache por (dates, company_ids).
      2. 1 chamada `api_nav_results(d, company_ids)` por data, em paralelo —
         cada uma já cacheada individualmente 60s.
    """
    cids = _company_ids_or_all(company_ids)
    key = ("api_nav_multi", tuple(dates), cids)
    hit, val = _c_get(key)
    if hit:
        return val
    result = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(api_nav_results, d, cids): d for d in dates}
        for fut in concurrent.futures.as_completed(futs):
            result[futs[fut]] = fut.result()
    _c_set(key, result, _POSITIONS_TTL)
    return result


def catalog_securities():
    """Contexto:
    Catálogo completo de ativos (securities) — substitui `db.securities.find`.
    Coleção pequena e estável (ver beehus_api.securities): cacheada 120s,
    resolução por id fica a cargo do chamador (ver `catalog_securities_by_id`).
    """
    hit, val = _c_get("catalog_securities")
    if hit:
        return val
    result = list_securities()
    _c_set("catalog_securities", result, _CATALOG_TTL)
    return result


def catalog_securities_by_id():
    """{securityId: security_doc} — atalho mais usado nas páginas."""
    return {str(s.get("_id", "")): s for s in catalog_securities()}


def catalog_groupings(company_id):
    """Contexto:
    Agrupamentos de uma empresa — substitui a leitura da collection
    `groupings` (a antiga leitura em `banda_rentabilidades.py` apontava para
    `db.groups`, uma collection SEM nenhum documento — bug pré-existente que
    fazia a aba "Agrupamentos" sempre voltar vazia; corrigido nesta migração
    ao trocar para o endpoint correto). Retorna [{"_id","name","walletIds"}]
    — `walletIds` já normalizado pra lista de strings a partir de
    `wallets[].walletId`.

    Pseudocódigo:
      1. Cache 120s por companyId.
      2. beehus_api.list_groupings(companyId) -> normaliza wallets[] pra
         lista simples de walletId (string).
    """
    hit, val = _c_get(("catalog_groupings", company_id))
    if hit:
        return val
    result = []
    for g in list_groupings(company_id):
        wids = []
        for entry in g.get("wallets", []):
            wid = entry.get("walletId") if isinstance(entry, dict) else entry
            if isinstance(wid, dict):
                wid = wid.get("_id")
            if wid:
                wids.append(str(wid))
        result.append({"_id": g.get("_id", ""), "name": g.get("name", ""), "walletIds": wids})
    _c_set(("catalog_groupings", company_id), result, _CATALOG_TTL)
    return result


# ── Shared helpers (dashboard / nav) ─────────────────────────────────────────

def biz_days_elapsed(date_str):
    """Count business days from date_str up to (and including) today."""
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        return 0
    today = date.today()
    if d >= today:
        return 0
    # Fast formula: count full weeks × 5, then add remaining weekdays
    delta = (today - d).days  # calendar days between d and today
    full_weeks, remainder = divmod(delta, 7)
    count = full_weeks * 5
    # Count remaining days after full weeks (from d's weekday forward)
    wd = d.weekday()  # 0=Mon
    for i in range(1, remainder + 1):
        if (wd + i) % 7 < 5:
            count += 1
    return count


def cell_cls(count, total, expected=True):
    if not expected:
        return "bg-gray-50 text-gray-300"
    if count == total:
        return "bg-green-100 text-green-700"
    if count > 0:
        return "bg-yellow-100 text-yellow-700"
    return "bg-red-100 text-red-600"


def wallet_cls(has_value):
    return "bg-green-50 text-green-700" if has_value else "bg-red-50 text-red-600"


def build_wallet_map(settings=None):
    """Returns (wallet_to_pair, pair_total) filtered by settings."""
    settings = settings or {}
    key      = ("wallet_map", bool(settings.get("only_daily_position")), bool(settings.get("only_with_consumption")))
    hit, val = _c_get(key)
    if hit:
        return val
    wallet_to_pair = {}
    pair_total     = {}
    for w in filter_wallets(catalog_wallets(), settings=settings):
        wid = w["_id"]
        cid = w["companyId"]
        eid = w["entityId"]
        if cid and eid:
            wallet_to_pair[wid] = (cid, eid)
            pair_total[(cid, eid)] = pair_total.get((cid, eid), 0) + 1
    result = (wallet_to_pair, pair_total)
    _c_set(key, result, 300)
    return result
