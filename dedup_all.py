import re, requests, sys
from collections import defaultdict, Counter

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOiI2OTJlZTk2MWE2ZDdhMjVkZjE5ZDZhODIiLCJ1c2VyTmFtZSI6Ill1cmkgTW9udGVpcm8gLSBCZWVodXMiLCJ0eXBlIjoiYmVlaHVzIiwiY29tcGFueUlkIjoiNTIzODc0MzIwMDAxMDkiLCJjb21wYW55TmFtZSI6IkJlZWh1cyBUZWNub2xvZ2lhIEx0ZGEuIiwiYWRtaW4iOnRydWUsInNob3VsZEFwcHJvdmVNb250aGx5UmVwb3J0cyI6ZmFsc2UsImlhdCI6MTc3OTk3NjM1NSwiZXhwIjoxNzgwMDYyNzU1fQ.dmuaS_fvqTmVvZdc6ssKr3dppw9NCIUAwpCmNQVYuC4"
BASE = "https://controladoria.beehus.com.br/beehus/financial/transactions"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
TICKER_RE = re.compile(r'\b(\d{2}[A-Z]\d{7})\b')

def get_id(f): return f.get("_id") if isinstance(f, dict) else f
def is_pgto(desc): return bool(desc and desc.startswith("Pgto "))
def ticker(desc): m = TICKER_RE.search(desc or ""); return m.group(1) if m else None

print("Buscando todas as transacoes...", flush=True)
resp = requests.get(f"{BASE}?companyId=10000000000000", headers=HEADERS, timeout=180)
resp.raise_for_status()
data = resp.json()
print(f"Total: {len(data)}", flush=True)

# Filtrar apenas xp-api coupon/amortization
filtered = [t for t in data
            if t.get("beehusTransactionType") in ("coupon", "amortization")
            and t.get("inputType") == "xp-api"]

print(f"xp-api coupon/amortization (todas as datas): {len(filtered)}")

# Contar por data
dates = Counter(t.get("liquidationDate","")[:10] for t in filtered)
print("Por data:")
for d, c in sorted(dates.items()): print(f"  {d}: {c}")
print()

# Agrupar por wallet+sec+tipo+data+ticker
pgto_groups   = defaultdict(list)
master_groups = defaultdict(list)
for t in filtered:
    tk = ticker(t.get("description",""))
    key = (get_id(t.get("walletId")), get_id(t.get("securityId")),
           t.get("beehusTransactionType"), t.get("liquidationDate","")[:10], tk)
    (pgto_groups if is_pgto(t.get("description","")) else master_groups)[key].append(t)

to_delete = []
skipped   = []

all_keys = set(pgto_groups) | set(master_groups)
for key in all_keys:
    ptxs = pgto_groups.get(key, [])
    mtxs = master_groups.get(key, [])

    if not ptxs:
        continue  # sem pgtos — nada a fazer

    if mtxs:
        # Tem master → deletar TODOS os pgtos independente da soma
        to_delete.extend(ptxs)
    else:
        # Sem master
        if len(ptxs) == 2:
            to_delete.extend(ptxs)   # par aprovado pelo usuario
        else:
            skipped.append(f"SEM_MASTER n={len(ptxs)}  date={key[3]}  wallet={key[0][-8:]}  {key[2]}  ticker={key[4]}")

print(f"A deletar: {len(to_delete)}")
print(f"Pulados (SEM_MASTER singletons): {len(skipped)}")
print()

# Resumo por data do que sera deletado
dates_del = Counter(t.get("liquidationDate","")[:10] for t in to_delete)
print("Delecoes por data:")
for d, c in sorted(dates_del.items()): print(f"  {d}: {c}")
print()

if not to_delete:
    print("Nada para deletar.")
    sys.exit(0)

print(f"Deletando {len(to_delete)}...", flush=True)
deleted = errors = 0
for t in to_delete:
    r = requests.delete(f"{BASE}/{t['_id']}", headers=HEADERS, timeout=30)
    if r.status_code in (200, 204):
        deleted += 1
        print(f"  [OK] {t['_id']}  {t.get('balance'):>10.2f}  {t.get('liquidationDate','')[:10]}  {t.get('description','')[:50]}")
    else:
        errors += 1
        print(f"  [ERRO] {t['_id']} -> HTTP {r.status_code}: {r.text[:80]}")

print(f"\nConcluido: {deleted} deletadas, {errors} erros.")
if skipped:
    print(f"Singletons pulados: {len(skipped)}")
    for s in skipped: print(f"  {s}")
