import re, requests
from collections import defaultdict

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOiI2OTJlZTk2MWE2ZDdhMjVkZjE5ZDZhODIiLCJ1c2VyTmFtZSI6Ill1cmkgTW9udGVpcm8gLSBCZWVodXMiLCJ0eXBlIjoiYmVlaHVzIiwiY29tcGFueUlkIjoiNTIzODc0MzIwMDAxMDkiLCJjb21wYW55TmFtZSI6IkJlZWh1cyBUZWNub2xvZ2lhIEx0ZGEuIiwiYWRtaW4iOnRydWUsInNob3VsZEFwcHJvdmVNb250aGx5UmVwb3J0cyI6ZmFsc2UsImlhdCI6MTc3OTk3NjM1NSwiZXhwIjoxNzgwMDYyNzU1fQ.dmuaS_fvqTmVvZdc6ssKr3dppw9NCIUAwpCmNQVYuC4"
BASE = "https://controladoria.beehus.com.br/beehus/financial/transactions"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
TICKER_RE = re.compile(r'\b(\d{2}[A-Z]\d{7})\b')

def get_id(f): return f.get("_id") if isinstance(f, dict) else f
def is_pgto(desc): return bool(desc and desc.startswith("Pgto "))
def ticker(desc): m = TICKER_RE.search(desc or ""); return m.group(1) if m else None

print("Buscando...", flush=True)
resp = requests.get(f"{BASE}?companyId=10000000000000", headers=HEADERS, timeout=180)
resp.raise_for_status()
data = resp.json()

filtered = [t for t in data
            if t.get("liquidationDate","")[:10] == "2026-05-22"
            and t.get("beehusTransactionType") in ("coupon","amortization")
            and t.get("inputType") == "xp-api"]

pgto_groups   = defaultdict(list)
master_groups = defaultdict(list)
for t in filtered:
    tk = ticker(t.get("description",""))
    key = (get_id(t.get("walletId")), get_id(t.get("securityId")),
           t.get("beehusTransactionType"), tk)
    (pgto_groups if is_pgto(t.get("description","")) else master_groups)[key].append(t)

# Singletons: 1 Pgto, sem master
to_delete = []
for key, ptxs in pgto_groups.items():
    if not master_groups.get(key) and len(ptxs) == 1:
        to_delete.extend(ptxs)

print(f"Singletons SEM_MASTER a deletar: {len(to_delete)}")
for t in to_delete:
    print(f"  {t['_id']}  {t.get('balance'):>10.2f}  {t.get('description','')[:60]}")

print(f"\nDeletando {len(to_delete)}...", flush=True)
deleted = errors = 0
for t in to_delete:
    r = requests.delete(f"{BASE}/{t['_id']}", headers=HEADERS, timeout=30)
    if r.status_code in (200, 204):
        print(f"  [OK] {t['_id']}  {t.get('balance'):>10.2f}  {t.get('description','')[:50]}")
        deleted += 1
    else:
        print(f"  [ERRO] {t['_id']} -> HTTP {r.status_code}: {r.text[:80]}")
        errors += 1

print(f"\nConcluido: {deleted} deletadas, {errors} erros.")
