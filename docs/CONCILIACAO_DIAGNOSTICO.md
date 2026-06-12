# Conciliação NAV

> `returnNavPerShare` é a fonte de verdade. O objetivo é fazer `returnContribution` convergir para esse valor.

---

## Princípio

O NAV é calculado a partir de saldos reais (posições, caixa, provisões). A cota deriva do NAV. Quando `returnContribution` diverge de `returnNavPerShare`, o problema está nos dados que alimentam o cálculo de contribuição — não no NAV.

A conciliação busca **corrigir `returnContribution`**, não "explicar o gap".

---

## Step 1 — Detectar

```
gap = returnNavPerShare − returnContribution
```

- `gap == 0` (dentro de tolerância) → sem problema. Fim.
- `gap ≠ 0` → seguir para Step 2.

---

## Step 2 — Eliminar

Uma carteira é composta por `cashAccounts`, `provisions` e `securities`. Securities são a principal fonte de divergências. Antes de investigar, eliminamos os que **com certeza** não são a causa.

Um security é eliminado quando **todas** as condições abaixo são verdadeiras:

| # | Condição | Significado |
|---|----------|-------------|
| a | `quantity == formerQuantity` | Sem variação de quantidade |
| b | Nenhuma transação associada na data | Sem movimentação financeira |
| c | Nenhuma provisão sendo criada ou liquidada na data | Sem evento de provisão |
| d | `rentabPU == rentabContribution` | Retornos iguais (`rentabPU = PU / formerPU − 1`, `rentabContribution = totalContribution / formerBalance`) |
| d′ | Se `d` falha: `Σbalance(coupon + amortization) ≈ expectedEventCash` | Evento explica a diferença → tratar como `d = true` |

Se **qualquer** condição falhar → security é suspeito e vai para Step 3.

---

## Step 3 — Diagnosticar Securities

Para cada security suspeito, investigar por tipo de causa.

### 3.1 — Amount Difference

Aplica quando `quantity ≠ formerQuantity`. Pode haver problema na **transação**, na **provisão**, ou em ambas.

Calcular offset a partir da coleção `securities`:

```
subscription (amountDiff > 0):  offset = subscriptionSettlementDays − subscriptionNavDays
redemption   (amountDiff < 0):  offset = redemptionSettlementDays − redemptionNavDays
```

| Offset | Significado | Esperado | Problema se ausente | Flag |
|--------|-------------|----------|---------------------|------|
| `== 0` | Liquidação imediata | Transação com o `securityId` | Transação faltando | `MISSING_TRANSACTION` |
| `> 0` | Liquidação futura (settlement após nav) | Provisão ativa | Provisão faltando | `MISSING_PROVISION` |
| `< 0` | Nav futuro (nav após settlement) | Provisão ativa | Provisão faltando | `MISSING_PROVISION` |

### 3.2 — Diferença de Rentabilidade

Aplica quando `rentabPU ≠ rentabContribution`. Indica provável evento (amortização, cupom).

Converter a diferença para valor monetário:

```
expectedEventCash = (rentabContribution − rentabPU) × formerBalance
```

Buscar transações de evento (`amortization`, `coupon`) para o security na data:

| Situação | Resultado | Flag |
|----------|-----------|------|
| Transação existe e `Σbalance ≈ expectedEventCash` | Explicado → security **eliminado** | — |
| Transação existe mas `Σbalance ≠ expectedEventCash` | Valor da transação errado | `WRONG_EVENT_BALANCE` |
| Sem transação → provisão existe e `amount ≈ expectedEventCash` | Explicado → security **eliminado** | — |
| Sem transação → provisão existe mas `amount ≠ expectedEventCash` | Valor da provisão errado | `WRONG_PROVISION_AMOUNT` |
| Sem transação e sem provisão | Transação/provisão ausente | `MISSING_EVENT` |

> **Nota:** `dividend` **não** está em `_EVENT_TYPES` — apenas `amortization` e `coupon` são detectados como transações de evento. Para dividendos, a diferença de rentab é tratada via provisão: na data do anúncio, deve existir uma provisão; na data do pagamento, a provisão é liquidada. A transação de dividendo em si não é usada no matching de Step 3.2.

### 3.3 — Withholding Tax ou Execution Price

Aplica quando há `amountDifference`. Valida o valor financeiro da transação.

```
expectedValue = amountDifference × executionPrice
```

> Se o usuário não informou `executionPrice`, o sistema preenche com `PU`.

| Situação | Resultado | Flag |
|----------|-----------|------|
| `expectedValue ≈ Σbalance(buySell)` | Transação correta | — |
| `expectedValue ≠ Σbalance` e `securityType == "brazilianFund"` | Provável **withholding tax** (IR retido na fonte) | `WITHHOLDING_TAX` |
| `expectedValue ≠ Σbalance` e `executionPrice == PU` | Provável **execution price** ausente | `MISSING_EXECUTION_PRICE` |
| `expectedValue ≠ Σbalance` nos demais casos | **Erro** no valor da transação | `WRONG_TRANSACTION_VALUE` |

---

## Step 4 — Diagnosticar Transações

Independente dos securities, analisar se alguma **transação** é elegível como causa do gap.

### 4.1 — Transação não identificada

Se `beehusTransactionType == null` → a transação não foi contabilizada. Problema identificado.

### 4.2 — Identificação incorreta de security

Se `transaction.securityId` não é nulo mas não existe entre os securities do `processedPosition`, a transação **pode** estar com o security errado.

Porém, há situações legítimas:

| Situação | Condição para NÃO ser erro |
|----------|---------------------------|
| **Compra de security novo** | `subscriptionNavDays > 0` — security ainda não entrou na posição. Confirmado se existe provisão para o securityId da transação. |
| **Venda com settlement futuro** | Security ainda não saiu da posição (settlement após NAV date, offset > 0). Provisão deve existir compensando o valor a receber. Confirmado se existe provisão ativa para o securityId da transação. |

Se nenhuma das condições acima se aplica (sem provisão correspondente) → provável erro de identificação do security na transação.

### 4.3 — Provável transação mal classificada

Cruzamento entre os valores faltantes identificados no Step 3 (`impact`) e as transações da data. Se um security tem um valor faltante (flag com `impact`) e existe uma transação com `|balance|` igual a esse valor que **não** está associada ao security, há alta probabilidade de que a transação esteja mal classificada (deveria pertencer àquele security).

---

## Step 5 — Validar Caixa

O caixa projetado deve ser igual ao caixa real. Caso contrário, há uma transação ausente ou com valor errado que impacta o NAV.

### Cálculo

```
projectedCash = formerCash + Σbalance(todas as transações da data)
cashDiff      = projectedCash − currentCash
```

- `formerCash` = soma dos `cashAccounts.values` na data anterior
- `currentCash` = soma dos `cashAccounts.values` na data atual
- Transações incluem todas as da carteira na data (`liquidationDate == date`)

### Diagnóstico

| Situação | Resultado |
|----------|-----------|
| `cashDiff == 0` | Caixa consistente |
| `cashDiff ≠ 0` e existem transações com `beehusTransactionType == null` | Transação não identificada impactando o caixa |
| `cashDiff ≠ 0` e não há transações na data | Transação de caixa ausente (provável `gainsExpenses`, `rebate` ou `otherFee`) |
| `cashDiff ≠ 0` nos demais casos | Valor de transação incorreto ou transação faltando |

> Transações de tipo `gainsExpenses`, `rebate` e `otherFee` afetam o caixa e o NAV mas **não** entram em `inAndOutFlows` nem no `totalContribution` — são uma causa frequente de gap.

---

## Step 6 — Anomalias de Rentabilidade

Validações estatísticas para identificar rentabilidades fora do esperado. Não explicam o gap diretamente, mas sinalizam dados potencialmente incorretos.

### 6.1 — Rentabilidade da carteira (`returnNavPerShare`)

Comparar `returnNavPerShare` da data atual contra o histórico da própria carteira.

```
threshold = média(returnNavPerShare histórico) ± 3 × desvio_padrão
```

Se `returnNavPerShare` da data está fora do threshold → flag de anomalia.

### 6.2 — Rentabilidade por security

Comparar `rentabPU` (`PU / formerPU − 1`) de cada security contra seu histórico.

> **Escopo:** apenas securities suspeitos (que falharam na eliminação do Step 2) são verificados. Securities eliminados não passam por esta validação.

> **Nota:** este cálculo pode ser intenso (muitos securities × muitas datas). Deve ser executado em um **processo separado** (batch/pré-cálculo), não dentro do fluxo de diagnóstico. O resultado (thresholds por security) fica armazenado em `rentability_thresholds.json` e é compartilhado entre a conciliação e a página **Validação Rentabilidades** (`/validacao-rentabilidades`).
