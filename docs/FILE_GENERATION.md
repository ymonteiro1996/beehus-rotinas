# Geração de Arquivos JSON

> Documentação global dos templates de arquivo JSON usados para upload ao banco de dados. Compartilhado entre todas as páginas que geram arquivos (Conciliação, Validação Rentabilidades, Replicação de Cenário, etc.).

---

## Templates

### 1. Transactions

**Usado por:** Conciliação (correções), Replicação de Cenário

```json
{
  "companyId": "...",
  "transactions": [
    {
      "companyId": "00000000000000",
      "entityId": "67c0a6b471f5e8c88f76044b",
      "walletId": "68bb268b9a9a11e087ee53de",
      "currencyId": "BRL",
      "securityId": "...",
      "operationDate": "2026-02-24",
      "liquidationDate": "2026-02-24",
      "balance": -20000000,
      "description": "Resgate de Cotas PF",
      "inputType": "sheets",
      "beehusTransactionType": "withdrawalDeposit",
      "hide": false,
      "comment": ""
    }
  ]
}
```

**Campos e origem:**

| Campo | Origem |
|-------|--------|
| `companyId` | `db.wallets` → wallet.companyId |
| `entityId` | `db.wallets` → wallet.entityId |
| `walletId` | Wallet de origem ou destino |
| `currencyId` | `db.wallets` → wallet.currencyId |
| `securityId` | Do security associado (campo omitido se não há security) |
| `operationDate` | Data da operação |
| `liquidationDate` | Data de liquidação |
| `balance` | Valor financeiro |
| `description` | Descrição da transação |
| `inputType` | `"sheets"` |
| `beehusTransactionType` | Tipo: `buySell`, `withdrawalDeposit`, `dividend`, `gainsExpenses`, etc. |
| `hide` | `true` para correções, `false` para replicação |
| `comment` | Observação livre |

---

### 2. Wallets

**Usado por:** Replicação de Cenário

```json
[
  {
    "name": "6485826",
    "hasDailyPosition": true,
    "companyId": "10000000000000",
    "currency": "BRL",
    "startDateConsolidation": "2026-03-11",
    "startDateReturn": "2026-03-11",
    "entityId": "67cf6a5c71f5e8c88f760505",
    "accountCode": "6485826",
    "consumptionIdentifiers": [],
    "securitiesForExplosion": []
  }
]
```

**Campos e origem:**

| Campo | Origem |
|-------|--------|
| `name` | Nome da carteira |
| `hasDailyPosition` | Se possui posição diária |
| `companyId` | ID da empresa |
| `currency` | Moeda da carteira |
| `startDateConsolidation` | Data de início da consolidação |
| `startDateReturn` | Data de início do retorno |
| `entityId` | ID da entidade |
| `accountCode` | Código da conta |
| `consumptionIdentifiers` | Lista de identificadores de consumo |
| `securitiesForExplosion` | Lista de securities para explosão |

---

### 3. Positions (unprocessedSecurities)

**Usado por:** Replicação de Cenário

```json
{
  "companyId": "86246110100000",
  "unprocessedSecurities": [
    {
      "date": "2025-01-31",
      "walletId": "680928415ea164e619dc813d",
      "security": "INVESCO S&P 500 EQUAL WEIGHT",
      "quantity": 1837,
      "pu": 332937.88,
      "balance": 181.24,
      "currencyId": "USD",
      "cashAccount": "Nao"
    }
  ]
}
```

**Campos e origem:**

| Campo | Origem |
|-------|--------|
| `companyId` | ID da empresa |
| `date` | Data da posição |
| `walletId` | ID da carteira (destino) |
| `security` | Nome do security (`beehusName`) |
| `quantity` | Quantidade |
| `pu` | Preço unitário |
| `balance` | Saldo financeiro (`pu × quantity`) |
| `currencyId` | Moeda do security |
| `cashAccount` | Se é conta caixa (`"Sim"` / `"Nao"`) |

---

### 4. Provisions (clipboard/Excel)

**Usado por:** Conciliação (correções), Replicação de Cenário

Provisões não possuem upload JSON no momento. O sistema gera uma **tabela copiável** (clipboard) que pode ser colada diretamente em uma planilha Excel.

**Colunas:**

| Coluna | Origem |
|--------|--------|
| `walletId` | ID da carteira |
| `initialDate` | Data de início da provisão (formato `DD/MMM/YY`, ex: `01/abr/24`) |
| `liquidationDate` | Data de liquidação (formato `DD/MMM/YY`) |
| `provisionType` | Tipo: `dividend`, `buySell`, etc. |
| `securityId` | ID do security |
| `balance` | Valor da provisão |
| `description` | Descrição da provisão |
| `provisionSource` | Origem: `adjustments` |
| `currencyId` | Moeda: `BRL`, `USD`, etc. |

**Exemplo:**

```
walletId	initialDate	liquidationDate	provisionType	securityId	balance	description	provisionSource	currencyId
688246985...	01/abr/24	30/abr/25	dividend	67fee83756e	1303,68	Juros sobre capital próprio de BBDC4 a receber em 30/abr/2025	adjustments	BRL
```

> **Nota:** Upload JSON para provisions será adicionado futuramente. Até lá, o fluxo é copiar a tabela e colar na planilha de upload.

---

## Uso por Página

### Conciliação — Correções

Após o diagnóstico (Steps 1-6), o analista aceita itens identificados como causa do gap.

**Fluxo:**

```
Diagnóstico → Aceitar itens → Contador flutuante → Gerar arquivo
```

**Itens aceitáveis:**

| Step | Tipo | Quando aparece |
|------|------|---------------|
| 3.1 | Amount Difference | Transação ou provisão faltando |
| 3.2 | Rentability Difference | Evento errado, provisão errada, ou ausente |
| 3.3 | Withholding Tax / Execution Price | IR, execution price, ou valor errado |
| 4.1 | Transação não identificada | `beehusTransactionType == null` |
| 4.2 | Security divergente | Security não está na posição |
| 5 | Cash mismatch | Diferença entre caixa projetado e atual |

**Mapeamento flag → transactionType:**

| Flag | `beehusTransactionType` |
|------|------------------------|
| `MISSING_TRANSACTION` | `buySell` |
| `MISSING_EVENT` | `dividend` |
| `WITHHOLDING_TAX` | `buySell` |
| `MISSING_EXECUTION_PRICE` | `buySell` |
| `WRONG_TRANSACTION_VALUE` | `buySell` |
| `CASH_MISMATCH` | `gainsExpenses` |

**Flags que não geram transações:**
- `MISSING_PROVISION` — provisão (futuro Excel)
- `WRONG_PROVISION_AMOUNT` — ajuste de provisão
- `WRONG_EVENT_BALANCE` — transação existente com valor errado
- `WRONG_SECURITY` — reclassificação
- `UNCLASSIFIED_TRANSACTION` — reclassificação

**Indicador "Provável Causa":** quando o impacto de um flag é igual ao gap (±1%), um badge **"PROVÁVEL CAUSA"** aparece.

---

### Conciliação — Replicação de Cenário

Clona todos os dados de um wallet+date para um target wallet+date.

**Fluxo:**

```
Selecionar wallet + date (origem)
        │
        ▼
Clicar "Replicar Cenário"
        │
        ▼
Informar target walletId + date
        │
        ▼
Sistema gera 3 JSONs:
  1. Wallet (template Wallets)
  2. Positions (template Positions)
  3. Transactions (template Transactions)
```

**Dados copiados da origem:**
- `db.wallets` → JSON Wallets (com target companyId/dates ajustados)
- `db.processedPosition` → JSON Positions (securities convertidos para formato unprocessed)
- `db.transactions` → JSON Transactions (com target walletId/dates ajustados)

**Endpoint:** `POST /api/conciliacao/replicate-scenario` *(a implementar)*

**Input:**
```json
{
  "sourceWalletId": "...",
  "sourceDate": "2026-04-03",
  "targetWalletId": "...",
  "targetDate": "2026-04-05"
}
```

**Output:** ZIP ou JSONs individuais para download.
