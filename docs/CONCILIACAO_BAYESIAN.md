# Otimização Bayesiana — Diagnóstico de Conciliação

> Dado o conjunto de flags diagnosticados (Steps 3–5), encontrar a combinação de correções que mais provavelmente fecha o gap, ponderada por confiança.

---

## Princípio

Cada flag do diagnóstico identifica uma **causa** e um **impacto**. Nem todos os impactos são certos — alguns são exatos, outros têm uma faixa provável. A otimização bayesiana atribui:

- **Confiança** (prior): probabilidade de que o flag seja realmente a causa
- **Distribuição do impacto**: valor exato ou faixa provável do fix

O objetivo: encontrar o subconjunto de flags cujos impactos somados fecham o gap.

```
P(fix_set | gap) ∝ P(gap | fix_set) × P(fix_set)

P(fix_set)      = Π(confidence_i)  para cada flag_i no fix_set
P(gap | fix_set) = N(0, σ²)        likelihood gaussiana centrada em 0
                   onde residual = gapCash − Σ(fix_impact_i)
```

---

## Tiers de Confiança

### Tier 1 — Determinístico (confidence ≈ 1.0)

O valor do fix é totalmente determinado pelos dados. Sem incerteza.

| Flag | Impacto | Distribuição | Confidence default |
|------|---------|--------------|-------------------|
| `MISSING_TRANSACTION` (offset=0) | `amountDiff × price` | Ponto fixo (delta) | **0.95** |
| `MISSING_PROVISION` | `amountDiff × price` | Ponto fixo (delta) | **0.90** |
| `MISSING_EVENT` | `expectedEventCash` | Ponto fixo (delta) | **0.85** |
| `WRONG_EVENT_BALANCE` | `expectedEventCash − eventTransactionTotal` | Ponto fixo (delta) | **0.85** |
| `WRONG_PROVISION_AMOUNT` | `expectedEventCash − provisionAmount` | Ponto fixo (delta) | **0.80** |
| `CASH_MISMATCH` (1 unclassified txn) | `cashDiff` | Ponto fixo (delta) | **0.90** |
| `UNCLASSIFIED_TRANSACTION` | `txn.balance` | Ponto fixo (delta) | **0.85** |

### Tier 2 — Bounded (confidence 0.5–0.9)

A causa é identificada mas o valor exato depende de um parâmetro desconhecido dentro de uma faixa.

| Flag | Parâmetro desconhecido | Distribuição | Confidence default |
|------|----------------------|--------------|-------------------|
| `MISSING_EXECUTION_PRICE` | `executionPrice` real | **Uniforme** `[execPrice_min, execPrice_max]` | **0.70** |
| `WITHHOLDING_TAX` | Alíquota de IR | **Discreta** `{15%, 22.5%}` com pesos `{0.6, 0.4}` | **0.75** |
| `WRONG_TRANSACTION_VALUE` | Valor correto da txn | **Gaussiana** `N(expectedValue, σ²)` | **0.65** |
| `CASH_MISMATCH` (múltiplas txns) | Qual txn é a causa | **Uniforme** sobre candidatas | **0.60** |

**Distribuições detalhadas:**

#### `MISSING_EXECUTION_PRICE`

O sistema usou `PU` como fallback, mas o preço real de execução é desconhecido.

```
execPrice_min = min(PU, formerPU) × (1 − margin)
execPrice_max = max(PU, formerPU) × (1 + margin)

impact = amountDiff × (PU − trueExecPrice)
```

**Parâmetro configurável:** `margin` (default: `0.05` = 5%)

#### `WITHHOLDING_TAX`

Para `brazilianFund`, IR é retido na fonte. A alíquota depende do prazo:

```
alíquotas = {15.0: 0.6, 22.5: 0.4}   // {alíquota%: peso}

impact = transactionBalance × (alíquota / 100)
```

**Parâmetro configurável:** `alíquotas` (mapa alíquota → peso)

#### `WRONG_TRANSACTION_VALUE`

O valor correto é próximo do esperado, mas com erro de medição.

```
impact ~ N(expectedValue − actualBalance, σ²)
σ = |expectedValue| × relative_error
```

**Parâmetro configurável:** `relative_error` (default: `0.02` = 2%)

### Tier 3 — Indicativo (confidence 0.1–0.5)

Correlações sem certeza. Úteis quando nenhum Tier 1/2 fecha o gap.

| Flag | Significado | Confidence default |
|------|------------|-------------------|
| Step 4.3 misclassified | Balance coincide, mas pode ser coincidência | **0.40** |
| Step 6 anomaly | Algo está errado, mas não identifica o quê | **0.20** |
| `WRONG_SECURITY` | Security pode estar errado na txn | **0.30** |

---

## Parâmetros Configuráveis

Todos os parâmetros podem ser ajustados pelo usuário via configuração:

```json
{
  "bayesian": {
    "tolerance": 0.01,
    "gaussian_sigma": 0.001,
    
    "confidence_overrides": {
      "MISSING_TRANSACTION": 0.95,
      "MISSING_PROVISION": 0.90,
      "MISSING_EVENT": 0.85,
      "WRONG_EVENT_BALANCE": 0.85,
      "WRONG_PROVISION_AMOUNT": 0.80,
      "MISSING_EXECUTION_PRICE": 0.70,
      "WITHHOLDING_TAX": 0.75,
      "WRONG_TRANSACTION_VALUE": 0.65,
      "UNCLASSIFIED_TRANSACTION": 0.85,
      "CASH_MISMATCH": 0.90,
      "MISCLASSIFIED": 0.40,
      "ANOMALY": 0.20,
      "WRONG_SECURITY": 0.30
    },

    "exec_price_margin": 0.05,
    
    "withholding_tax_rates": {
      "15.0": 0.6,
      "22.5": 0.4
    },

    "wrong_txn_relative_error": 0.02
  }
}
```

### Onde configurar

O arquivo de configuração será `data/bayesian_config.json`. Se não existir, os defaults acima são usados.

O usuário pode ajustar:

| Parâmetro | Onde | Efeito |
|-----------|------|--------|
| `confidence_overrides` | Por flag | Aumenta/diminui a probabilidade prior de cada tipo de flag |
| `exec_price_margin` | Global | Amplia/reduz a faixa de busca do execution price |
| `withholding_tax_rates` | Global | Altera as alíquotas e seus pesos |
| `wrong_txn_relative_error` | Global | Controla a largura da gaussiana para valores incorretos |
| `tolerance` | Global | Threshold para considerar o gap como resolvido |
| `gaussian_sigma` | Global | Largura da likelihood (quão "exato" o match precisa ser) |

---

## Algoritmo

### Input

- `gapCash` (R$) — gap atual
- `flags[]` — lista de flags diagnosticados, cada um com `impact` e `flag` type

### Processo

1. Para cada flag, buscar `confidence` e `distribution` do impacto
2. Para Tier 2, amostrar N valores da distribuição (Monte Carlo)
3. Enumerar combinações de flags (power set, limitado a ~10 flags)
4. Para cada combinação:
   - `total_impact = Σ(fix_impact_i)`
   - `residual = gapCash − total_impact`
   - `likelihood = exp(−residual² / (2 × σ²))`
   - `prior = Π(confidence_i) × Π(1 − confidence_j)` para j fora do set
   - `posterior ∝ likelihood × prior`
5. Normalizar posteriors
6. Retornar top-K combinações ordenadas por posterior

### Output

```json
{
  "bestFix": {
    "flags": ["MISSING_TRANSACTION", "WITHHOLDING_TAX"],
    "totalImpact": 5230.50,
    "residualGap": 0.02,
    "confidence": 0.87,
    "gapResolved": true
  },
  "alternatives": [...],
  "cashResolved": true
}
```

---

## Validação (integração com Recálculo)

Após encontrar o `bestFix`:

1. Aplicar as correções do `bestFix` como transações/provisões simuladas
2. Executar o recálculo completo (fórmulas 1–13 do `CONCILIACAO_RECALCULO.md`)
3. Verificar:
   - `gapPct ≈ 0` → gap resolvido
   - `projectedCash == currentCash` → caixa consistente
4. Se validação falha → tentar próxima alternativa
