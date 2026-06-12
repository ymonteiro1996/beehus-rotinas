import re, requests, sys
from collections import defaultdict

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOiI2OTJlZTk2MWE2ZDdhMjVkZjE5ZDZhODIiLCJ1c2VyTmFtZSI6Ill1cmkgTW9udGVpcm8gLSBCZWVodXMiLCJ0eXBlIjoiYmVlaHVzIiwiY29tcGFueUlkIjoiNTIzODc0MzIwMDAxMDkiLCJjb21wYW55TmFtZSI6IkJlZWh1cyBUZWNub2xvZ2lhIEx0ZGEuIiwiYWRtaW4iOnRydWUsInNob3VsZEFwcHJvdmVNb250aGx5UmVwb3J0cyI6ZmFsc2UsImlhdCI6MTc3OTk3NjM1NSwiZXhwIjoxNzgwMDYyNzU1fQ.dmuaS_fvqTmVvZdc6ssKr3dppw9NCIUAwpCmNQVYuC4"
BASE = "https://controladoria.beehus.com.br/beehus/financial/transactions"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
TICKER_RE = re.compile(r'\b(\d{2}[A-Z]\d{7})\b')
TARGET_DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-05-22"

def get_id(f): return f.get("_id") if isinstance(f, dict) else f
def is_pgto(desc): return bool(desc and desc.startswith("Pgto "))
def ticker(desc): m = TICKER_RE.search(desc or ""); return m.group(1) if m else None

print(f"Buscando transacoes para {TARGET_DATE}...", flush=True)
resp = requests.get(f"{BASE}?companyId=10000000000000", headers=HEADERS, timeout=180)
resp.raise_for_status()
data = resp.json()
print(f"Total retornado: {len(data)}", flush=True)

filtered = [t for t in data
            if t.get("liquidationDate","")[:10] == TARGET_DATE
            and t.get("beehusTransactionType") in ("coupon","amortization")
            and t.get("inputType") == "xp-api"]

print(f"coupon/amortization em {TARGET_DATE} (xp-api): {len(filtered)}")

pgto_groups   = defaultdict(list)
master_groups = defaultdict(list)
for t in filtered:
    tk = ticker(t.get("description",""))
    key = (get_id(t.get("walletId")), get_id(t.get("securityId")),
           t.get("beehusTransactionType"), tk)
    (pgto_groups if is_pgto(t.get("description","")) else master_groups)[key].append(t)

to_delete = []
skipped   = []

all_keys = set(pgto_groups) | set(master_groups)
for key in all_keys:
    ptxs = pgto_groups.get(key, [])
    mtxs = master_groups.get(key, [])

    if not ptxs:
        continue  # so masters, sem pgto — ok

    if not mtxs:
        # sem master: deletar apenas pares
        if len(ptxs) == 2:
            to_delete.extend(ptxs)
        else:
            skipped.append(f"SEM_MASTER n={len(ptxs)}  {key}")
        continue

    m_sum = round(sum(t.get("balance",0) for t in mtxs), 2)
    p_sum = round(sum(t.get("balance",0) for t in ptxs), 2)

    if m_sum >= p_sum - 0.05:
        to_delete.extend(ptxs)
    else:
        skipped.append(f"DIVERGE masters={m_sum} pgtos={p_sum}  {key}")

print(f"\nA deletar: {len(to_delete)}")
if skipped:
    print(f"Pulados:   {len(skipped)}")
    for s in skipped: print(f"  {s}")

if not to_delete:
    print("Nada para deletar.")
    sys.exit(0)

print(f"\nDeletando {len(to_delete)}...", flush=True)
deleted = errors = 0
for t in to_delete:
    r = requests.delete(f"{BASE}/{t['_id']}", headers=HEADERS, timeout=30)
    if r.status_code in (200, 204):
        print(f"  [OK] {t['_id']}  {t.get('balance'):>10.2f}  {t.get('description','')[:55]}")
        deleted += 1
    else:
        print(f"  [ERRO] {t['_id']} -> HTTP {r.status_code}: {r.text[:80]}")
        errors += 1

print(f"\nConcluido: {deleted} deletadas, {errors} erros.")
if skipped:
    print(f"Pulados (revisar): {len(skipped)}")
