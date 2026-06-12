import requests
import sys
from collections import defaultdict

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOiI2OTJlZTk2MWE2ZDdhMjVkZjE5ZDZhODIiLCJ1c2VyTmFtZSI6Ill1cmkgTW9udGVpcm8gLSBCZWVodXMiLCJ0eXBlIjoiYmVlaHVzIiwiY29tcGFueUlkIjoiNTIzODc0MzIwMDAxMDkiLCJjb21wYW55TmFtZSI6IkJlZWh1cyBUZWNub2xvZ2lhIEx0ZGEuIiwiYWRtaW4iOnRydWUsInNob3VsZEFwcHJvdmVNb250aGx5UmVwb3J0cyI6ZmFsc2UsImlhdCI6MTc3OTk3NjM1NSwiZXhwIjoxNzgwMDYyNzU1fQ.dmuaS_fvqTmVvZdc6ssKr3dppw9NCIUAwpCmNQVYuC4"
BASE_URL = "https://controladoria.beehus.com.br/beehus/financial/transactions"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

COMPANY_ID = "10000000000000"
DATES = ["2026-05-19", "2026-05-20"]
TYPES = ["coupon", "amortization"]

def get_id(field):
    return field.get("_id") if isinstance(field, dict) else field

def is_pgto(desc):
    return desc.startswith("Pgto ") if desc else False

print("Buscando transacoes...", flush=True)
resp = requests.get(f"{BASE_URL}?companyId={COMPANY_ID}", headers=HEADERS, timeout=180)
resp.raise_for_status()
all_tx = resp.json()
print(f"Total retornado: {len(all_tx)}", flush=True)

# Filtrar por datas, tipo e inputType xp-api
filtered = [
    t for t in all_tx
    if t.get("liquidationDate", "")[:10] in DATES
    and t.get("beehusTransactionType") in TYPES
    and t.get("inputType") == "xp-api"
]
print(f"Filtradas (xp-api, coupon/amortization, datas alvo): {len(filtered)}", flush=True)

# Agrupar por (walletId, securityId, beehusTransactionType, liquidationDate)
groups = defaultdict(list)
for t in filtered:
    key = (
        get_id(t.get("walletId")),
        get_id(t.get("securityId")),
        t.get("beehusTransactionType"),
        t.get("liquidationDate", "")[:10],
    )
    groups[key].append(t)

to_delete = []
warnings = []

for key, txlist in groups.items():
    wallet_id, sec_id, tx_type, liq_date = key

    if len(txlist) == 1:
        continue  # sem duplicata

    # Separar master (nao-Pgto) dos duplicados (Pgto)
    masters = [t for t in txlist if not is_pgto(t.get("description", ""))]
    pgtos   = [t for t in txlist if is_pgto(t.get("description", ""))]

    if len(masters) == 1 and len(pgtos) >= 1:
        master = masters[0]
        master_bal = round(master.get("balance", 0), 2)
        pgto_sum   = round(sum(t.get("balance", 0) for t in pgtos), 2)

        if abs(master_bal - pgto_sum) <= 0.02:
            for t in pgtos:
                to_delete.append({
                    "id": t["_id"],
                    "balance": t.get("balance"),
                    "desc": t.get("description", "")[:60],
                    "wallet": wallet_id,
                    "sec": sec_id,
                    "type": tx_type,
                    "date": liq_date,
                })
        else:
            warnings.append(
                f"SOMA NAO CONFERE  wallet={wallet_id}  sec={sec_id}  type={tx_type}  date={liq_date}"
                f"  master={master_bal}  pgto_sum={pgto_sum}  n_pgtos={len(pgtos)}"
            )
    else:
        warnings.append(
            f"PADRAO INESPERADO  wallet={wallet_id}  sec={sec_id}  type={tx_type}  date={liq_date}"
            f"  n_masters={len(masters)}  n_pgtos={len(pgtos)}  total={len(txlist)}"
        )

print(f"\nTransacoes a deletar: {len(to_delete)}")
for d in to_delete:
    print(f"  {d['id']}  {d['balance']:>10.2f}  {d['type']:<14}  {d['date']}  {d['desc']}")

if warnings:
    print(f"\nAVISOS ({len(warnings)}):")
    for w in warnings:
        print(f"  {w}")

if not to_delete:
    print("\nNada para deletar.")
    sys.exit(0)

print(f"\nDeletando {len(to_delete)} transacoes...", flush=True)
deleted = errors = 0
for d in to_delete:
    r = requests.delete(f"{BASE_URL}/{d['id']}", headers=HEADERS, timeout=30)
    if r.status_code in (200, 204):
        print(f"  [OK] {d['id']}  {d['balance']:>10.2f}  {d['desc'][:50]}")
        deleted += 1
    else:
        print(f"  [ERRO] {d['id']} -> HTTP {r.status_code}: {r.text[:80]}")
        errors += 1

print(f"\nConcluido: {deleted} deletadas, {errors} erros.")
