import requests, time
from collections import Counter

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOiI2OTJlZTk2MWE2ZDdhMjVkZjE5ZDZhODIiLCJ1c2VyTmFtZSI6Ill1cmkgTW9udGVpcm8gLSBCZWVodXMiLCJ0eXBlIjoiYmVlaHVzIiwiY29tcGFueUlkIjoiNTIzODc0MzIwMDAxMDkiLCJjb21wYW55TmFtZSI6IkJlZWh1cyBUZWNub2xvZ2lhIEx0ZGEuIiwiYWRtaW4iOnRydWUsInNob3VsZEFwcHJvdmVNb250aGx5UmVwb3J0cyI6ZmFsc2UsImlhdCI6MTc4MTE4MTY1NCwiZXhwIjoxNzgxMjY4MDU0fQ.jZD0PHz7P9E5wcPAvsZZKcwquxmguQDKO_hmlK_dBeE"
BASE = "https://controladoria.beehus.com.br/beehus/financial/transactions"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
COMPANY_ID = "00000000000002"
BATCH_SIZE = 10
BATCH_PAUSE = 5

print(f"Buscando transacoes da company {COMPANY_ID}...", flush=True)
for attempt in range(1, 8):
    try:
        resp = requests.get(f"{BASE}?companyId={COMPANY_ID}", headers=HEADERS, timeout=240)
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

# Filtrar Dividend (case-insensitive para garantir)
to_patch = [t for t in data
            if str(t.get("beehusTransactionType","")).lower() == "dividend"]

print(f"\nTransacoes com beehusTransactionType=Dividend: {len(to_patch)}")
if to_patch:
    print("Por data:")
    for d, cnt in sorted(Counter(t.get("liquidationDate","")[:10] for t in to_patch).items()):
        print(f"  {d}: {cnt}")
    print("Exemplos de description:")
    for t in to_patch[:5]:
        print(f"  {t.get('description','')[:70]}")

if not to_patch:
    print("Nada a alterar.")
    raise SystemExit(0)

# PATCH em lotes de 10
batches = [to_patch[i:i+BATCH_SIZE] for i in range(0, len(to_patch), BATCH_SIZE)]
print(f"\nAtualizando {len(to_patch)} em {len(batches)} lotes de {BATCH_SIZE}...", flush=True)

updated = errors = 0
for i, batch in enumerate(batches, 1):
    print(f"  Lote {i}/{len(batches)}...", flush=True)
    for t in batch:
        tid = t["_id"]
        try:
            r = requests.patch(
                f"{BASE}/{tid}",
                headers=HEADERS,
                json={"beehusTransactionType": "coupon"},
                timeout=60
            )
        except requests.exceptions.Timeout:
            try:
                r = requests.patch(
                    f"{BASE}/{tid}",
                    headers=HEADERS,
                    json={"beehusTransactionType": "coupon"},
                    timeout=90
                )
            except Exception as e:
                print(f"    [ERRO] {tid} -> {e}")
                errors += 1
                continue
        if r.status_code in (200, 204):
            print(f"    [OK] {tid}  {t.get('liquidationDate','')[:10]}  {t.get('description','')[:55]}")
            updated += 1
        else:
            print(f"    [ERRO] {tid} -> HTTP {r.status_code}: {r.text[:80]}")
            errors += 1
    if i < len(batches):
        time.sleep(BATCH_PAUSE)

print(f"\n{'='*60}")
print(f"RESUMO: {updated} atualizadas (Dividend -> coupon), {errors} erros")
