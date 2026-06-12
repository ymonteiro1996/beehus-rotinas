import re, requests, sys
from collections import defaultdict

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOiI2OTJlZTk2MWE2ZDdhMjVkZjE5ZDZhODIiLCJ1c2VyTmFtZSI6Ill1cmkgTW9udGVpcm8gLSBCZWVodXMiLCJ0eXBlIjoiYmVlaHVzIiwiY29tcGFueUlkIjoiNTIzODc0MzIwMDAxMDkiLCJjb21wYW55TmFtZSI6IkJlZWh1cyBUZWNub2xvZ2lhIEx0ZGEuIiwiYWRtaW4iOnRydWUsInNob3VsZEFwcHJvdmVNb250aGx5UmVwb3J0cyI6ZmFsc2UsImlhdCI6MTc3OTk3NjM1NSwiZXhwIjoxNzgwMDYyNzU1fQ.dmuaS_fvqTmVvZdc6ssKr3dppw9NCIUAwpCmNQVYuC4"
BASE = "https://controladoria.beehus.com.br/beehus/financial/transactions"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
TICKER_RE = re.compile(r'\b(\d{2}[A-Z]\d{7})\b')
TARGET_DATE = "2026-05-25"

def get_id(f): return f.get("_id") if isinstance(f, dict) else f
def is_pgto(desc): return bool(desc and desc.startswith("Pgto "))
def ticker(desc): m = TICKER_RE.search(desc or ""); return m.group(1) if m else None

print(f"Buscando transacoes... (somente {TARGET_DATE})", flush=True)
resp = requests.get(f"{BASE}?companyId=10000000000000", headers=HEADERS, timeout=180)
resp.raise_for_status()
data = resp.json()
print(f"Total retornado: {len(data)}", flush=True)

# Filter strictly to TARGET_DATE + xp-api
day_xpapi = [t for t in data
             if t.get("liquidationDate","")[:10] == TARGET_DATE
             and t.get("inputType") == "xp-api"]

print(f"Transacoes xp-api em {TARGET_DATE}: {len(day_xpapi)}")

# ── PARTE 1: taxes com balance > 0 ─────────────────────────────────────────
taxes_del = [t for t in day_xpapi
             if t.get("beehusTransactionType") == "taxes"
             and (t.get("balance") or 0) > 0]

print(f"\n[TAXES] A deletar (balance > 0): {len(taxes_del)}")
for t in taxes_del:
    print(f"  {t['_id']}  {t.get('balance'):>12.2f}  {t.get('description','')[:60]}")

deleted_taxes = errors_taxes = 0
if taxes_del:
    print(f"\nDeletando {len(taxes_del)} taxes...", flush=True)
    for t in taxes_del:
        r = requests.delete(f"{BASE}/{t['_id']}", headers=HEADERS, timeout=30)
        if r.status_code in (200, 204):
            print(f"  [OK] {t['_id']}  {t.get('balance'):>12.2f}")
            deleted_taxes += 1
        else:
            print(f"  [ERRO] {t['_id']} -> HTTP {r.status_code}: {r.text[:80]}")
            errors_taxes += 1
    print(f"Taxes: {deleted_taxes} deletadas, {errors_taxes} erros.")
else:
    print("Nenhuma taxes a deletar.")

# ── PARTE 2: dedup coupon/amortization ─────────────────────────────────────
filtered = [t for t in day_xpapi
            if t.get("beehusTransactionType") in ("coupon", "amortization")]

print(f"\n[DEDUP] coupon/amortization em {TARGET_DATE}: {len(filtered)}")

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
        continue  # so masters, ok

    if mtxs:
        # Tem master → deletar todos os pgtos
        to_delete.extend(ptxs)
    else:
        # Sem master
        if len(ptxs) == 2:
            to_delete.extend(ptxs)  # par aprovado
        else:
            skipped.append(f"SEM_MASTER n={len(ptxs)}  wallet={str(key[0])[-8:]}  {key[2]}  ticker={key[3]}")

print(f"A deletar: {len(to_delete)}")
if skipped:
    print(f"Pulados (SEM_MASTER singletons): {len(skipped)}")
    for s in skipped:
        print(f"  {s}")

deleted_dedup = errors_dedup = 0
if to_delete:
    print(f"\nDeletando {len(to_delete)} coupon/amortization...", flush=True)
    for t in to_delete:
        r = requests.delete(f"{BASE}/{t['_id']}", headers=HEADERS, timeout=30)
        if r.status_code in (200, 204):
            print(f"  [OK] {t['_id']}  {t.get('balance'):>12.2f}  {t.get('description','')[:55]}")
            deleted_dedup += 1
        else:
            print(f"  [ERRO] {t['_id']} -> HTTP {r.status_code}: {r.text[:80]}")
            errors_dedup += 1
else:
    print("Nada para deletar no dedup.")

# ── RESUMO ──────────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"RESUMO {TARGET_DATE}:")
print(f"  Taxes deletadas:      {deleted_taxes}  (erros: {errors_taxes})")
print(f"  Dedup deletadas:      {deleted_dedup}  (erros: {errors_dedup})")
print(f"  Total:                {deleted_taxes + deleted_dedup}")
if skipped:
    print(f"  Singletons pulados:   {len(skipped)} (revisar manualmente)")
