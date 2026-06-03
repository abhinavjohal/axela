# ZN Sep26 on Trading Technologies — Debug Checklist

Use this before Week 1 (200 lots). One wrong setting destroys edge faster than a bad signal.

## 1. Instrument & contract

- [ ] Security Search: **ZN Sep 2026** (U26) — not ZN Sep25 roll, not ZF/ZB.
- [ ] Order ticket shows tick increment **1/64** (half of 32nd).
- [ ] PnL currency USD; multiplier **$1,000/point**.
- [ ] Competition allows overnight — confirm **GTD/GTC** and session auto-liquidation is **off** unless you want it.

## 2. Market data

- [ ] CME Globex depth enabled (at least 5–10 levels for imbalance features).
- [ ] Clock sync: TT workstation NTP; log timestamps in **CT** and **IST** (competition starts 03:30 IST).
- [ ] Record **Market Stats** / Time & Sales export path for post-session `backtest_skeleton.py`.

## 3. Order types & fees

- [ ] Limit vs market: default **limit** for MR/MM sleeves; market only macro post-release.
- [ ] Fee model in sim: **$0.50 per lot per side** ($1.00 round-turn) — code uses per-leg counting; confirm organizer matches.
- [ ] Volume counter: confirm whether **partial fills** sum to lots the same way as full fills.

## 4. Risk on TT

- [ ] Max position **10** — set TT Risk Guardian hard cap.
- [ ] Daily loss stop (suggest $1,500–$2,500 Week 1 while debugging).
- [ ] Disable trading 2 min before/after major releases until macro sleeve is tested.

## 5. Strategy deployment order

1. **Paper / sim** with live MD — 2 sessions minimum.
2. **Session MR** only — measure hit rate vs fee breakeven (~0.03 ticks/lot average at scale).
3. Add **macro post-release** with halved size.
4. Use **volume-aware MM** only in last 20% of week if below min lots.

## 6. What to log every session (for alpha debug)

| Field | Why |
|-------|-----|
| signal reason | Attribution |
| mid at signal / fill | Slippage |
| position after | Inventory risk |
| event_tag | Macro vs non-macro PnL |
| lots cumulative | Volume compliance |

## 7. Week 1 volume plan (200 lots)

Example split that preserves ZN edge:

- **120–150 lots ZN**: high-conviction sessions (US morning + macro).
- **50–80 lots other micros** (MES/MBT): scratch volume if ZN vol too high — lower tick value but still $0.50/lot fee.

Run: `python -m zn_competition.economics`
