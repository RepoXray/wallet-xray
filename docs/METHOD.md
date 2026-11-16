# Method

All amounts are in USD. `dep` = total bought across the scored tokens.

## Replay

For each token, trades are replayed in order:

- **Wallet**: buys and sells at its own prices; tokens still held are marked at the last bar close.
- **Fast copy**: each trade filled at the close of the bar containing `t + 3s`, 1% fee per side.
- **Normal copy**: filled at the close of the next bar (`t + 3s + bar`), 1% fee per side.
- A sell closes the same fraction of the copier's position as of the wallet's.

Bar size per token: 30s if its trades span ≤ 4h, 1m if ≤ 24h, else 5m.

## Checks (0–100, shown as 1–19)

```
roiScore(x) = clamp(50 + 250 * x / dep, 0, 100)
SPD = roiScore(normal copy PnL)
CON = roiScore(normal copy PnL minus the best token)
LCK = roiScore(5th percentile of 2,000 bootstrap resamples of per-token copy PnL)
MEM = roiScore(normal copy PnL on the latest third of tokens)   (needs ≥ 5 tokens)
INT = clamp(100 - 100 * max(sold_into, 1.2 * flip60), 0, 100)
```

`sold_into` = share of sell volume placed into two rising bars in a row. `flip60` = share sold within 60 seconds of the first buy.

## Scores

```
Wallet IQ = 100 + 15 * clamp((wallet PnL / dep) / 0.12, -3, 3.4)
Your IQ   = 100 + 1.1 * (weighted mean of checks - 50)
            weights SPD .40, CON .15, LCK .15, MEM .10, INT .20
            if SPD < 50: Your IQ ≤ 100 - (50 - SPD)
```

## Verdicts

| Verdict | Rule |
|---|---|
| Predator | Wallet IQ ≥ 115, Your IQ < 90, INT < 40 |
| Lucky | Wallet IQ ≥ 115 and CON < 30 or LCK < 30 |
| Mastermind | Your IQ ≥ 140 |
| Sharp | Your IQ ≥ 115 |
| Average | Your IQ ≥ 85 |
| Below average | otherwise |

A wallet needs at least 8 traded tokens in the window to be scored.
