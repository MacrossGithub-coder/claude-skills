---
name: backtrader-multi
description: Run multi-strategy quantitative backtests on a stock ticker over a date range, then generate a shareable markdown analysis report covering strategy ranking, risk flags, and a deployment recommendation. Use this skill whenever the user asks to backtest, evaluate, or compare trading strategies for a specific stock — including phrasings like "backtrader-multi RKLB 2025-01-01 2026-01-01", "回测 RKLB", "对 AAPL 做策略回测", "test these MA/RSI strategies on TSLA", "compare strategies for NVDA", or any request that mentions a ticker plus a date range plus the words backtest / strategy / quant / 量化. Make sure to use this skill even if the user does not explicitly say "backtrader-multi" — any backtest request involving an equity ticker should trigger it.
---

# backtrader-multi

This skill runs the bundled multi-strategy backtest engine (MA + RSI variants over `bt.Cerebro`) on a single ticker, then synthesises the raw output (CSV / PNG / QuantStats HTML) into a shareable markdown report. The Python project is bundled inside the skill — users only need a working `python3` (≥ 3.9) on PATH.

## Trigger forms

Any of these should fire the skill:

- Shorthand: `backtrader-multi <TICKER> <START> <END>` (e.g. `backtrader-multi RKLB 2025-01-01 2026-01-01`)
- Chinese: `回测 RKLB`, `对 AAPL 做回测 2024 全年`, `跑一下 NVDA 的策略`
- English: `backtest TSLA last year`, `compare strategies for NVDA from 2024-01 to 2024-12`

If the ticker is unambiguous but the dates are missing, ask the user **once** for the date range. Do not invent dates.

## Network requirement (read this before running)

The bundled `data_loader.py` downloads data from Yahoo Finance via `yfinance`. **Yahoo Finance is blocked from mainland China IPs since November 2021.** When that happens the run fails with `YFRateLimitError('Too Many Requests')` even though the cause is geographic blocking, not a real rate limit.

If the run fails with that error, do **not** retry, do **not** add a proxy automatically. Tell the user (matching their language):

> The run failed because Yahoo Finance returned a geographic-block response. Set `HTTPS_PROXY=http://<host>:<port>` and re-run, or switch your proxy tool (Surge / ClashX / V2Ray) into global / TUN mode for the duration of the backtest. The skill will not bypass this on its own.

This is intentional. Network configuration is the user's responsibility.

## Workflow

### Step 0 — Resolve the skill installation directory

The skill can live in any of these layouts depending on how it was installed:

- **User-level install:** `~/.claude/skills/backtrader-multi/`
- **Marketplace install:** `~/.claude/plugins/marketplaces/<marketplace>/backtrader-multi/` (no `skills/` segment — the skill sits at the marketplace repo root)
- **Plugin cache:** `~/.claude/plugins/cache/<plugin>/<version>/skills/backtrader-multi/`

Do not hard-code any of these. Probe them in order, preferring the user-level path, before anything else:

```bash
if [[ -f "$HOME/.claude/skills/backtrader-multi/SKILL.md" ]]; then
  SKILL_DIR="$HOME/.claude/skills/backtrader-multi"
else
  _MATCH="$(find "$HOME/.claude/plugins" -maxdepth 8 -type f \
    \( -path '*/skills/backtrader-multi/SKILL.md' -o -path '*/backtrader-multi/SKILL.md' \) \
    2>/dev/null | head -1)"
  SKILL_DIR="${_MATCH:+$(dirname "$_MATCH")}"
fi
[[ -z "${SKILL_DIR:-}" || ! -d "$SKILL_DIR" ]] && { echo "backtrader-multi skill not found"; exit 1; }
```

The user-level path check is an O(1) stat — when it hits (typical case) we skip the `find` entirely. The `find` fallback covers both marketplace-root and `skills/`-nested layouts in a single pass, capped at depth 8 to stay fast on systems with many installed plugins. All subsequent commands reference `$SKILL_DIR/...`.

### Step 1 — Run the backtest

From the user's current working directory, invoke the bundled wrapper:

```bash
bash "$SKILL_DIR/scripts/run.sh" <TICKER> <START> <END>
```

The first invocation creates `$SKILL_DIR/.venv/` and pip-installs `yfinance / backtrader / quantstats / pandas / matplotlib / PyYAML`. Expect 1–2 minutes of wait on the first run; subsequent runs reuse the venv and start instantly.

The wrapper prints `OUTPUT_DIR=<absolute-path>` and `RUN_DATE=<YYYYMMDD>` on its last two lines on success. **Parse and capture those values from the stdout** — the rest of the workflow depends on them. `OUTPUT_DIR` already points to `<caller-cwd>/output/<TICKER>`, so its parent is the `--output-root` for Step 2 and its contents are the artefacts to summarise in Step 3. Do not recompute these from `$PWD` — Bash tool calls may not always run in the same working directory, and the wrapper's printed value is the ground truth.

If the wrapper exits non-zero, inspect stderr:
- `YFRateLimitError` / `No data available` → network / proxy issue (see section above)
- `ModuleNotFoundError` after a fresh install → likely a stale venv. Tell the user to `rm -rf "$SKILL_DIR/.venv"` and re-run.
- Anything else → surface the error verbatim, do not guess.

### Step 2 — Parse the artefacts

Using the `OUTPUT_DIR` value captured from Step 1:

```bash
"$SKILL_DIR/.venv/bin/python" \
  "$SKILL_DIR/scripts/analyze_results.py" \
  --ticker <TICKER> \
  --output-root "$(dirname "$OUTPUT_DIR")"
```

`$(dirname "$OUTPUT_DIR")` resolves to `<caller-cwd>/output` regardless of where Bash thinks the CWD currently is. The script prints a JSON object with these fields:

- `ticker`, `run_date`, `config_period`, `data_window`, `seed`, `n_strategies`
- `selected_names` — strategies the engine intended to run, parsed from the log's `Selected N strategies` line
- `missing_strategies` — names that appear in `selected_names` but have no `Done` line; non-empty when a strategy errored mid-run. Surface this in the report if it is non-empty.
- `strategies_ranked` — array sorted by Sharpe descending; each item has `name`, `total_return_pct`, `max_drawdown_pct`, `sharpe`, `trades`, and `flags`. Strategies with `Sharpe=nan` / `inf` are normalised to `sharpe: null` and tagged with the `sharpe_unavailable` flag.
- `best_strategy` — top-ranked strategy name (or `null` when no strategy completed)
- `artifacts` — absolute paths to the run log, CSV, comparison chart, and HTML reports

Note: the analyser ignores log entries from any earlier same-day run by scoping its parsing to text after the **last** `Starting | ticker=...` block. You will not see stale strategies from an earlier run leak into the JSON.

### Step 3 — Write the shareable report (English + 中文 双语)

Generate **two** report files: an English version and a Chinese version. They cover the same content but each is written natively in its target language — do not translate word-for-word.

Templates (read with the `Read` tool):
- English: `$SKILL_DIR/references/report_template.md`
- 中文: `$SKILL_DIR/references/report_template_zh.md`

Quality bar — these are meant to be **shared with someone who was not in the room**, so:

- Use **complete sentences**. No log-style abbreviations like "DD high, sharpe ok" or "回撤大、夏普一般".
- Translate technical flags into plain language using the mapping in each template's Risk Flags / 风险标记 section.
- The Recommendation / 部署建议 section must name a single strategy and justify it in one paragraph. If no strategy is deployable, say so explicitly and suggest a concrete next step (widen parameter search, extend window / 调整参数搜索范围、扩大回测窗口).
- Format Sharpe to 3 decimals; percentages to 2 decimals; trade win rates as percentages.
- Strategy names go verbatim — do not paraphrase `MaRsiStrategy_Aggressive` to "the aggressive one" or "激进策略". The class name is the identifier.

Save **three** outputs:

1. `<CWD>/output/<TICKER>/<RUN_DATE>_summary.md` — English version, alongside the other artefacts.
2. `<CWD>/output/<TICKER>/<RUN_DATE>_summary_zh.md` — Chinese version, same directory.
3. Inline in the conversation — paste the version that matches the language the user is currently using. If they are mixing languages, default to Chinese (中文). Mention that the other-language version has been saved alongside.

## Modifying parameters other than ticker / start / end

The CLI shorthand only accepts those three. Everything else (random `seed`, `n_combos`, the strategy pool, `cash`, `commission`) lives in the bundled `config.yaml`. To change them, edit:

```
$SKILL_DIR/project/config.yaml
```

(For the user's reference, expand `$SKILL_DIR` to the absolute path resolved in Step 0 before mentioning it — they will not have the variable set in their own shell.)

The change persists until edited again. Tell the user this is the location if they ask to tune anything beyond the three CLI args.

## Files in this skill (paths relative to `$SKILL_DIR`)

- `scripts/run.sh` — venv bootstrap + wrapper around `run_backtest.py` + relocates output to caller's CWD
- `scripts/analyze_results.py` — parses `<RUN_DATE>_run.log` + `<RUN_DATE>_trades.csv` into JSON
- `project/` — verbatim copy of the multi-strategy engine; do not edit unless intentionally maintaining the strategy code
- `requirements.txt` — pinned dependency list used by the venv bootstrap
- `references/report_template.md` — English markdown skeleton for the shareable summary
- `references/report_template_zh.md` — 中文 markdown 报告骨架（与英文版结构对应）
