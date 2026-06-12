import re, requests
from collections import defaultdict, Counter

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOiI2OTJlZTk2MWE2ZDdhMjVkZjE5ZDZhODIiLCJ1c2VyTmFtZSI6Ill1cmkgTW9udGVpcm8gLSBCZWVodXMiLCJ0eXBlIjoiYmVlaHVzIiwiY29tcGFueUlkIjoiNTIzODc0MzIwMDAxMDkiLCJjb21wYW55TmFtZSI6IkJlZWh1cyBUZWNub2xvZ2lhIEx0ZGEuIiwiYWRtaW4iOnRydWUsInNob3VsZEFwcHJvdmVNb250aGx5UmVwb3J0cyI6ZmFsc2UsImlhdCI6MTc4MDQxOTE5OSwiZXhwIjoxNzgwNTA1NTk5fQ.fqi9Z0fTbqsZm5_lUypuj0RD8LW1x51KfTtI4gnUTGw"
BASE = "https://controladoria.beehus.com.br/beehus/financial/transactions"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
TICKER_RE = re.compile(r'\b(\d{2}[A-Z]\d{7})\b')
TARGET_DATE = "2026-05-27"

def get_id(f): return f.get("_id") if isinstance(f, dict) else f
def is_pgto(desc): return bool(desc and desc.startswith("Pgto "))
def ticker(desc): m = TICKER_RE.search(desc or ""); return m.group(1) if m else None

print(f"[DRY-RUN] Buscando transacoes para {TARGET_DATE}...", flush=True)
resp = requests.get(f"{BASE}?companyId=10000000000000", headers=HEADERS, timeout=180)
resp.raise_for_status()
data = resp.json()
print(f"Total retornado: {len(data)}", flush=True)

day_xpapi = [t for t in data
             if t.get("liquidationDate","")[:10] == TARGET_DATE
             and t.get("inputType") == "xp-api"]

print(f"Transacoes xp-api em {TARGET_DATE}: {len(day_xpapi)}")

# ── TAXES com balance > 0 ───────────────────────────────────────────────────
taxes_del = [t for t in day_xpapi
             if t.get("beehusTransactionType") == "taxes"
             and (t.get("balance") or 0) > 0]

print(f"\n[TAXES] balance > 0: {len(taxes_del)}")
taxes_by_desc = Counter(t.get("description","")[:50] for t in taxes_del)
for desc, cnt in taxes_by_desc.most_common(10):
    print(f"  {cnt:>4}x  {desc}")
total_taxes_val = sum(t.get("balance",0) for t in taxes_del)
print(f"  Soma total: {total_taxes_val:,.2f}")

# ── DEDUP coupon/amortization ───────────────────────────────────────────────
filtered = [t for t in day_xpapi
            if t.get("beehusTransactionType") in ("coupon", "amortization")]

print(f"\n[DEDUP] coupon/amortization em {TARGET_DATE}: {len(filtered)}")
pgtos_count   = sum(1 for t in filtered if is_pgto(t.get("description","")))
masters_count = len(filtered) - pgtos_count
print(f"  Pgtos:   {pgtos_count}")
print(f"  Masters: {masters_count}")

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
        continue

    if mtxs:
        to_delete.extend(ptxs)
    else:
        if len(ptxs) == 2:
            to_delete.extend(ptxs)
        else:
            skipped.append((key, ptxs))

print(f"\n  A deletar (pgtos com master + pares SEM_MASTER): {len(to_delete)}")
print(f"  Singletons SEM_MASTER (aguardar decisao):        {len(skipped)}")

if skipped:
    print(f"\n  Detalhe dos singletons:")
    for key, ptxs in skipped:
        for t in ptxs:
            print(f"    {t['_id']}  {t.get('balance'):>10.2f}  {t.get('description','')[:60]}")

# ── RESUMO ──────────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"RESUMO DRY-RUN {TARGET_DATE}:")
print(f"  Taxes a deletar:              {len(taxes_del)}")
print(f"  Dedup a deletar:              {len(to_delete)}")
print(f"  Total a deletar:              {len(taxes_del) + len(to_delete)}")
print(f"  Singletons SEM_MASTER:        {len(skipped)} (nao serao deletados)")
print(f"\nExecute cleanup_0527.py para confirmar a delecao.")
