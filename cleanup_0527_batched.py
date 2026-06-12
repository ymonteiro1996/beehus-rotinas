import re, requests, time
from collections import defaultdict

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOiI2OTJlZTk2MWE2ZDdhMjVkZjE5ZDZhODIiLCJ1c2VyTmFtZSI6Ill1cmkgTW9udGVpcm8gLSBCZWVodXMiLCJ0eXBlIjoiYmVlaHVzIiwiY29tcGFueUlkIjoiNTIzODc0MzIwMDAxMDkiLCJjb21wYW55TmFtZSI6IkJlZWh1cyBUZWNub2xvZ2lhIEx0ZGEuIiwiYWRtaW4iOnRydWUsInNob3VsZEFwcHJvdmVNb250aGx5UmVwb3J0cyI6ZmFsc2UsImlhdCI6MTc4MDQxOTE5OSwiZXhwIjoxNzgwNTA1NTk5fQ.fqi9Z0fTbqsZm5_lUypuj0RD8LW1x51KfTtI4gnUTGw"
BASE = "https://controladoria.beehus.com.br/beehus/financial/transactions"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
TICKER_RE = re.compile(r'\b(\d{2}[A-Z]\d{7})\b')
TARGET_DATE = "2026-05-27"
BATCH_SIZE = 10
BATCH_PAUSE = 5  # segundos entre lotes

def get_id(f): return f.get("_id") if isinstance(f, dict) else f
def is_pgto(desc): return bool(desc and desc.startswith("Pgto "))
def ticker(desc): m = TICKER_RE.search(desc or ""); return m.group(1) if m else None

def delete_batch(items, label):
    deleted = errors = skipped = 0
    total = len(items)
    batches = [items[i:i+BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]
    print(f"\n[{label}] {total} registros em {len(batches)} lotes de {BATCH_SIZE}", flush=True)
    for i, batch in enumerate(batches, 1):
        print(f"  Lote {i}/{len(batches)}...", flush=True)
        for t in batch:
            tid = t["_id"]
            try:
                r = requests.delete(f"{BASE}/{tid}", headers=HEADERS, timeout=60)
            except requests.exceptions.Timeout:
                try:
                    r = requests.delete(f"{BASE}/{tid}", headers=HEADERS, timeout=90)
                except Exception as e:
                    print(f"    [ERRO] {tid} -> {e}")
                    errors += 1
                    continue
            if r.status_code in (200, 204):
                print(f"    [OK] {tid}  {t.get('balance'):>10.2f}  {t.get('description','')[:50]}")
                deleted += 1
            elif r.status_code == 404:
                skipped += 1
            else:
                print(f"    [ERRO] {tid} -> HTTP {r.status_code}: {r.text[:60]}")
                errors += 1
        if i < len(batches):
            time.sleep(BATCH_PAUSE)
    print(f"  Subtotal: {deleted} deletados, {skipped} ja deletados, {errors} erros")
    return deleted, errors

# ── GET com retry ─────────────────────────────────────────────────────────
print(f"Buscando transacoes xp-api em {TARGET_DATE}...", flush=True)
for attempt in range(1, 8):
    try:
        resp = requests.get(f"{BASE}?companyId=10000000000000", headers=HEADERS, timeout=240)
        resp.raise_for_status()
        break
    except Exception as e:
        wait = attempt * 20
        print(f"  Tentativa {attempt} falhou ({e}). Aguardando {wait}s...", flush=True)
        time.sleep(wait)
else:
    print("Servidor indisponivel. Abortando.")
    raise SystemExit(1)

data = resp.json()
print(f"Total retornado: {len(data)}", flush=True)

day_xpapi = [t for t in data
             if t.get("liquidationDate","")[:10] == TARGET_DATE
             and t.get("inputType") == "xp-api"]
print(f"Transacoes xp-api em {TARGET_DATE}: {len(day_xpapi)}")

# ── PARTE 1: taxes balance > 0 ────────────────────────────────────────────
taxes_del = [t for t in day_xpapi
             if t.get("beehusTransactionType") == "taxes"
             and (t.get("balance") or 0) > 0]
print(f"\n[TAXES] A deletar: {len(taxes_del)}")
del_taxes, err_taxes = delete_batch(taxes_del, "TAXES") if taxes_del else (0, 0)

# ── PARTE 2: coupon/amortization dedup ───────────────────────────────────
filtered = [t for t in day_xpapi
            if t.get("beehusTransactionType") in ("coupon", "amortization")]

pgto_groups   = defaultdict(list)
master_groups = defaultdict(list)
for t in filtered:
    tk = ticker(t.get("description",""))
    key = (get_id(t.get("walletId")), get_id(t.get("securityId")),
           t.get("beehusTransactionType"), tk)
    (pgto_groups if is_pgto(t.get("description","")) else master_groups)[key].append(t)

to_delete = []
for key in set(pgto_groups) | set(master_groups):
    ptxs = pgto_groups.get(key, [])
    if not ptxs:
        continue
    to_delete.extend(ptxs)  # com master OU sem master (pares e singletons)

print(f"\n[DEDUP] coupon/amortization a deletar: {len(to_delete)}")
del_dedup, err_dedup = delete_batch(to_delete, "DEDUP") if to_delete else (0, 0)

# ── RESUMO ─────────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"RESUMO FINAL {TARGET_DATE}:")
print(f"  Taxes:  {del_taxes} deletadas  ({err_taxes} erros)")
print(f"  Dedup:  {del_dedup} deletadas  ({err_dedup} erros)")
print(f"  Total:  {del_taxes + del_dedup}")
