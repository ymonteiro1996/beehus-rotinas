import re, requests
from collections import defaultdict, Counter

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOiI2OTJlZTk2MWE2ZDdhMjVkZjE5ZDZhODIiLCJ1c2VyTmFtZSI6Ill1cmkgTW9udGVpcm8gLSBCZWVodXMiLCJ0eXBlIjoiYmVlaHVzIiwiY29tcGFueUlkIjoiNTIzODc0MzIwMDAxMDkiLCJjb21wYW55TmFtZSI6IkJlZWh1cyBUZWNub2xvZ2lhIEx0ZGEuIiwiYWRtaW4iOnRydWUsInNob3VsZEFwcHJvdmVNb250aGx5UmVwb3J0cyI6ZmFsc2UsImlhdCI6MTc4MDQxOTE5OSwiZXhwIjoxNzgwNTA1NTk5fQ.fqi9Z0fTbqsZm5_lUypuj0RD8LW1x51KfTtI4gnUTGw"
BASE = "https://controladoria.beehus.com.br/beehus/financial/transactions"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
TARGET_DATE = "2026-05-27"

def get_id(f): return f.get("_id") if isinstance(f, dict) else f

print(f"Buscando transacoes xp-api em {TARGET_DATE}...", flush=True)
resp = requests.get(f"{BASE}?companyId=10000000000000", headers=HEADERS, timeout=180)
resp.raise_for_status()
data = resp.json()
print(f"Total retornado: {len(data)}", flush=True)

day_xpapi = [t for t in data
             if t.get("liquidationDate","")[:10] == TARGET_DATE
             and t.get("inputType") == "xp-api"]

print(f"Transacoes xp-api em {TARGET_DATE}: {len(day_xpapi)}")

# Mostrar todos os tipos presentes
tipos = Counter(t.get("beehusTransactionType","") for t in day_xpapi)
print("\nTipos presentes:")
for tp, cnt in tipos.most_common():
    print(f"  {tp}: {cnt}")

# Excluir os tipos ja tratados (coupon, amortization, taxes)
TIPOS_JA_TRATADOS = {"coupon", "amortization", "taxes"}
proventos = [t for t in day_xpapi
             if t.get("beehusTransactionType","") not in TIPOS_JA_TRATADOS]

print(f"\nProventos (excluindo coupon/amortization/taxes): {len(proventos)}")

if not proventos:
    print("Nenhum provento encontrado.")
    raise SystemExit(0)

# Agrupar por (walletId, securityId, beehusTransactionType, balance) para detectar duplicatas exatas
# Chave mais ampla: wallet + security + tipo + valor + data
groups = defaultdict(list)
for t in proventos:
    key = (
        get_id(t.get("walletId")),
        get_id(t.get("securityId")),
        t.get("beehusTransactionType",""),
        round(t.get("balance", 0), 2),
        t.get("description","")[:80],
    )
    groups[key].append(t)

dups   = {k: v for k, v in groups.items() if len(v) > 1}
unique = {k: v for k, v in groups.items() if len(v) == 1}

print(f"\nGrupos com duplicatas (mesmo wallet+security+tipo+valor+descricao): {len(dups)}")
print(f"Grupos unicos: {len(unique)}")

total_to_delete = sum(len(v) - 1 for v in dups.values())
print(f"Registros a deletar (mantendo 1 por grupo): {total_to_delete}")

if dups:
    print("\nDetalhe das duplicatas:")
    by_type = defaultdict(list)
    for key, txs in dups.items():
        by_type[key[2]].append((key, txs))

    for tp, items in sorted(by_type.items()):
        print(f"\n  [{tp}] — {sum(len(v)-1 for _,v in items)} a deletar de {len(items)} grupos")
        for key, txs in items[:10]:  # mostrar ate 10 grupos por tipo
            wallet_id, sec_id, tipo, bal, desc = key
            print(f"    wallet=...{str(wallet_id)[-8:]}  bal={bal:>10.2f}  n={len(txs)}  {desc[:55]}")
            for t in txs:
                print(f"      {t['_id']}")
        if len(items) > 10:
            print(f"    ... e mais {len(items)-10} grupos")

# Verificar tambem duplicatas por wallet+security+tipo sem exigir mesmo valor
print("\n" + "="*60)
print("Analise alternativa: duplicatas por wallet+security+tipo (ignorando valor):")
groups2 = defaultdict(list)
for t in proventos:
    key2 = (
        get_id(t.get("walletId")),
        get_id(t.get("securityId")),
        t.get("beehusTransactionType",""),
    )
    groups2[key2].append(t)

dups2 = {k: v for k, v in groups2.items() if len(v) > 1}
print(f"Grupos com 2+ registros (mesmo wallet+security+tipo): {len(dups2)}")
total2 = sum(len(v) for v in dups2.values())
print(f"Total de transacoes nesses grupos: {total2}")

if dups2:
    by_type2 = defaultdict(list)
    for key, txs in dups2.items():
        by_type2[key[2]].append((key, txs))
    for tp, items in sorted(by_type2.items()):
        total_txs = sum(len(v) for _, v in items)
        print(f"\n  [{tp}] {len(items)} grupos, {total_txs} transacoes")
        for key, txs in items[:5]:
            vals = [round(t.get("balance",0),2) for t in txs]
            print(f"    wallet=...{str(key[0])[-8:]}  n={len(txs)}  valores={vals}  desc={txs[0].get('description','')[:50]}")
        if len(items) > 5:
            print(f"    ... e mais {len(items)-5} grupos")
