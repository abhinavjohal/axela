# Axela — ZN Sep26 Competition Framework

Production-oriented **stdlib-only** core for **ZN Sep 2026** on **Trading Technologies (TT)**.

## Hard constants (all modules)

| Parameter | Value |
|-----------|--------|
| Tick size | 1/64 point (0.015625) |
| Tick value | **$15.625** / contract |
| Fee | **$0.50** / lot / side |
| Round-turn fee | **$1.00** / lot |
| Max position | **10** lots |

## Layout

```
zn_competition/
  specs.py              # Contract + competition constants
  economics.py          # Fee drag, breakeven ticks, volume plans
  microstructure.py     # Quote, spread, book imbalance
  features.py           # Streaming VWAP, vwap_z, vol (ticks)
  risk.py               # Position ledger, caps, daily stop
  execution.py          # Passive/aggressive fills on tick grid
  backtest.py           # CSV + synthetic backtest engine
  strategies/           # Macro, session MR, volume pad + stack
  tests/test_core.py    # unittest (stdlib)
```

## Commands

```bash
python3 -m unittest discover -s zn_competition/tests -v
python3 -m zn_competition.economics
python3 -m zn_competition.backtest
python3 -m zn_competition.backtest /path/to/tt_quotes.csv 1
```

## TT CSV format

Required: `timestamp`, `bid`, `ask`  
Optional: `bid_size`, `ask_size`, `last`, `volume`, `event_tag`, `event_phase`, `surprise_10y_equiv_bp`

## Remote

https://github.com/abhinavjohal/axela
