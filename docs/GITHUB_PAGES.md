# Publishing this documentation with GitHub Pages

The `docs/` folder is Pages-ready: `docs/index.html` is a self-contained static landing page
(no build step, no Jekyll required, no external assets) in the same style as the
intraday-alpha-platform and Quant-Finance-Library sites, and every link on it points at
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
| `/` | `docs/index.html` — landing page (numbers block, boundary strip, subsystem cards, sample report, quick start) |
| everything else | linked back to rendered Markdown on github.com: `LEARN.md`, `COOKBOOK.md`, `docs/architecture/overview.md`, `docs/DIAGRAMS.md`, `docs/SPECIFICATION.md`, `docs/threat-model/threat-model.md`, `docs/evaluation/evaluation.md`, `docs/api/api.md` |

Mermaid diagrams in `docs/DIAGRAMS.md` render natively on github.com — no plugin needed.

## Keeping the landing page honest

`docs/index.html` quotes real measured numbers. If you change the code, re-check the numbers
block against:

- `pytest` — total and per-suite test counts (`pytest --collect-only -q`),
- `ceap evaluate --suite all` — primary/verdict accuracy, coverage, false-positive/flag rate, mean duration, for both the 50-scenario execution suite and the 21-scenario research suite,
- `python -c "from ceap.mcp.registry import build_default_servers as b; s=b(); print(len(s), sum(len(v.list_tools()) for v in s.values()))"` — server count and tool count,
- `python -c "from ceap.data import DatasetStore; print(DatasetStore().get('T01').summary())"` — dataset sizes,
- `python -c "from ceap.data.historical import HistoricalStore; print(HistoricalStore().get('R01').summary())"` — research dataset sizes,
- `python -c "from ceap.rag.retrieval import build_knowledge_base as k; kb=k(); print(len(kb.documents), len(kb))"` — knowledge corpus.

A wrong number on the landing page is a documentation bug — treat it like one.
