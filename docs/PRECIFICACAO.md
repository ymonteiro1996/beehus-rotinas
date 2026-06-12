# Precificacao — Calculation Reference

## Overview

The Precificacao page calculates projected PU (unit price) for fixed-income securities using three methods:

1. **Pos-fixado** — post-fixed, benchmark-linked
2. **Pre-fixado Curva (HTM)** — pre-fixed, held-to-maturity
3. **Inflacao Curva (HTM)** — inflation-linked, held-to-maturity

All methods start from the **PU of the security in the wallet position** (`processedPosition`) and project forward using a benchmark calendar.

---

## 1. Pos-fixado (%BM)

Tracks a benchmark index (e.g. CDI) with an indexer percentage.

### Formula

```
rent_BM(t) = PU_BM(t) / PU_BM(t-1) - 1
PU(t)      = PU(t-1) x (1 + rent_BM(t) x indexerPercentual / 100)
```

### Parameters

| Parameter           | Source                              | Description                          |
|---------------------|-------------------------------------|--------------------------------------|
| `PU inicial`        | `processedPosition.securities[].pu` | PU from the wallet position          |
| `Data inicial`      | `processedPosition.positionDate`    | Position date of the wallet          |
| `benchmarkId`       | User selection                      | Which benchmark to track             |
| `indexerPercentual` | User input (or from security)       | % of benchmark to apply (e.g. 98%)  |

### Calculation steps

1. Fetch PU and date from the wallet's most recent `processedPosition`
2. Fetch the benchmark's price history from `securityPrices`
3. Build a daily factor map: `factor(t) = price(t) / price(t-1)`
4. Starting from `PU`, roll forward day by day (skipping the position date):
   - `PU(t) = PU(t-1) x (1 + factor(t) x indexerPercentual / 100)`

### Result columns

| Column        | Description                                             |
|---------------|---------------------------------------------------------|
| Fator Diario  | `1 + rent_BM(t)` — daily benchmark factor               |
| Yield BM      | —                                                       |
| PU Calculado  | Projected PU for the day                                |

---

## 2. Pre-fixado Curva (HTM)

Held-to-maturity calculation using a **weighted average yield** from buy transactions.

### Formula

```
weighted_yield = SUM(qty_i x yield_i) / SUM(qty_i)
daily_factor   = (1 + weighted_yield / 100) ^ (1/252)
PU(t)          = PU_posicao x daily_factor ^ n
```

Where `n` = number of business days from position date to date `t`.

### Parameters

| Parameter      | Source                              | Description                                     |
|----------------|-------------------------------------|-------------------------------------------------|
| `PU inicial`   | `processedPosition.securities[].pu` | PU from the wallet position                     |
| `Data inicial`  | `processedPosition.positionDate`   | Position date of the wallet                     |
| `transactions` | User input / `transactions` DB      | List of buys, each with `quantity` and `yield`  |

### Transaction input

Each transaction row has:
- **Date** — date of the buy (display only, not used in the calculation)
- **Quantity** — number of units bought
- **Yield (% a.a.)** — annual yield at purchase

Only quantity and yield are used in the calculation. Date is kept for control/traceability. Transactions can be loaded from the DB (collection `transactions`, filtered by `beehusTransactionType` = buySell or securityTransfer).

### Calculation steps

1. Parse transactions and filter valid rows (quantity > 0, yield not null)
2. Compute the weighted average yield across all transactions
3. Derive a single daily accrual factor: `(1 + weighted_yield / 100) ^ (1/252)`
4. Determine the starting PU (priority: posPU/positionDate > initialPU > securityPrices)
5. For each calendar date after positionDate:
   - `n` = business days from positionDate
   - `PU(t) = PU_posicao x daily_factor ^ n`

### Result columns

| Column        | Description                                                        |
|---------------|--------------------------------------------------------------------|
| Fator Diario  | `daily_factor` — the yield daily factor (constant)                  |
| Yield BM      | Annualized benchmark rentability: `((1 + rent_BM)^252 - 1) x 100` |
| PU Calculado  | Projected PU for the day                                           |

---

## 3. Inflacao Curva (HTM)

Same logic as Pre-fixado, but incorporates the daily variation of an inflation benchmark (e.g. IPCA) accumulated on top of the yield accrual.

### Formula

```
weighted_yield = SUM(qty_i x yield_i) / SUM(qty_i)
daily_factor   = (1 + weighted_yield / 100) ^ (1/252)
accum_BM(t)    = PROD(1 + rent_BM(d))   for d from positionDate+1 to t
PU(t)          = PU_posicao x accum_BM(t) x daily_factor ^ n
```

### Parameters

Same as Pre-fixado Curva, plus:

| Parameter          | Source         | Description                           |
|--------------------|----------------|---------------------------------------|
| `benchmarkId`      | User selection | Inflation benchmark (default: IPCA)   |

### Calculation steps

1-4. Same as Pre-fixado Curva
5. For each calendar date after positionDate:
   - Accumulate benchmark: `accum_BM *= (1 + rent_BM(t))`
   - `n` = business days from positionDate
   - `PU(t) = PU_posicao x accum_BM x daily_factor ^ n`

### Result columns

| Column        | Description                                                        |
|---------------|--------------------------------------------------------------------|
| Fator Diario  | `daily_factor x (1 + rent_BM(t))` — combined daily factor          |
| Yield BM      | Annualized benchmark rentability: `((1 + rent_BM)^252 - 1) x 100` |
| PU Calculado  | Projected PU for the day                                           |

---

## PU Priority

When determining the starting PU for HTM calculations:

1. **posPU / positionDate** — from the wallet's `processedPosition` (default)
2. **initialPU / initialPUDate** — user-supplied override
3. **securityPrices** — last available PU from the prices collection (fallback)

---

## Data sources

| Collection          | Usage                                                              |
|---------------------|--------------------------------------------------------------------|
| `securities`        | Security metadata (name, type, indexer, yield, etc.)               |
| `securityPrices`    | Historical PU values (benchmark data, fallback for lastPU)         |
| `processedPosition` | Wallet positions — primary source for PU, quantity, pricingType    |
| `transactions`      | Buy transactions (quantity, yield per lot, loaded via modal)       |
| `wallets`           | Wallet definitions                                                 |
| `companies`         | Company definitions (filtered by Configuracoes)                    |

## Constants

- **252** — business days per year (Brazilian convention for yield annualization)
