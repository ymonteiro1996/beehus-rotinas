import json
import requests
import time
import sys

URL = "https://controladoria.beehus.com.br/beehus/financial/transactions"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOiI2OTJlZTk2MWE2ZDdhMjVkZjE5ZDZhODIiLCJ1c2VyTmFtZSI6Ill1cmkgTW9udGVpcm8gLSBCZWVodXMiLCJ0eXBlIjoiYmVlaHVzIiwiY29tcGFueUlkIjoiNTIzODc0MzIwMDAxMDkiLCJjb21wYW55TmFtZSI6IkJlZWh1cyBUZWNub2xvZ2lhIEx0ZGEuIiwiYWRtaW4iOnRydWUsImlhdCI6MTc3Nzk4NTI0OSwiZXhwIjoxNzc4MDcxNjQ5fQ.1nY1_wWhh2xxMUi2w3sjfDsb7nFzzPUuJbM8S4f-EQc"

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

def main():
    json_file = "ajuste_passivo_transactions.json"

    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    transactions = data["transactions"]
    total = len(transactions)
    print(f"Total de transações: {total}")

    success = 0
    errors = []

    for i, tx in enumerate(transactions, 1):
        date = tx.get("operationDate", "?")
        balance = tx.get("balance", "?")
        print(f"[{i}/{total}] {date} | balance={balance} ... ", end="", flush=True)

        resp = requests.post(URL, headers=HEADERS, json=tx)

        if resp.status_code in (200, 201):
            print(f"OK ({resp.status_code})")
            success += 1
        else:
            msg = f"ERRO {resp.status_code}: {resp.text[:200]}"
            print(msg)
            errors.append({"index": i, "date": date, "status": resp.status_code, "response": resp.text[:500]})

        time.sleep(0.1)

    print(f"\n--- Resultado ---")
    print(f"Sucesso: {success}/{total}")
    if errors:
        print(f"Erros ({len(errors)}):")
        for e in errors:
            print(f"  [{e['index']}] {e['date']} -> HTTP {e['status']}: {e['response']}")
    else:
        print("Nenhum erro.")

if __name__ == "__main__":
    main()
