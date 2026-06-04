# ZN Sep26 Competition Playbook (Max PnL, Volume-Constrained)

## Objective function

Maximize **net PnL** after **$0.50/lot** fees, subject to:

- Week 1 ≥ **200** lots (you are here)
- Weeks 2–4: 300 / 400 / 500
- Total ≥ **2,000** lots
- **≤ 10 lots** position per instrument
- Hold overnight allowed — treat carry as risk, not free optionality

**Do not** chase volume with market orders in quiet hours; fee drag is linear, edge is not.

---

## ZN microstructure (where pros actually trade)

| Regime | Behavior | Sleeve |
|--------|----------|--------|
| US 7:20–10:00 CT | Auction digestion, curve flows | Session MR, small breakout |
| Macro (CPI/NFP/FOMC) | Jump → trend or whip | Macro post-release only |
| 10Y Treasury auction | Tail risk in ZN | Reduce size 50% ±30 min |
| Asia (low liq) | Wider spreads, fake breaks | **No trade** or volume pad only |
| End-of-day | Real-money rebalancing | Trend follow 30–60 min max |

ZN is a **rates beta** instrument: your alpha is timing **yield shocks** and **mean-reversion when shocks are absent**.

---

## Three-sleeve stack (priority)

### Sleeve A — Macro event (primary alpha)

- Trade **after** print, in direction of surprise vs consensus (see `macro_event.py`).
- Size: 2–4 lots; hard stop in ticks (e.g. 3–4 ticks).
- Week 1 might only need 4–6 events × 2 round trips × 3 lots ≈ 36–72 lots — high edge per lot.

### Sleeve B — Session mean reversion (workhorse)

- Fade extensions from session VWAP when `|z| > 1.25`, flat near `|z| < 0.35`.
- **Off** during blocked events (FOMC, NFP, CPI, auctions).
- Target hold &lt; 15 min; passive entry, aggressive exit.

### Master Plan — Alpha Sniper + Module 4 Volume Churner (production)

Two **independent** TT paths (see `TT_ADL_SPECIFICATION.md` §8.0, `alpha_volume_platform.py`):

| Engine | Role | Rule |
|--------|------|------|
| **Alpha** | `SniperOBIEngine` @ **0.85** (or 0.75) | OBI directional sniper **24/7** — never lower threshold to force volume |
| **Volume** | `VolumeChurner` @ **30s** generator | When **flat (0)**, arm 1-lot bid + 1-lot ask; scratch ≈ **$1.00 RT** for **2 legs** |

- OBI has priority: churn is blocked while alpha has open trade or resting entry.
- Do **not** use OBI 0.55–0.65 for volume — that destroys sniper edge.
- Budget churn fees from alpha P&L (`expected_scratch_cost_usd` per cycle).

Backtest: `python3 -m zn_competition.backtest` (default platform mode).

---

## Fee math (internalize)

- 200 lots × $0.50 = **$100** fees Week 1 minimum path.
- 2000 lots full comp ≈ **$1,000** fees if all counted on ZN.
- Breakeven ≈ **0.032 ticks per lot** on average — trivial if you trade rarely with edge; deadly if you churn.

**1 tick = $15.625** per lot. **Round-turn fee = $1.00/lot** (0.064 ticks). One good macro trade (+3 ticks) on 3 lots ≈ $140 gross — covers 140 round-turns in theory.

---

## Week 1 execution calendar (IST-aware)

Competition start: **01 Jun 2026 03:30 IST**.

| IST window | CT (approx) | Action |
|------------|-------------|--------|
| 03:30–06:00 | Evening US | Observe only unless Europe data |
| 18:30–22:30 | 07:00–10:00 CT | Primary ZN session |
| 20:00–21:30 | Macro | Event sleeve |
| 22:30–01:00 | US afternoon | MR + reduce size |

Track **US 10Y auction** dates separately — ZN skews violently on tail stops.

---

## Multi-instrument volume (4 weeks)

Eligible: MBT, MCL, MES, MGC, **ZN**.

- **Alpha focus**: ZN (only standard Treasury in list).
- **Volume filler**: MES/MBT when ZN realized vol &gt; threshold — correlated risk, so cap filler at 30–40% of weekly lots.

---

## Debug loop (daily)

1. Export session CSV from TT.
2. Run attribution: PnL by `reason` tag.
3. Compare avg **slippage ticks** vs `expected_edge_ticks` in signals.
4. If `volume_pad` &gt; 25% of lots and PnL negative → tighten pad rules.
5. Adjust only **one** parameter per day (entry_z, size, or loss stop).

---

## Python in this repo

```bash
cd /Users/abhinavjohal/axela
python -m zn_competition.economics
python -m zn_competition.backtest_skeleton  # after CSV export
```

Next step with live data: wire TT Time & Sales → `vwap_z` and `book_imbalance` in `StrategyContext.extra`.
