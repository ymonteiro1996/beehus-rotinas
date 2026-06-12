import re
import requests
import sys
from collections import defaultdict

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOiI2OTJlZTk2MWE2ZDdhMjVkZjE5ZDZhODIiLCJ1c2VyTmFtZSI6Ill1cmkgTW9udGVpcm8gLSBCZWVodXMiLCJ0eXBlIjoiYmVlaHVzIiwiY29tcGFueUlkIjoiNTIzODc0MzIwMDAxMDkiLCJjb21wYW55TmFtZSI6IkJlZWh1cyBUZWNub2xvZ2lhIEx0ZGEuIiwiYWRtaW4iOnRydWUsInNob3VsZEFwcHJvdmVNb250aGx5UmVwb3J0cyI6ZmFsc2UsImlhdCI6MTc3OTk3NjM1NSwiZXhwIjoxNzgwMDYyNzU1fQ.dmuaS_fvqTmVvZdc6ssKr3dppw9NCIUAwpCmNQVYuC4"
BASE_URL = "https://controladoria.beehus.com.br/beehus/financial/transactions"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

COMPANY_ID = "10000000000000"
DATES = ["2026-05-19", "2026-05-20"]
TYPES = ["coupon", "amortization"]

TICKER_RE = re.compile(r'\b(\d{2}[A-Z]\d{7})\b')

def get_id(f): return f.get("_id") if isinstance(f, dict) else f
def is_pgto(desc): return bool(desc and desc.startswith("Pgto "))
def extract_ticker(desc):
    m = TICKER_RE.search(desc or "")
    return m.group(1) if m else None

print("Buscando transacoes...", flush=True)
resp = requests.get(f"{BASE_URL}?companyId={COMPANY_ID}", headers=HEADERS, timeout=180)
resp.raise_for_status()
all_tx = resp.json()
print(f"Total retornado: {len(all_tx)}", flush=True)

filtered = [
    t for t in all_tx
    if t.get("liquidationDate", "")[:10] in DATES
    and t.get("beehusTransactionType") in TYPES
    and t.get("inputType") == "xp-api"
]
print(f"Filtradas: {len(filtered)}", flush=True)

# Agrupar por (walletId, securityId, tipo, data, ticker)
groups = defaultdict(list)
for t in filtered:
    ticker = extract_ticker(t.get("description", ""))
    key = (
        get_id(t.get("walletId")),
        get_id(t.get("securityId")),
        t.get("beehusTransactionType"),
        t.get("liquidationDate", "")[:10],
        ticker,
    )
    groups[key].append(t)

to_delete = []
skipped = []

for key, txlist in groups.items():
    wallet_id, sec_id, tx_type, liq_date, ticker = key

    if len(txlist) <= 1:
        continue

    masters = [t for t in txlist if not is_pgto(t.get("description", ""))]
    pgtos   = [t for t in txlist if is_pgto(t.get("description", ""))]

    master_total = round(sum(t.get("balance", 0) for t in masters), 2)
    pgto_total   = round(sum(t.get("balance", 0) for t in pgtos), 2)

    # Caso: nenhum master → pular
    if len(masters) == 0:
        skipped.append(f"AMBOS_PGTO  wallet={wallet_id}  sec={sec_id}  ticker={ticker}  type={tx_type}  date={liq_date}  pgtos={[t['_id'] for t in pgtos]}")
        continue

    # Caso: nenhum pgto → nada a deletar
    if len(pgtos) == 0:
        continue

    # Verificar que total_masters ≈ total_pgtos
    if abs(master_total - pgto_total) > 0.05:
        skipped.append(
            f"SOMA_DIVERGE  wallet={wallet_id}  sec={sec_id}  ticker={ticker}  type={tx_type}  date={liq_date}"
            f"  masters={master_total}  pgtos={pgto_total}"
        )
        continue

    # OK — deletar todos os pgtos
    for t in pgtos:
        to_delete.append({
            "id": t["_id"],
            "balance": t.get("balance"),
            "desc": t.get("description", "")[:60],
            "type": tx_type,
            "date": liq_date,
            "ticker": ticker,
        })

print(f"\nTransacoes a deletar: {len(to_delete)}")
for d in to_delete:
    print(f"  {d['id']}  {d['balance']:>10.2f}  {d['type']:<14}  {d['date']}  {d['ticker']}  {d['desc']}")

if skipped:
    print(f"\nPulados ({len(skipped)}):")
    for s in skipped:
        print(f"  {s}")

if not to_delete:
    print("\nNada para deletar.")
    sys.exit(0)

print(f"\nDeletando {len(to_delete)} transacoes...", flush=True)
deleted = errors = 0
for d in to_delete:
    r = requests.delete(f"{BASE_URL}/{d['id']}", headers=HEADERS, timeout=30)
    if r.status_code in (200, 204):
        print(f"  [OK] {d['id']}  {d['balance']:>10.2f}  {d['ticker']}  {d['desc'][:45]}")
        deleted += 1
    else:
        print(f"  [ERRO] {d['id']} -> HTTP {r.status_code}: {r.text[:80]}")
        errors += 1

print(f"\nConcluido: {deleted} deletadas, {errors} erros.")
if skipped:
    print(f"Pulados (revisar manualmente): {len(skipped)}")
