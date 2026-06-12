import re, requests
from collections import defaultdict, Counter

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
            if t.get("liquidationDate","")[:10] == "2026-05-20"
            and t.get("beehusTransactionType") in ("coupon","amortization")
            and t.get("inputType") == "xp-api"]

print(f"Total em 2026-05-20: {len(filtered)}")

# Separar Pgtos e Masters
pgtos_all   = [t for t in filtered if is_pgto(t.get("description",""))]
masters_all = [t for t in filtered if not is_pgto(t.get("description",""))]
print(f"  Pgtos:   {len(pgtos_all)}")
print(f"  Masters: {len(masters_all)}")
print()

# Para cada Pgto, verificar se existe master no mesmo grupo (wallet+sec+tipo+ticker)
pgto_groups = defaultdict(list)
master_groups = defaultdict(list)
for t in filtered:
    tk = ticker(t.get("description",""))
    key = (get_id(t.get("walletId")), get_id(t.get("securityId")),
           t.get("beehusTransactionType"), tk)
    if is_pgto(t.get("description","")):
        pgto_groups[key].append(t)
    else:
        master_groups[key].append(t)

# Categorizar Pgtos
cat_has_master = []   # tem master no grupo → deletar
cat_sem_master = []   # sem master → par ou singleton
cat_diverge    = []   # tem master mas soma nao bate

for key, ptxs in pgto_groups.items():
    mtxs = master_groups.get(key, [])
    if not mtxs:
        cat_sem_master.extend(ptxs)
        continue
    m_sum = round(sum(t.get("balance",0) for t in mtxs), 2)
    p_sum = round(sum(t.get("balance",0) for t in ptxs), 2)
    if m_sum >= p_sum - 0.05:
        cat_has_master.extend(ptxs)
    else:
        cat_diverge.extend(ptxs)

print(f"Pgtos COM master (a deletar):   {len(cat_has_master)}")
print(f"Pgtos SEM master:               {len(cat_sem_master)}")
print(f"Pgtos DIVERGE (master<pgto):    {len(cat_diverge)}")
print()

# Detalhar sem_master por ticker e padrao de pares
ticker_sem = Counter(ticker(t.get("description","")) for t in cat_sem_master)
print("Pgtos SEM master por ticker:")
for tk, cnt in ticker_sem.most_common():
    print(f"  {tk}: {cnt} pgtos")

# Verificar pares (2 Pgtos por grupo) vs singletons
print()
sem_master_groups = defaultdict(list)
for t in cat_sem_master:
    tk = ticker(t.get("description",""))
    key = (get_id(t.get("walletId")), get_id(t.get("securityId")),
           t.get("beehusTransactionType"), tk)
    sem_master_groups[key].append(t)

pairs    = {k:v for k,v in sem_master_groups.items() if len(v)==2}
singletons = {k:v for k,v in sem_master_groups.items() if len(v)==1}
bigger   = {k:v for k,v in sem_master_groups.items() if len(v)>2}
print(f"SEM_MASTER grupos pares (n=2):      {len(pairs)}")
print(f"SEM_MASTER grupos singleton (n=1):  {len(singletons)}")
print(f"SEM_MASTER grupos maiores (n>2):    {len(bigger)}")

# Mostrar primeiros 5 pares para entender padrao
print()
print("Exemplos de pares SEM_MASTER:")
for i, (key, txlist) in enumerate(list(pairs.items())[:5]):
    wallet_id, sec_id, tx_type, tk = key
    for t in sorted(txlist, key=lambda x: -x.get("balance",0)):
        print(f"  {t['_id']}  {t.get('balance'):>10.2f}  {t.get('description','')[:60]}")
    print(f"  => soma={round(sum(t.get('balance',0) for t in txlist),2)}")
    print()

# Verificar se SO_MASTERS (2 masters por grupo) estao corretos ou sao duplicatas
print("="*60)
print("SO_MASTERS (grupos com 2+ masters, 0 pgtos):")
so_master_groups = []
for key, mtxs in master_groups.items():
    ptxs = pgto_groups.get(key, [])
    if len(mtxs) >= 2 and not ptxs:
        so_master_groups.append((key, mtxs))

# Contar por ticker
so_tickers = Counter(key[3] for key,_ in so_master_groups)
print(f"Total grupos SO_MASTERS: {len(so_master_groups)}")
for tk, cnt in so_tickers.most_common():
    print(f"  {tk}: {cnt} grupos")
