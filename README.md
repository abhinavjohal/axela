# Axela — ZN Sep26 Competition Framework

Research and strategy scaffolding for the **ZN Sep 2026** (10-Year T-Note) futures trading competition on **Trading Technologies (TT)**.

**Goal:** Maximize net PnL after fees while meeting weekly volume minimums (Week 1: 200 lots; full competition: 2,000 lots total).

## Repository layout

```
zn_competition/
  PLAYBOOK.md              # Strategy, sessions, multi-instrument volume plan
  TT_DEBUG_CHECKLIST.md    # TT setup verification before live
  specs.py                 # Contract economics (tick, multiplier, windows)
  economics.py             # Fee drag & breakeven scenarios
  risk.py                  # Position caps & daily stop helpers
  backtest_skeleton.py     # Replay TT-exported tick/bar CSV
  strategies/
    macro_event.py         # Post-release macro sleeve
    session_mr.py            # Session VWAP mean reversion
    volume_aware_mm.py       # Volume pad (last resort)
```

## Quick start

```bash
python3 -m zn_competition.economics
```

After exporting session data from TT (columns: `timestamp`, `bid`, `ask`, `mid`, optional `vwap_z`):

```bash
python3 -c "
from pathlib import Path
from zn_competition.backtest_skeleton import load_bars, run_paper
print(run_paper(load_bars(Path('data/your_session.csv')), week=1))
"
```

Place CSVs under `data/` (gitignored).

## Competition rules (summary)

| Constraint | Value |
|------------|--------|
| Instrument | ZN Sep26 (+ MBT, MCL, MES, MGC micros) |
| Max position | 10 lots per instrument |
| Fee | $0.50 per lot |
| Week 1 volume | ≥ 200 lots |
| Total volume | ≥ 2,000 lots over 4 weeks |
| Period | 1 Jun 2026 03:30 IST → 27 Jun 2026 03:30 IST |

## Feedback for AI reviewers

When asking another AI tool for feedback, point it at:

1. **`zn_competition/PLAYBOOK.md`** — strategic assumptions and session calendar
2. **`zn_competition/strategies/`** — signal logic and priority stack
3. **`zn_competition/economics.py`** — fee breakeven math
4. **`zn_competition/backtest_skeleton.py`** — fill model limitations

**Open questions worth reviewing:**

- Lot counting: does the organizer count each leg or round-turn?
- Optimal Week 1 split between ZN alpha vs MES/MBT volume filler
- Parameter defaults (`entry_z`, macro size, daily loss stop)
- Feature pipeline: `vwap_z`, `book_imbalance`, macro surprise feed

## Connect remote (GitHub / GitLab)

```bash
git remote add origin git@github.com:YOUR_USER/axela.git
git push -u origin main
```

Then share the repo URL with Codex, Claude, or other agents for PR-style review.

## License

Private competition research — add a license if you open-source.
