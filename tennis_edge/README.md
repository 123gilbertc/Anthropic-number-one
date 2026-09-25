# tennis_edge — Tennis model + Polymarket value scanner

A tool that tells you **when a Polymarket tennis price is wrong by enough to bet on, and how much to bet**.
It is *not* a crystal ball. No tennis model predicts winners much better than ~70% of the time,
and the betting market already runs at about that level. You don't win here by picking more winners.
You win by buying outcomes **priced below their true probability**, sizing the bet correctly, and
skipping everything else.

## How it works

| Layer | What it does | Why |
|---|---|---|
| **Surface Elo** (`elo.py`) | Rates every ATP/WTA player from match history. Blends overall and surface ratings, adjusts for best-of-5, and pulls inactive players back toward average | Elo is the strongest *simple* tennis predictor in published comparisons |
| **Market blend** (`betting.shrink`) | `p = w·model + (1−w)·market` | Markets are sharp. Trusting the model 100% loses money. `backtest --tune` finds the best `w` |
| **Edge filter** | Bet only when `p − ask price ≥ min_edge` (default 4¢) | Small edges disappear into the spread and model error |
| **Fractional Kelly** | Stake = ¼ Kelly, capped at 2% of bankroll | Full Kelly on noisy probabilities wipes out bankrolls |
| **Walk-forward backtest** (`backtest.py`) | Replays years of matches against **Pinnacle closing odds**, never using future data | The honest test: if it can't beat Pinnacle, it can't beat Polymarket |
| **Scanner** (`polymarket.py`) | Pulls live singles match-winner (`moneyline`) markets and their ask prices from Polymarket's public API. It skips set/game/handicap props, doubles and matches already in progress, and never places orders | Finds the actual price you'd pay |
| **Journal** (`scan --log`) | Writes every pick to `bets_journal.csv` | Track closing-line value (CLV). Beating the closing price is the best early sign of a real edge |

## Setup

```bash
cd tennis_edge
pip install -r requirements.txt

# 1) ATP results 2015-now (free, Sackmann format, via TML-Database)
python -m tennis_edge download

# 2) WTA + current-season results + closing odds (free): open
#    http://www.tennis-data.co.uk/alldata.php and save the yearly .xlsx files for
#    ATP (e.g. 2024, 2025, 2026) and WTA (the "w" links) into ./data/
#    Name the WTA ones differently, e.g. data/wta_2026.xlsx, so they don't overwrite the ATP files.
```

The scanner warns you if your newest result is more than 14 days old. Refresh `./data` weekly.

## Tonight's run (the daily routine)

```bash
# every rated match starting in the next 24h, plus any bets that clear the edge after fees
python -m tennis_edge scan --bankroll 500 --hours 24 --all --log
```

The output shows each player's model probability vs the Polymarket ask, then a pick list with
**edge after Polymarket's taker fee** (sports fee = 5% × p × (1−p) per share) and a stake.
Players with fewer than 30 rated matches (most ITF events) are skipped rather than guessed.

If your network blocks the Polymarket API, save the JSON from
`https://gamma-api.polymarket.com/events?tag_slug=tennis&active=true&closed=false&limit=100&offset=0`
(and `offset=100`, `200` ...) in a browser, then run `scan --events-json page1.json page2.json`.

## Other commands

```bash
python -m tennis_edge ratings --surface clay
python -m tennis_edge predict "Jannik Sinner" "Carlos Alcaraz" --surface grass --best-of 5
python -m tennis_edge price "Sinner" "Alcaraz" 0.47 0.55 --surface grass --best-of 5
python -m tennis_edge backtest --data "data/*.xlsx" --start 2024-01-01 --tune   # needs tennis-data odds
```

Main settings: `--w-model` (trust in the model vs the market, default 0.3), `--min-edge` (default 4¢
after fees), `--kelly` (default ¼), `--max-bet` (default 2% of bankroll), `--min-liquidity`, `--fee-rate`.

## Recommended process

1. **Backtest.** Run `backtest --tune` on 5+ years of odds data. If the model's log loss doesn't beat
   the market's at your chosen `w`, the edge doesn't exist yet. Don't bet real money.
2. **Paper trade for 2–4 weeks** with `scan --log`. After each match, fill in `closing_price` and `result`.
   If your entry prices are consistently *better* than the closing prices, you likely have an edge.
3. **Go live small.** Keep ¼ Kelly and the 2% cap. Expect losing streaks of 10+ bets even with a real edge.
4. **Where edges actually show up:** early prices on thin markets (Challengers, early rounds, WTA),
   right after a draw is released, and injury or withdrawal news the market hasn't priced in yet.
   Big-name finals are the most efficient markets. Your edge there is roughly zero.

## Ideas to improve the model

The biggest known gains over Elo come from:
- serve/return points-won stats (Sackmann files include them)
- fatigue: minutes played in the last 3–7 days
- head-to-head history and handedness
- altitude and court speed
- live in-play pricing from point-by-point win probabilities

Add each one as a feature, and keep it only if it improves backtest log loss.

## Honest limits

- Polymarket tennis prices track the sharp sportsbooks closely. A pure Elo model is **less** accurate
  than the market. The synthetic tests prove this: against a perfectly priced market the system loses
  slightly, which is exactly what should happen.
- The scanner guesses surface and best-of-5 from the event title. Use `--surface` and `--best-of`
  to override when it's unclear.
- Name matching uses "surname + first initial". Check any pick where two players share both.
- Check that prediction markets and sports event contracts are legal where you live.
  Only bet money you can afford to lose.

## Tests

```bash
python -m pytest -q tests
```
