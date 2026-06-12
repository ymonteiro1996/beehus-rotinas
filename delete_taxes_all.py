import requests, time

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOiI2OTJlZTk2MWE2ZDdhMjVkZjE5ZDZhODIiLCJ1c2VyTmFtZSI6Ill1cmkgTW9udGVpcm8gLSBCZWVodXMiLCJ0eXBlIjoiYmVlaHVzIiwiY29tcGFueUlkIjoiNTIzODc0MzIwMDAxMDkiLCJjb21wYW55TmFtZSI6IkJlZWh1cyBUZWNub2xvZ2lhIEx0ZGEuIiwiYWRtaW4iOnRydWUsInNob3VsZEFwcHJvdmVNb250aGx5UmVwb3J0cyI6ZmFsc2UsImlhdCI6MTc4MDY4NTE0NiwiZXhwIjoxNzgwNzcxNTQ2fQ.gytCnGYOtl89CyW5jytF8ER-x2pq60-KWoReADGb0zI"
BASE = "https://controladoria.beehus.com.br/beehus/financial/transactions"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
BATCH_SIZE = 10
BATCH_PAUSE = 5

# ── GET com retry ─────────────────────────────────────────────────────────
print("Buscando todas as transacoes...", flush=True)
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

# ── Filtrar taxes > 0, xp-api, todas as datas ────────────────────────────
to_delete = [t for t in data
             if t.get("beehusTransactionType") == "taxes"
             and t.get("inputType") == "xp-api"
             and (t.get("balance") or 0) > 0]

from collections import Counter
dates = Counter(t.get("liquidationDate","")[:10] for t in to_delete)
print(f"\nTaxes > 0 (xp-api) a deletar: {len(to_delete)}")
print("Por data:")
for d, c in sorted(dates.items()):
    print(f"  {d}: {c}")

if not to_delete:
    print("Nada a deletar.")
    raise SystemExit(0)

# ── Delete em lotes de 10 ────────────────────────────────────────────────
batches = [to_delete[i:i+BATCH_SIZE] for i in range(0, len(to_delete), BATCH_SIZE)]
print(f"\nDeletando {len(to_delete)} em {len(batches)} lotes de {BATCH_SIZE}...", flush=True)

deleted = errors = skipped = 0
for i, batch in enumerate(batches, 1):
    print(f"  Lote {i}/{len(batches)}...", flush=True)
    for t in batch:
        tid = t["_id"]
        date = t.get("liquidationDate","")[:10]
        bal  = t.get("balance", 0)
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
            print(f"    [OK] {tid}  {bal:>10.2f}  {date}")
            deleted += 1
        elif r.status_code == 404:
            skipped += 1
        else:
            print(f"    [ERRO] {tid} -> HTTP {r.status_code}: {r.text[:60]}")
            errors += 1
    if i < len(batches):
        time.sleep(BATCH_PAUSE)

print(f"\n{'='*60}")
print(f"RESUMO: {deleted} deletadas, {skipped} ja deletadas, {errors} erros")
