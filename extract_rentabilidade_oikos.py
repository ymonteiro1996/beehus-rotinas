"""
Extrai rendimento bruto do Comdinheiro e totalContribution do MongoDB para
carteiras Oikos, compara os valores e gera um Excel.

Intervalos:
  31/12/2025 > 30/01/2026
  30/01/2026 > 27/02/2026
  27/02/2026 > 31/03/2026
  31/03/2026 > 30/04/2026

Uso:
  python extract_rentabilidade_oikos.py
"""

import os
import sys
import json
import re
import time
import requests
import certifi
import pandas as pd
from pymongo import MongoClient

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR              = os.path.dirname(os.path.abspath(__file__))
USER_CONNECTIONS_FILE = os.path.join(BASE_DIR, "data", "user_connections.json")
DB_NAME               = "Beehus"

# ── Carteiras Oikos (companyId, cliente, walletId, codigoComdinheiro) ─────────
CARTEIRAS_RAW = [
    ("23313334000110", "BUSINESS ADM PIC UBS ZUR USD",    "680a9cdd3b2296d861270d1e", "32203321"),
    ("23313334000110", "VER PF MS USD",                   "680a9cdd3b2296d861270d25", "32205121"),
    ("23313334000110", "LAP VER ADVISORY PF MS USD",      "68fff59647170dafb2f8fc59", "32205122"),
    ("23313334000110", "BUSINESS ADM PIC MS USD",         "680a9cdd3b2296d861270d2c", "32205321"),
    ("23313334000110", "BUSINESS ADM PIC MS USD II",      "680aa45c72f927b92d97bdee", "32205322"),
    ("23313334000110", "BUSINESS ADM PIC MS USD III",     "680aa45c72f927b92d97bdf5", "32205323"),
    ("23313334000110", "BUSINESS ADM PIC MS USD IV",      "680aa45c72f927b92d97bdfc", "32205324"),
    ("23313334000110", "LAP VER BUSINESS ADM OTHERS II",  "68fff5c147170dafb2f8fcd5", "32207022"),
    ("23313334000110", "VLP ADVISORY PIC USD",            "69a86ca91b6fd599c1d12959", "32205325"),
    ("23313334000110", "VER PF UBS BRL",                  "680a9ce43b2296d861271223", "32101111"),
    ("23313334000110", "VER PF BTG BRL",                  "680a9ce83b2296d86127153f", "32108111"),
    ("23313334000110", "JER PF UBS USD",                  "680a9ce13b2296d861270fac", "A6203321"),
    ("23313334000110", "LAP JER BUSINESS ADM OTHERS II",  "68fff6a247170dafb2f8ff23", "A6207022"),
    ("23313334000110", "LAP JELP ADVISORY PIC USD",       "69dfd7319b6f5b0dad142959", "A6205321"),
    ("23313334000110", "JER PF UBS BRL",                  "680a9ce43b2296d861271232", "A6101111"),
    ("23313334000110", "JER PF UBS BRL II",               "680a9cea3b2296d8612716d4", "A6101112"),
    ("23313334000110", "JER PF UBS BRL III",              "680a9cea3b2296d8612716db", "A6101113"),
    ("23313334000110", "JER PF BTG BRL",                  "68643daa3111a24e3f7ef8d8", "A6108111"),
    ("23313334000110", "LAP JER II PF BTG BRL",           "68fff67e47170dafb2f8fead", "A6108112"),
    ("23313334000110", "JUL PF UBS USD",                  "680a9ce13b2296d861270fb3", "A7203321"),
    ("23313334000110", "JUL PF MS USD",                   "680a9cdd3b2296d861270d36", "A7205121"),
    ("23313334000110", "LAP JUL ADVISORY PF MS USD",      "68fff70147170dafb2f9000f", "A7205122"),
    ("23313334000110", "LAP JUL BUSINESS ADM OTHERS II",  "68fff72e47170dafb2f9008b", "A7207022"),
    ("23313334000110", "LAP JUL PF ADVISORY USD",         "69e675ba86e64d52561ac5d2", "A7205122"),
    ("23313334000110", "JUL PF UBS BRL",                  "680a9ce43b2296d861271239", "A7101111"),
    ("23313334000110", "JUL PF UBS BRL II",               "680a9cea3b2296d8612716e9", "A7101112"),
    ("23313334000110", "JUL PF UBS BRL III",              "680a9cea3b2296d8612716f0", "A7101113"),
    ("23313334000110", "LAP JUL PF ITAU BRL",             "68fff6c847170dafb2f8ff99", "A7102111"),
    ("23313334000110", "JUL PF BTG BRL",                  "68643c303111a24e3f7ef856", "A7108111"),
    ("23313334000110", "JUL PF BTG BRL II",               "68b61086447fab2ccbd173f1", "A7108113"),
    ("23313334000110", "LAP - JUL XP PF",                 "686438353111a24e3f7ef7a8", "A7122111"),
    ("23313334000110", "SOP PF UBS USD",                  "680a9ce13b2296d861270fba", "A8203321"),
    ("23313334000110", "LAP SOP BUSINESS ADM OTHERS II",  "68fff7c647170dafb2f922a6", "A8207022"),
    ("23313334000110", "SOP PF UBS BRL",                  "680a9ce43b2296d861271240", "A8101111"),
    ("23313334000110", "SOP PF UBS BRL II",               "680a9ceb3b2296d8612716fe", "A8101112"),
    ("23313334000110", "SOP PF UBS BRL III",              "680a9ceb3b2296d861271705", "A8101113"),
    ("23313334000110", "SQP PF BTG BRL",                  "68643e0f3111a24e3f7ef917", "A8108111"),
    ("23313334000110", "LAP SOP II PF BTG BRL",           "68fff7a047170dafb2f9222c", "A8108112"),
    ("23313334000110", "AMN PF UBS USD",                  "680a9ce13b2296d861270fc1", "A9203321"),
    ("23313334000110", "LAP AMN BUSINESS ADM OTHERS II",  "68fff62447170dafb2f8fdc1", "A9207022"),
    ("23313334000110", "LAP AMN PF AVENUE",               "68fff65647170dafb2f8fe37", "A9245121"),
    ("23313334000110", "LAP AMN MORGAN PIC USD",          "69dfd62c9b6f5b0dad1427d9", "A9205322"),
    ("23313334000110", "ALP ADVISORY",                    "69dfd6da9b6f5b0dad1428d5", "A9205321"),
    ("23313334000110", "AMN PF UBS BRL",                  "680a9ce53b2296d86127124e", "A9101111"),
    ("23313334000110", "AMN PF UBS BRL II",               "680a9ceb3b2296d861271713", "A9101112"),
    ("23313334000110", "AMN PF UBS BRL III",              "680a9ceb3b2296d86127171a", "A9101113"),
    ("23313334000110", "AMN PF BTG BRL",                  "680a9ce93b2296d8612715c9", "A9108111"),
    ("23313334000110", "LAP AMN PF BTG BRL II",           "68fff5ed47170dafb2f8fd4b", "A9108112"),
]

_seen = set()
CARTEIRAS = []
for _row in CARTEIRAS_RAW:
    _key = (_row[2], _row[3])
    if _key not in _seen:
        _seen.add(_key)
        CARTEIRAS.append(_row)

# ── Date intervals ────────────────────────────────────────────────────────────
# (label, data_ini DDMMYYYY, data_fim DDMMYYYY, positionDate YYYY-MM-DD)
INTERVALS = [
    ("31/12/2025 > 30/01/2026", "31122025", "30012026", "2026-01-30"),
    ("30/01/2026 > 27/02/2026", "30012026", "27022026", "2026-02-27"),
    ("27/02/2026 > 31/03/2026", "27022026", "31032026", "2026-03-31"),
    ("31/03/2026 > 30/04/2026", "31032026", "30042026", "2026-04-30"),
]

# ── Comdinheiro ───────────────────────────────────────────────────────────────
CD_URL = "https://api.comdinheiro.com.br/v1/ep1/import-data"

_CD_TMPL = (
    "username=adminconcepta+ymonteiro.concepta"
    "&password=123Mudar%40"
    "&URL=PosicaoConsolidada001.php%3F"
    "%26nome_portfolio%3D{codigo}"
    "%26data_ini%3D{data_ini}"
    "%26data_fim%3D{data_fim}"
    "%26classe%3DTIPO"
    "%26subclasse%3D"
    "%26layout%3D4"
    "%26exibir_day_trade_data_ini%3D0"
    "%26exibicao%3Ddefault"
    "%26num_casas%3D2"
    "%26ord_classe%3Dalfc"
    "%26ord_ativo%3Dpad"
    "%26opcao_tabela%3Dsubcart_atv"
    "%26fall_ordem%3D1"
    "%26fall_ordem_grand%3D1000"
    "%26fall_cor%3Dd3d3d3t"
    "%26mostra_tabela_grafico%3Dtabela_grafico"
    "%26posicao_tabela_grafico%3Dacima_tabela"
    "%26valores%3D1"
    "%26estilo_pdf%3Dpb0001"
    "%26numeracao_pdf%3D2"
    "%26pos%3Dativo%2BSB_ini%2Baportes%2Bresgates%2Beventos"
    "%2Bsaldo_bruto%2Brendimento%2Btri_pagos%2Bprovisao_IR_IOF"
    "%2Bsaldo_liquido%2Bpercent_SL"
    "&format=json3"
)
_CD_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}

_SKIP_NAMES = {
    "Total Disponível", "Total da Carteira", "Total",
    "Ações/ETFs", "Caixa", "Fundos Offshore", "Genérico",
    "Renda Fixa", "Renda Variável", "Multimercado",
    "Previdência", "Imóveis", "Outros",
}


def _normalize(name):
    """Uppercase + collapse whitespace for fuzzy matching."""
    return re.sub(r"\s+", " ", str(name).upper().strip())


def _to_float(val):
    try:
        return float(str(val).replace(",", ".").replace(" ", ""))
    except (ValueError, TypeError):
        return None


# ── Comdinheiro query + parse ─────────────────────────────────────────────────

def query_comdinheiro(codigo, data_ini, data_fim):
    payload = _CD_TMPL.format(codigo=codigo, data_ini=data_ini, data_fim=data_fim)
    resp = requests.post(CD_URL, data=payload, headers=_CD_HEADERS, timeout=60)
    resp.raise_for_status()
    return resp.json()


def parse_assets(data):
    """
    Returns (list of {ativo, rendimento_bruto}, error_str).

    json3 format:
      tables = {
        "tab0": {
          "lin0": {"col0": "Ativo", ..., "colN": "Rendimento Bruto"},
          "lin1": {...},  ...
        }
      }
    """
    results = []
    warnings = data.get("warnings", {})
    err = " | ".join(str(v) for v in warnings.values()) if warnings else ""
    tables = data.get("tables")

    if not tables:
        return results, err

    table_iter = tables.values() if isinstance(tables, dict) else tables

    for table in table_iter:
        if not isinstance(table, dict):
            continue

        header_row = table.get("lin0", {})
        if not header_row:
            continue

        col_map      = {v: k for k, v in header_row.items()}
        ativo_col    = col_map.get("Ativo")
        rend_col     = col_map.get("Rendimento Bruto")
        desc_col     = col_map.get("Descrição") or col_map.get("Descricao")

        if not ativo_col:
            continue

        for row_key, row in table.items():
            if row_key == "lin0" or not isinstance(row, dict):
                continue

            ativo = str(row.get(ativo_col, "")).strip()
            if not ativo or ativo in _SKIP_NAMES or ativo.startswith("Total"):
                continue
            if desc_col and str(row.get(desc_col, "")).strip() == "Caixa Bloqueado":
                continue

            rendimento = _to_float(row.get(rend_col)) if rend_col else None
            results.append({"ativo": ativo, "rendimento_bruto": rendimento})

    return results, err


# ── MongoDB ───────────────────────────────────────────────────────────────────

def get_mongo_db():
    username = os.environ.get("USERNAME", "unknown").lower()
    with open(USER_CONNECTIONS_FILE, "r", encoding="utf-8") as fh:
        conns = json.load(fh)
    if username not in conns:
        raise RuntimeError(f"MongoDB não configurado para '{username}'. Configure em /setup.")
    client = MongoClient(conns[username], tlsCAFile=certifi.where())
    return client[DB_NAME]


def build_sec_mapping(mdb, company_id):
    """Return {unprocessedId: securityId} from securityMappings."""
    result = {}
    doc = mdb.securityMappings.find_one({"companyId": company_id})
    if doc:
        for mapping in (doc.get("mappings") or []):
            from_id = mapping.get("from")
            sec_id  = mapping.get("securityId") or mapping.get("to")
            if from_id and sec_id:
                result[str(from_id)] = str(sec_id)
    return result


def get_processed_pos_map(mdb, wallet_id, position_date):
    """
    Query processedPosition for the given wallet/date.
    Returns {normalized_beehusName: {securityId, totalContribution}}.
    Tries exact date first, then nearest available date.
    """
    doc = mdb.processedPosition.find_one(
        {"walletId": wallet_id, "positionDate": position_date,
         "trashed": {"$ne": True}}
    )
    if not doc:
        # Try nearest earlier date
        doc = mdb.processedPosition.find_one(
            {"walletId": wallet_id,
             "positionDate": {"$lte": position_date},
             "trashed": {"$ne": True}},
            sort=[("positionDate", -1)]
        )

    if not doc:
        return {}, None

    actual_date = doc.get("positionDate", position_date)
    result = {}
    for sec in doc.get("securities", []):
        name = str(sec.get("beehusName") or "").strip()
        if not name:
            continue
        key = _normalize(name)
        result[key] = {
            "securityId":        str(sec.get("securityId") or ""),
            "totalContribution": sec.get("totalContribution"),
            "beehusName":        name,
        }
    return result, actual_date


def get_unprocessed_pos_map(mdb, wallet_id, position_date):
    """
    Fallback: query unprocessedSecurityPositions.
    unprocessedId format: '<identifier> - <type> - <name>' or just '<name>'.
    Returns {normalized_extracted_name: {securityId, totalContribution, fullUid}}.
    """
    doc = (
        mdb.unprocessedSecurityPositions.find_one(
            {"walletId": wallet_id, "positionDate": position_date,
             "trashed": {"$ne": True}}
        )
        or mdb.unprocessedSecurityPositions.find_one(
            {"walletId": wallet_id, "positionDate": position_date}
        )
    )
    if not doc:
        return {}

    result = {}
    for sec in doc.get("securities", []):
        full_uid = str(sec.get("unprocessedId") or "").strip()
        if not full_uid:
            continue

        # Extract asset name: last segment after splitting on ' - '
        parts = full_uid.split(" - ")
        if len(parts) >= 3:
            extracted = " - ".join(parts[2:]).strip()
        else:
            extracted = full_uid

        key = _normalize(extracted)
        result[key] = {
            "securityId":        str(sec.get("securityId") or ""),
            "totalContribution": sec.get("totalContribution"),
            "fullUid":           full_uid,
        }
    return result


# ── Row builder ───────────────────────────────────────────────────────────────

def make_row(label, codigo, cliente, wallet_id,
             ativo, sec_id, rendimento, total_contrib, obs=""):
    diff = (
        round(rendimento - total_contrib, 6)
        if rendimento is not None and total_contrib is not None
        else None
    )
    return {
        "Intervalo":                      label,
        "Código Comdinheiro":             codigo,
        "Cliente":                        cliente,
        "walletId (mongo)":               wallet_id,
        "Ativo (comdinheiro)":            ativo,
        "securityId (mongo)":             sec_id,
        "Rendimento Bruto (comdinheiro)": rendimento,
        "totalContribution (mongo)":      total_contrib,
        "Diferença":                      diff,
        "Obs":                            obs,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Conectando ao MongoDB...")
    try:
        mdb = get_mongo_db()
    except Exception as exc:
        print(f"ERRO MongoDB: {exc}")
        sys.exit(1)

    company_id = "23313334000110"
    print("Carregando securityMappings...")
    sec_mapping = build_sec_mapping(mdb, company_id)
    print(f"  {len(sec_mapping)} mapeamentos.")

    rows = []
    total_calls = len(CARTEIRAS) * len(INTERVALS)
    count = 0

    for company_id, cliente, wallet_id, codigo in CARTEIRAS:
        for label, data_ini, data_fim, pos_date in INTERVALS:
            count += 1
            print(f"[{count}/{total_calls}] {codigo} | {label} | {cliente[:28]}", end=" ... ", flush=True)

            # ── MongoDB: processedPosition (primary) ──────────────────────────
            proc_map, actual_date = get_processed_pos_map(mdb, wallet_id, pos_date)
            # Fallback: unprocessedSecurityPositions
            unproc_map = get_unprocessed_pos_map(mdb, wallet_id, pos_date) if not proc_map else {}

            date_note = f" (data real: {actual_date})" if actual_date and actual_date != pos_date else ""

            # ── Comdinheiro ───────────────────────────────────────────────────
            cd_assets, cd_err = [], ""
            try:
                cd_data           = query_comdinheiro(codigo, data_ini, data_fim)
                cd_assets, cd_err = parse_assets(cd_data)
            except Exception as exc:
                cd_err = str(exc)[:200]

            print(f"{len(cd_assets)} CD | {len(proc_map)} proc | {len(unproc_map)} unproc")

            if not cd_assets and not proc_map and not unproc_map:
                obs = (cd_err or "Sem dados") + date_note
                rows.append(make_row(label, codigo, cliente, wallet_id,
                                     "", "", None, None, obs))
                time.sleep(0.3)
                continue

            # Build CD lookup: {normalized_name: rendimento_bruto}
            cd_lookup = {_normalize(asset["ativo"]): asset for asset in cd_assets}

            # Union of all asset names (normalized)
            all_keys = set(cd_lookup) | set(proc_map) | set(unproc_map)

            for norm_key in sorted(all_keys):
                # Comdinheiro data
                cd_entry   = cd_lookup.get(norm_key)
                ativo_name = cd_entry["ativo"] if cd_entry else norm_key
                rendimento = cd_entry["rendimento_bruto"] if cd_entry else None

                # MongoDB data: processedPosition first, then unprocessed
                mongo_entry = proc_map.get(norm_key) or unproc_map.get(norm_key)

                if mongo_entry:
                    sec_id        = mongo_entry.get("securityId", "")
                    total_contrib = mongo_entry.get("totalContribution")
                    # Fallback securityId via securityMappings
                    if not sec_id:
                        sec_id = sec_mapping.get(norm_key, "")
                else:
                    sec_id        = sec_mapping.get(norm_key, "")
                    total_contrib = None

                obs_parts = []
                if cd_entry is None:
                    obs_parts.append("Só no MongoDB")
                elif mongo_entry is None:
                    obs_parts.append("Só no Comdinheiro")
                if cd_err:
                    obs_parts.insert(0, cd_err)
                if date_note:
                    obs_parts.append(date_note.strip())

                rows.append(make_row(
                    label, codigo, cliente, wallet_id,
                    ativo=ativo_name, sec_id=sec_id,
                    rendimento=rendimento, total_contrib=total_contrib,
                    obs=" | ".join(obs_parts),
                ))

            time.sleep(0.3)

    # ── Excel output ──────────────────────────────────────────────────────────
    cols = [
        "Intervalo", "Código Comdinheiro", "Cliente",
        "walletId (mongo)", "Ativo (comdinheiro)", "securityId (mongo)",
        "Rendimento Bruto (comdinheiro)", "totalContribution (mongo)",
        "Diferença", "Obs",
    ]
    df = pd.DataFrame(rows, columns=cols)

    out_path = os.path.join(BASE_DIR, "rentabilidade_oikos.xlsx")
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Rentabilidade")
        ws = writer.sheets["Rentabilidade"]
        for col_cells in ws.columns:
            max_len = max(
                (len(str(cell.value)) if cell.value is not None else 0)
                for cell in col_cells
            )
            ws.column_dimensions[col_cells[0].column_letter].width = min(max_len + 4, 60)

    print(f"\nExcel salvo: {out_path}")
    print(f"Total de linhas: {len(df)}")

    with_cd    = df["Rendimento Bruto (comdinheiro)"].notna().sum()
    with_mongo = df["totalContribution (mongo)"].notna().sum()
    with_diff  = (
        df["Rendimento Bruto (comdinheiro)"].notna()
        & df["totalContribution (mongo)"].notna()
    ).sum()
    print(f"  Com rendimento Comdinheiro: {with_cd}")
    print(f"  Com totalContribution mongo: {with_mongo}")
    print(f"  Com diferença calculada: {with_diff}")


if __name__ == "__main__":
    main()
