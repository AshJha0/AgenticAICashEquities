# Publishing this documentation with GitHub Pages

The `docs/` folder is Pages-ready: `docs/index.html` is a self-contained static landing page
(no build step, no Jekyll required, no external assets), and every link on it points at
rendered Markdown on GitHub, so nothing else needs generating.

## One-time setup

1. Push the repository to GitHub (`AshJha0/AgenticAICashEquities`). If you use a different
   repository name, update the hard-coded links in `docs/index.html` (search for
   `AshJha0/AgenticAICashEquities`) and in this file.
2. On GitHub: **Settings → Pages → Build and deployment**:
   - Source: *Deploy from a branch*
   - Branch: `main`, folder: `/docs`
3. Save. The site appears at `https://ashjha0.github.io/AgenticAICashEquities/` within a
   minute or two (the first deploy can take a few minutes; the Actions tab shows the
   `pages build and deployment` job).

Because the landing page is plain HTML, Jekyll is irrelevant, but if you ever add files whose
names begin with an underscore, add an empty `docs/.nojekyll` so Pages serves them.

## What gets served

| URL | content |
|---|---|
| `/` | `docs/index.html` — landing page (evidence-labelled results, boundary strip, subsystem cards, what it is not, sample report, quick start) |
| everything else | linked back to rendered Markdown on github.com: `README.md`, `LEARN.md`, `COOKBOOK.md`, `CHANGELOG.md`, `docs/INDEX.md`, `docs/architecture/overview.md`, `docs/ai-agents/ai-agents.md`, `docs/quant/quant.md`, `docs/quant/real-data-and-paper.md`, `docs/research/README.md`, `docs/DIAGRAMS.md`, `docs/threat-model/threat-model.md`, `docs/evaluation/evaluation.md`, `docs/api/api.md`, and the releases page |

Mermaid diagrams in `docs/DIAGRAMS.md` render natively on github.com — no plugin needed.

## Keeping the landing page honest

`docs/index.html` quotes measured numbers, each with its source in an HTML comment and its
evidence class (mock LLM, synthetic, VOID, NOT RUN LIVE). It deliberately gives no hard test count;
CI is the source for that. `scripts/check_docs.py` does not parse the HTML page, so re-check it by
hand at every release against:

- `docs/evaluation/latest.json` (the committed `ceap evaluation-report --json` output) — accuracy, coverage, false-positive rate, research verdicts, golden-set results and sign-off,
- `docs/research/result_momentum_2026-11.md` and `result_momentum_2026-11-v2.md` — R5 coverage figures,
- `config/paper.json` — paper feed and forward start,
- `docs/evaluation/local_model/qwen2.5_7b-instruct.json` and `judge_calibration.json` — local-model numbers (0.9.0),
- `docs/research/e1/result_holdout.json` — the E1 holdout t-statistic (0.9.0),
- `python -c "from ceap.mcp.registry import build_default_servers as b; s=b(); print(len(s), sum(len(v.list_tools()) for v in s.values()))"` — server count and tool count,
- `python -c "from ceap.data import DatasetStore; print(DatasetStore().get('T01').summary())"` — dataset sizes,
- `python -c "from ceap.data.historical import HistoricalStore; print(HistoricalStore().get('R01').summary())"` — research dataset sizes.

A wrong number on the landing page is a documentation bug — treat it like one.
