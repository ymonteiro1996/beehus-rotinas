import requests
import json

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOiI2OTJlZTk2MWE2ZDdhMjVkZjE5ZDZhODIiLCJ1c2VyTmFtZSI6Ill1cmkgTW9udGVpcm8gLSBCZWVodXMiLCJ0eXBlIjoiYmVlaHVzIiwiY29tcGFueUlkIjoiNTIzODc0MzIwMDAxMDkiLCJjb21wYW55TmFtZSI6IkJlZWh1cyBUZWNub2xvZ2lhIEx0ZGEuIiwiYWRtaW4iOnRydWUsInNob3VsZEFwcHJvdmVNb250aGx5UmVwb3J0cyI6ZmFsc2UsImlhdCI6MTc3OTk3NjM1NSwiZXhwIjoxNzgwMDYyNzU1fQ.dmuaS_fvqTmVvZdc6ssKr3dppw9NCIUAwpCmNQVYuC4"
BASE_URL = "https://controladoria.beehus.com.br/beehus/financial/transactions"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

COMPANY_ID = "10000000000000"
LIQUIDATION_DATE = "2026-05-21"
TRANSACTION_TYPE = "taxes"

print("Buscando transações...")
resp = requests.get(f"{BASE_URL}?companyId={COMPANY_ID}", headers=HEADERS, timeout=120)
resp.raise_for_status()
all_transactions = resp.json()
print(f"Total de transações retornadas: {len(all_transactions)}")

to_delete = [
    t for t in all_transactions
    if t.get("liquidationDate", "").startswith(LIQUIDATION_DATE)
    and t.get("beehusTransactionType") == TRANSACTION_TYPE
    and (t.get("balance") or 0) > 0
]

print(f"\nTransações que serão deletadas ({len(to_delete)}):")
for t in to_delete:
    print(f"  ID: {t['_id']}  balance: {t['balance']}  desc: {t.get('description', '')}")

if not to_delete:
    print("Nenhuma transação encontrada para deletar.")
else:
    confirm = input(f"\nConfirmar deleção de {len(to_delete)} transações? (s/N): ")
    if confirm.strip().lower() != "s":
        print("Cancelado.")
    else:
        deleted = 0
        errors = 0
        for t in to_delete:
            tid = t["_id"]
            del_resp = requests.delete(f"{BASE_URL}/{tid}", headers=HEADERS, timeout=30)
            if del_resp.status_code in (200, 204):
                print(f"  [OK] Deletado: {tid}")
                deleted += 1
            else:
                print(f"  [ERRO] {tid} -> HTTP {del_resp.status_code}: {del_resp.text[:100]}")
                errors += 1
        print(f"\nConcluído: {deleted} deletadas, {errors} erros.")
