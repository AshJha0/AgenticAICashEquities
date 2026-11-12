# Reviewer D: docs, diagrams and tutorials against the code and the committed results

Reviewed at tag v0.9.0 (`dd6bf3c`), branch `review-2026-11-12`, 2026-11-12. Read-only; this file is
the only thing written.

## 1. Scope covered, and what I did not get to

Read in full: `README.md`, `LEARN.md`, `CHANGELOG.md` 0.9.0 section and the head of 0.8.0,
`docs/evaluation/evaluation.md`, `docs/evaluation/local_model/README.md`,
`docs/ai-agents/rag-hybrid-0.9.0.md`, `docs/research/e1/README.md`, `docs/quant/quant-errata-0.9.0.md`,
`docs/review/2026-11-09/PLAN.md`, `docs/INDEX.md`, `docs/GITHUB_PAGES.md`, `docs/index.html` (body),
`docs/exercises.md`, `docs/glossary.md`, `docs/learn/ai/01`, `06`, `07` and the first half of `02`,
`scripts/check_docs.py`, `harness/state_machine.py`.

Checked by machine over every tracked `.md` (outside `docs/review`) and `docs/index.html`:

- every result JSON flattened and compared with the prose (`latest.json`, the three local-model
  files, `rag-hybrid-0.9.0.json`, `e1/result_holdout.json`, `null_sweep_per_seed.json`);
- every `python -m ceap.*` module, `scripts/*` path, backticked path, relative link and anchor
  (0 broken anchors; 3 broken paths, listed below);
- every inline and fenced `ceap ...` command parsed with the real `build_parser()` (41 distinct
  inline commands: all parse, apart from deliberate abbreviations);
- every documented endpoint against `docs/api/openapi.json` (all present);
- argparse flags of the `python -m` runners; role capabilities behind the curl recipes;
- the package-import diagram against a grep of module-level imports; the state diagram against
  `TRANSITIONS`; the tool count (8 servers, 39 tools: confirmed by `build_default_servers()`).

Ran: `scripts/check_docs.py` (0 problems in 51 files), `scripts/check_banned_words.py` (clean),
the quickstart `ceap investigate ... --dataset T06` once (67 s on this loaded machine), the COOKBOOK
recipe 50 snippet, exercise 1, the `wilson` example and the 1980-session formula.

Not covered, or only sampled by grep: `COOKBOOK.md` recipes 8-22 and 27-48 line by line (commands
parsed, prose not read); `docs/SPECIFICATION.md`; `docs/architecture/overview.md` (grep only);
`docs/ai-agents/ai-agents.md` (two sections read); `docs/execution/execution.md` (one table read);
`docs/ml/*`; `docs/threat-model/*`; `docs/adr/*`; `docs/api/api.md` prose; `docs/research/README.md`
and the R5 result pages (headline coverage figures only); `docs/learn/ai/03`-`05`;
`docs/review/2026-11-07/*`; CHANGELOG sections older than 0.8.0 except 0.3.1. The test count (1326)
and coverage (93 %) could not be reproduced: `pytest --collect-only` did not finish in 170 s here
and running the suite is out of bounds. I did not execute any long command, Ollama, or network call.

## 2. Score: 6 / 10 against a top-firm bar

- The headline numbers of 0.9.0 are right. Every figure in the README results block, the local-model
  table, the retrieval comparison, E1 and the null sweep matches its committed file to the digit, and
  nulls and voids are stated without spin. That is unusual and worth a lot.
- The layer beneath is stale. The README's only worked example does not match what the tool prints;
  two COOKBOOK recipes fail as written; LEARN carries a count the evaluation page itself calls
  wrong, a "not done yet" list that 0.9.0 shipped, and three statements that contradict its own
  later sections.
- Two claims on the front page are not true as worded: "500-name universe" for RS01-RS30, and "no
  hosted model was called" beside hosted-model figures quoted in three other documents.
- The earlier plan has no evidence trail (eight reviews absent, one never delivered) and no 0.9.0
  status, though three documents say it has.
- `check_docs.py` guards five landing files and the `ceap` commands only; about 250 numbers in the
  secondary docs, the HTML page, recipes that use curl/docker, and example output are unguarded,
  and that is exactly where the defects are.

## 3. What is good (verified)

- Local-model table: all 13 rows of `docs/evaluation/local_model/README.md:33-47` equal
  `qwen2.5_7b-instruct.json` (k, n, rate, Wilson bounds, per-seed values). Injection table
  `:56-62` equals `qwen2.5_7b-instruct_injection.json`. Judge kappas `:69-71` equal
  `judge_calibration.json`.
- The caveats at `local_model/README.md:82-98` (seeds share cases, replay not live, labels written
  by Claude, Ollama upgraded mid-run, challenger not measured) are the right ones and are placed
  beside the numbers, not in a footnote.
- `docs/ai-agents/rag-hybrid-0.9.0.md:17-23` equals `rag-hybrid-0.9.0.json` on every cell; the
  failed abstention rule is reported with its held-out leak (6/12 against 1/12) at `:44-52`.
- The pre-registration order is real: `d04f19d` (2026-11-09 17:48, rules and held-out critic set)
  precedes `5c35a89` (19:38, the checks).
- E1: `docs/research/e1/README.md:27-31` equals `result_holdout.json` (t 1.16, Sharpe 0.23,
  CI [-0.12, 0.59], in-sample t 1.86, ETF median 0.94, breakeven 38.0). The MDE of 0.49 is
  (1.645 + 0.84) / sqrt(26). The crash during the opening is disclosed at `:20-24`.
- Null sweep: `null_sweep_per_seed.json` has 500 seeds, 17 with a statistic, max 2.386, so 0 at
  3.0 and exactly 15 at 0.40, as `evaluation.md:138-139` says.
- State diagram `docs/DIAGRAMS.md:70-91` matches `harness/state_machine.py:32-50` edge for edge.
- LEARN 18a worked ledger example (`LEARN.md:681-685`) is arithmetically right to the cent; the
  power formula at `:678-679` gives 1979.6, rounded up to 1980 as the test pins.
- `docs/learn/ai/01`, `06`, `07`: plain idea, formal statement, worked example from the repo,
  offline exercise, self-check. Formulas (softmax with temperature, Wilson, Cohen's kappa) are
  correct. This is the best teaching material in the repo.
- Wording: `git grep -i` for the banned word returns nothing; no hype words in prose (the only
  `robust` hits are the statistical term and the `ROBUST` finding label).
- Citations checked and correct: Perold 1988; Almgren and Chriss 2000, J. Risk 3(2); Obizhaeva and
  Wang 2013, JFM 16(1); Andrews 1991, Econometrica 59(3); Moskowitz and Grinblatt 1999; Cooper,
  Gutierrez and Hameed 2004; Harvey, Liu and Zhu 2016; Lo 2002; Politis and Romano 1994.
- `data/reference/ATTRIBUTION.md` handles the CC BY-SA share-alike on the membership file and the
  S&P trademark correctly, and says the knowledge documents are fictional.

## 4. Findings, ranked

### D-01 [P1] The README's only worked example is not what v0.9.0 prints
- Evidence: `README.md:119-145`; `docs/index.html:310-325`. README shows "implementation shortfall
  delta -35.9 bps, slippage versus interval VWAP delta +0.4 bps", "latency ratio ... 9.98",
  "reject rate = 10.0%", "p99 9828 us", three observations starting with "Primary anomaly".
- Actual, same command at this tag: "delta -3.7 bps ... delta +0.0 bps", ratio 10.10, reject rate
  9.2 %, p99 10008 us, observation 1 is the change-point detector, eight observations.
- Status: VERIFIED (ran `ceap investigate "Analyse our AAPL execution between 14:00 and 15:00 ..." --dataset T06`).
- Confidence: high.
- Why it matters at a top firm: the first thing a reader does is run quickstart step 1 and compare
  with the block underneath. A deterministic, seeded product whose documented output differs in
  every number reads as either non-reproducible or unmaintained. The page calls it "the excerpt".
- Side observation for the X/Q reviewers (SUSPECTED, not my area): the actual executive summary
  says "implementation shortfall delta -3.7 bps" while observation 5 of the same report says IS
  "decreased by 28.6 bps relative to the baseline's +13.9 bps". One of the two is not the delta
  the sentence names.
- Fix: regenerate the excerpt from the tool and pin it: a `tests/repo` test that runs T06 with the
  mock and asserts the README and `index.html` blocks are a prefix-match of the rendered report.

### D-02 [P1] "Research scenarios RS01-RS30, 500-name universe" is false
- Evidence: `README.md:154`. Code: `src/ceap/data/historical.py:328` (`symbols = RESEARCH_UNIVERSE`,
  the 30 names); `RESEARCH_UNIVERSE_500` is imported at `historical.py:88` and used by no scenario,
  tool, evaluation or CLI path (only `tests/unit/test_r090_l4_universe.py`). `evaluation.md:468`
  and `LEARN.md:633` both say 30 names.
- Status: VERIFIED (grep of every use).
- Confidence: high.
- Why it matters: plan step D5 exists because "deciles of 3 names are meaningless". Every published
  research number (0.8333, 23/24, the null sweep, the golden research items) is still on 30 names.
  The README row tells a quant reader that weakness was fixed.
- Fix: README row to "30-name universe (a 500-name generator exists, not yet used by any
  scenario)"; state the same in "What 0.9.0 adds" (`README.md:191`). Acceptance: a docs test that
  the universe size quoted beside RS01-RS30 equals `len(HistoricalStore().get('R01').symbols)`.

### D-03 [P1] Hosted-model results are quoted in three documents while the front page says none exist, and the figures disagree
- Evidence: `README.md:15` ("No hosted-model numbers"), `README.md:161` ("NOT RUN LIVE"),
  `docs/index.html:199` ("no hosted model was called"). Against: `docs/evaluation/evaluation.md:164-176`
  (claude-haiku-4-5: verdict accuracy **0.95**, flags coverage 0.81; with a stronger planner 1.00 /
  1.00 / 0.10), `LEARN.md:643-655` (**19/21** = 0.905, then 21/21), `docs/ai-agents/ai-agents.md:345-357`
  (19/21). `CHANGELOG.md:1290-1308` (0.3.1) records the live run but neither 0.95 nor 19/21.
- Status: VERIFIED.
- Confidence: high.
- Why it matters: (a) "no hosted model was called" is untrue by the repo's own CHANGELOG; (b) the
  one hosted result has two values (0.95 and 19/21) and no artefact; (c) the `<!-- source: CHANGELOG.md -->`
  tags on `LEARN.md:643,645` satisfy `check_docs.py` (the file exists) though the CHANGELOG does
  not contain the number. A model-validation reader will find this in minutes and discount the
  evidence-class discipline everywhere else.
- Fix: one paragraph, in one place, labelled "historical, 0.3.1, pre-0.4.0 scenario set, no artefact
  kept, not reproducible"; a single figure or none; front page wording "no hosted-model result is
  claimed; one uncommitted 0.3.1 run is described as history". Acceptance: `check_docs` treats a
  `source:` of `CHANGELOG.md` as insufficient unless the quoted number occurs in that file.

### D-04 [P1] The reference quant page is known to be wrong, is still served to the retriever, and is linked without a warning
- Evidence: `docs/quant/quant.md:171-196` (hurdle "measured, not chosen", 0.40; `NO_ALPHA` at
  t < 0.40), `:286-289` (23/24, 6/6, 29/30; two verdicts). Corrections live only in
  `docs/quant/quant-errata-0.9.0.md`, which states at `:6-7` that it "is kept out of the RAG corpus".
  Pointers to the errata exist only at `docs/INDEX.md:18` and `CHANGELOG.md:102`. Direct links to
  quant.md with no pointer: `docs/index.html` nav ("Quant"), `LEARN.md:410`, `docs/GITHUB_PAGES.md:29`.
  `evaluation.md:123` does warn.
- Status: VERIFIED (text); JUDGEMENT on severity.
- Confidence: high.
- Why it matters: two exposures. A reader arriving from the site reads a hurdle of 0.40 and a score
  of 29/30. And the knowledge server can return those stale sections as DOCUMENT evidence that the
  reporter may quote with a verified citation span: the citation audit proves the quote is in the
  document, not that the document is right. The reason given (re-pinning moves caches and
  cassettes) is an engineering cost, not a reason to ship a known-wrong corpus.
- Fix: for 0.10.0 fold the errata in and re-measure (already planned). Until then: put the errata
  link in every place that links quant.md, and add the errata to the corpus as a superseding
  document or exclude the affected sections. Acceptance: a retrieval test that the query "what is
  the PROMOTE hurdle" does not return a chunk containing "0.40" without the correction.

### D-05 [P1] Two COOKBOOK recipes fail as written
- Evidence (recipe 6): `COOKBOOK.md:166-167` decide an approval with `dev-admin-key`.
  `src/ceap/policy/permissions.py:47-63`: ADMIN has no `approvals:decide`;
  `src/ceap/api/routes.py:602-604,637-642` require it. The call returns 403. `COOKBOOK.md:153`
  itself says "only `approver` decides approvals". The set-up sentence at `:162-163` uses a viewer,
  who lacks `investigate` (`permissions.py:25`) and cannot start the investigation at all.
- Evidence (recipe 26): `COOKBOOK.md:496` runs the image with `CEAP_API_KEYS="prod-key:trader"`.
  `Dockerfile:39` sets `CEAP_ENV=prod`; `src/ceap/api/auth.py:113-124` refuses any raw key under
  256 estimated bits outside dev. "prod-key" is 8 characters: the container exits at start-up.
  The same line passes `ANTHROPIC_API_KEY` although `Dockerfile:41` sets `CEAP_LLM_PROVIDER=mock`.
- Status: VERIFIED by reading the code paths (not executed: no server or Docker started).
- Confidence: high.
- Why it matters: both are 0.8.0/0.9.0 breaking changes (A4, H11) that the CHANGELOG lists and the
  recipes never caught up with. `check_docs.py` parses only lines starting `ceap `, so curl and
  docker lines are unguarded.
- Fix: recipe 6 with `dev-approver-key` and a principal that can investigate but lacks
  `risk:medium`; recipe 26 with `$(python -c 'import secrets; print(secrets.token_urlsafe(32))')`.
  Acceptance: an integration test that replays each curl recipe against the test client with the
  documented key and asserts the documented status; a settings test that the documented Docker
  environment passes `check_key_strength`.

### D-06 [P1] LEARN and a second page still publish "21/24 clear", a count the evaluation page says does not add up
- Evidence: `LEARN.md:662` ("21/24 clear and 2/6 near-threshold" under a total of 25/30);
  `docs/quant/real-data-and-paper.md:51` (same). `docs/evaluation/evaluation.md:145-147`: "The 0.8.0
  notes gave 21/24 clear next to 2/6 and 25/30, which do not add up". README, evaluation.md and the
  errata say 23/24.
- Status: VERIFIED. 21 + 2 = 23, not 25.
- Confidence: high.
- Why it matters: an arithmetic error in the flagship tutorial, already diagnosed elsewhere in the
  same release. Also: the 23/24 and 2/6 split has no committed artefact. `latest.json` holds only
  `research_verdict_accuracy = 0.8333`; the split is prose in lane notes, and the page it cites for
  detail does not exist (D-10).
- Fix: correct both lines; write the per-class counts into `latest.json` (`capability.research_clear`,
  `research_near`) and annotate them as metrics so the checker covers them.

### D-07 [P1] The 2026-11-09 plan has no evidence trail and no 0.9.0 status, though three documents say it has
- Evidence: `docs/review/2026-11-09/` contains only `PLAN.md`. `PLAN.md:39-45` cites eight reviews
  (R1-AI, R1-Q, R1-X, R1-E, R2-MR, R2-RC, R2-PR, R2-DT); none is in the repository. `:42-43`: R2-DT
  "pending; added when it reports"; `:222`: I5 "Reserved for R2-DT findings ... tbd". `PLAN.md:3-21`
  is headed "Status after 0.8.0" and stops there. Claims of per-step 0.9.0 status: `README.md:182`,
  `CHANGELOG.md:11-12`, `docs/INDEX.md:27` ("lanes L1-L6 ... with per-step status"; the plan has no
  lanes L1-L6).
- Status: VERIFIED.
- Confidence: high.
- Why it matters: every step carries tags such as "R2-MR C1" that cannot be followed. A validator
  cannot tell whether a finding was closed, softened or dropped. One review of the eight was never
  delivered and the plan still says so three days and one release later. Section 5 below lists what
  that hides (G4 silently dropped, H2 half done, B7 not measured).
- Fix: commit the eight review files (or state that they are lost and R2-DT never reported); add a
  "Status after 0.9.0" table per step with done / done-weak / not done and the evidence path.

### D-08 [P2] LEARN contradicts itself in five places (stale text beside current text)
- Evidence:
  - `LEARN.md:715`: "Not done yet: paper MOC caps, money as integer micro-dollars, dividends and the
    sigma fat-finger band (0.9.0)". All four shipped in 0.9.0 (`CHANGELOG.md:213-246`).
  - `LEARN.md:486`: "The one false positive is S50". `latest.json` false_positive_rate 0.06 is 3 of
    50; the two others and the one primary miss are not described.
  - `LEARN.md:461-462`: "the previous hour is always a clean baseline, which is also how the
    attribution model is defined". `LEARN.md:197-198` says the baseline is the same clock window on
    the previous five sessions, because the hour before is not comparable.
  - `LEARN.md:491-492` and `:549-550`: `deteriorated` "also considers VWAP slippage". `LEARN.md:218-219`:
    it is the residual z against the pre-trade estimate; the old delta is only a fallback.
  - `LEARN.md:563` (knowledge check 1): an untraceable number "is a warning on the report".
    `LEARN.md:355-357`: since 0.8.0 the prose is not shipped. Knowledge check 6 (`:576-578`) omits
    the 0.9.0 statement checks.
- Status: VERIFIED.
- Confidence: high.
- Why it matters: a student is taught the 0.4 design in sections 13-17 and the current design in
  5, 6 and 10. The self-test at the end marks the old answers as correct.
- Fix: rewrite sections 13, 14, 16 and 17 against the current code; list all three false positives
  and the miss by scenario id from a fresh `ceap evaluate`.

### D-09 [P2] Speed and reproducibility claims that do not hold
- Evidence: `LEARN.md:76` "`ceap investigate` answers it in about 0.9 seconds"; `LEARN.md:478` and
  `evaluation.md:91` give `mean_duration_ms ~32000`; my run took 67 s (loaded machine).
  `COOKBOOK.md:475-477`: "`pytest --ignore=tests/evaluation` ~45 s", "`pytest tests/unit` a few
  seconds", "the 50-scenario run, ~1 minute"; the suite is 1326 tests with a slow tier
  (`CHANGELOG.md:97-101`), collection alone exceeded 170 s here, and `COOKBOOK.md:742` says the
  evaluation report takes about 20 minutes. `LEARN.md:5-6`: "`pytest` and `ceap evaluate` regenerate
  every figure": not the local-model, retrieval, E1, R5 or paper figures.
- Status: VERIFIED for the contradictions; the 67 s is one run under load.
- Confidence: medium on absolute timings, high on the inconsistency.
- Fix: one measured timing table (machine stated) in `docs/testing/test-pyramid.md`; other docs
  link to it. Replace the LEARN sentence with the list of commands that regenerate each family.

### D-10 [P2] Broken references the checker cannot see
- Evidence: `docs/ai-agents/rag-hybrid-0.9.0.md:85` cites `docs/changes/0.9.0-l3.md` (no such file
  or folder); `COOKBOOK.md:257` cites `tests/unit/test_analytics.py` (the folder is
  `tests/unit/analytics/`); `docs/GITHUB_PAGES.md:20` is fine (conditional), no action.
- Status: VERIFIED (script over all tracked docs: 3 unresolved paths, 0 broken links, 0 broken anchors).
- Confidence: high.
- Fix: correct the two paths; add path and link resolution to `check_docs.py` (its docstring,
  `scripts/check_docs.py:1-19`, lists four checks; none resolves a path that is not in a
  `source:` tag).

### D-11 [P2] Stale thresholds, counts and sizes
- Evidence:
  - Coverage floor: `docs/DIAGRAMS.md:345` and `docs/configuration.md:57` say 85 %; CI enforces 90
    (`.github/workflows/ci.yml:73`; the step name at `ci.yml:66` also still says 85 %).
  - `docs/testing/test-pyramid.md:14-24`: unit 478, MCP 20, agent 173, evaluation 28, repository 17.
    Tree at the tag: 735, 37, 189, 50, 21 (`def test_` per folder). The page says the counts are
    from "the lane-6 commit"; they were not refreshed after the 182 tests of `CHANGELOG.md:94`.
  - `CHANGELOG.md:282`: engine facade "443 lines"; file has 447. `CHANGELOG.md:332`: "0 errors in
    207 files"; `src` has 230 `.py` files at the tag.
  - `docs/evaluation/evaluation.md:13`: column header "14:00-15:00 London"; `:28-29` says the
    session moved to New York in 0.5.
  - `docs/evaluation/evaluation.md:141-158`: a block headed "Current results" that the first line
    calls superseded, still showing 0.97 / 29/30.
  - `docs/evaluation/evaluation.md:378`: history.csv "has one row per release from 0.4.0"; the file
    has rows for 0.4.0, 0.5.0, 0.6.0 and 0.9.0 only. 0.8.0, the release in which research accuracy
    fell from 0.9667 to 0.8333, has no row.
  - `COOKBOOK.md:223`: "the 5 Stage-1 servers" (fine as history, but the demo is described nowhere
    for the eight).
- Status: VERIFIED.
- Confidence: high.
- Fix: correct each; generate the pyramid table and the coverage floor from the tree and `ci.yml`
  in the docs check.

### D-12 [P2] "Hand-labelled", "hand-built" and "held-out" used for things that are not
- Evidence: judge reference: `docs/DIAGRAMS.md:578` "a hand-labelled reference", `:591` "hand
  labels", `docs/architecture/overview.md:164` "a hand-labelled reference", `LEARN.md:727`
  "hand-built reference", `evaluation.md:431` "50 hand-built items". The labels were written by
  Claude (`local_model/README.md:93-95`). Plan step B5 asked for "50 human labels" (`PLAN.md:115`).
  Retrieval: `LEARN.md:435` "On 124 held-out queries", `docs/index.html:154` "in a held-out
  comparison". The 124 are all answerable queries, including the 20 train queries
  (`latest.json` `retrieval.by_category.train.n = 20`); only the abstention rule had a dev/held-out
  split (`rag-hybrid-0.9.0.md:39-49`).
- Status: VERIFIED.
- Confidence: high.
- Why it matters: both words carry a specific meaning to an evaluation reader. The caveat is
  stated correctly in one file and lost in five.
- Fix: "model-written reference labels (not human)" and "124 answerable queries" everywhere.

### D-13 [P2] "The last planned release" beside work scheduled for 0.10.0
- Evidence: `README.md:45`, `CHANGELOG.md:11`, `docs/index.html:49` against `CHANGELOG.md:81`
  ("left for 0.10.0"), `:102`, `quant-errata-0.9.0.md:7,88`, `local_model/README.md:79` ("a known
  issue for the next release").
- Status: VERIFIED. Confidence: high.
- Fix: choose one. If the project stops here, the known issues are permanent and should be listed
  as limitations; if not, drop "last planned".

### D-14 [P2] The local-model summary leaves out what a practitioner needs first: time, and one adverse golden figure
- Evidence: `docs/evaluation/local_model/qwen2.5_7b-instruct.json:22-25`: planning call median
  1,062,547 ms (17.7 minutes), p95 26 minutes, seed 1; seeds 2 and 3 similar. No document states
  latency; the only hint is "roughly 5 output tokens/s" (`local_model/README.md:20`). The one-line
  summary repeated at `README.md:19,160`, `CHANGELOG.md:20`, `evaluation.md:434` gives golden
  "cause 6/9, verdict 3/3" and omits "no forbidden claim 6/9" and "faithful claims 25/39" from the
  same table (`local_model/README.md:38,46`).
- Status: VERIFIED. Confidence: high.
- Why it matters: a plan that takes 18 minutes is the main operational finding of the experiment.
  The summary line picks the two favourable golden rows of four.
- Fix: add a latency row (median and p95 per purpose) to the table and "about 18 minutes per plan
  on this CPU" to the summary; include the forbidden-claim row in the one-liner.

### D-15 [P2] The golden sign-off record and the CHANGELOG disagree on what 13/15 is
- Evidence: `tests/golden/golden_set_research_v1_1.json:11`: "the 13/15 is a platform defect to
  diagnose, not a relabel". `CHANGELOG.md:74-79`: "This is a near-threshold sampling miss, not a
  code defect". Also `PLAN.md:28-29` still says both files "await owner sign-off" (v1.1 is SIGNED;
  the OOD file is still `PENDING_OWNER`, `tests/golden/golden_ood_v1.json:12-13`, correctly stated
  at `evaluation.md:347-348`). `evaluation.md:276-288` and the generated table describe the golden
  set as v1.0.0 only and never mention the signed v1.1 file; `docs/index.html:79` mentions it under
  a `latest.json` source tag, where it does not appear.
- Status: VERIFIED. Confidence: high.
- Fix: record the diagnosis in the sign-off file (new dated note; do not edit the signed text),
  describe v1.1 in `evaluation.md`, and either sign or drop the OOD file.

### D-16 [P2] "Escape impossible" beside a measured 3/10
- Evidence: `LEARN.md:543-544`: "the catalogue and policy make escape impossible anyway".
  `local_model/README.md:58`: 3 of 10 attacks planted in the user question were obeyed by the local
  model (2 more replies unusable).
- Status: VERIFIED (text); JUDGEMENT on wording.
- Confidence: medium.
- Fix: "the catalogue and policy bound what an obeyed injection can do (no tool outside the
  catalogue, no number without evidence); obey rate through the question measured at 3/10 on the
  local model".

### D-17 [P2] No "not investment advice" statement anywhere
- Evidence: `git grep -i "investment advice|disclaimer"` over all `.md` and `docs/index.html`
  returns nothing. `README.md:236-238` is "MIT" only. The repo shows a strategy study, a paper
  book and PROMOTE verdicts.
- Status: VERIFIED. Confidence: high.
- Fix: two lines in README and the site footer: research and education only, not investment advice,
  no warranty beyond the licence.

### D-18 [P3] Smaller items
- `docs/DIAGRAMS.md:290`: `paper` is drawn without its module-level import of `ceap.mcp`
  (`src/ceap/paper/job.py:38`); with `mcp --> paper` on `:289` that is a cycle the diagram hides,
  though its preamble at `:270-276` says the less clean edges "are shown as they are".
- `docs/learn/ai/07-local-models.md:17`: cassette attributed to `ceap.llm.recording`; the local
  cassette classes are in `src/ceap/llm/ollama.py:146,291`.
- `local_model/README.md:23` "first 3 golden items" against `evaluation.md:425-426` "first 3
  investigation items ... plus 1 research item". The second is what n = 9 and n = 3 imply.
- `LEARN.md:99,491,565`: drift "about 30 bps per hour at 10 % vol". 10 % / sqrt(252) / sqrt(6.5) is
  24.7 bps. `LEARN.md:170` (14 bps for a linear schedule) is right.
- `LEARN.md:96`: "Arrival (decision) price" merges two prices that section 5 and the glossary keep
  apart.
- `LEARN.md:299-301`: "why the critic model is not allowed to lower a confidence" against `:337-339`
  "can only lower"; the intended point (it cannot set one directly) needs one more clause.
- `docs/glossary.md:34`: deflated Sharpe described as a corrected Sharpe ratio; it is a
  probability, and the gate is DSR >= 0.5.
- `LEARN.md:726`: "(200 attacks ...)" beside a result on 40.
- `CHANGELOG.md:212`: two papers "cited from memory". Both citations are correct; say so or drop
  the phrase. `docs/quant/quant.md:132` gives -55 % Nasdaq to Shumway 1997; it is Shumway and
  Warther 1999 (pinned file: put it in the errata).
- `CHANGELOG.md:88` (R² about 0.93 for a constant y) against `quant-errata-0.9.0.md:79-80`
  ("anything from -3.3 to 0.82").
- Yahoo Finance via `yfinance` is used for the E1 ETF gate (`result_holdout.json` `etf_gate.source`)
  and optional dividends; `ATTRIBUTION.md:27-31` says it is not committed but gives no usage note
  for Yahoo's terms. PLAN D2 named FRED and AQR; neither is used, and nothing says so.
- Tone: sections 18 and 19 of LEARN, the hosted-model section of `ai-agents.md` and `quant.md` keep
  the older voice: 44 em dashes in LEARN, "genuinely" (`LEARN.md:610,647`), "load-bearing, not
  cosmetic" (`:598`), "The maths doesn't trust itself either" (`:621`), "plays the same trick"
  (`:630`), "honest results, not projected" (`evaluation.md:164`), "the lesson generalises past
  this project" (`LEARN.md:654`, `ai-agents.md:359`). The 0.9.0 pages do not have this. "enterprise-style"
  at `docs/index.html:42`, `docs/architecture/overview.md:5`, `docs/SPECIFICATION.md:30`.
- `docs/INDEX.md` has no row for `evaluation/local_model/README.md` or `ai-agents/l3-prereg-0.9.0.md`.
- Secondary docs carry numbers with no source tag and no checker: `execution.md` 43 numeric lines,
  `research/README.md` 56, `overview.md` 55 (counts by regex; `check_docs.py:35-41` covers five files).

## 5. Earlier plan (2026-11-09): what shipped

Review files in the repo: none of the eight (D-07).

### Scheduled for 0.8.0 in the release train (`PLAN.md:231`)

| Items | Status from CHANGELOG 0.8.0 and code |
|---|---|
| A1-A20 | done (`PLAN.md:13`); not re-verified here beyond A2, A4, A13 text in LEARN matching `permissions.py` |
| F9, F11 (staging part), G1, G3, G6, G7, H5, H8, H9, I3 | done |
| H2 | half: branch coverage on, floor 85 % then (90 % in 0.9.0, `ci.yml:73`); "95 % on analytics and domain" never done and not mentioned again |
| Pulled forward into 0.8.0: B2, B3, B6, B9, C1, C3, D1-D4, D6-D9, D11-D23, F1-F4, F6-F8, F14 | done per `PLAN.md:14-18`; B6's OOD file is still `PENDING_OWNER` at 0.9.0 |

### Moved to 0.9.0 (`PLAN.md:14-21`)

| Item | Shipped? | Convincing? |
|---|---|---|
| B1 local model, n >= 3 seeds on golden, adversarial, claims | yes | weak: golden 3 of 45 items (+1 research), adversarial 40 of 200 on one seed; disclosed |
| B4 injection corpus, nightly, per surface | corpus yes | weak: no workflow references Ollama or `CEAP_LOCAL_MODEL`, so "nightly" never runs in CI; one manual subset run |
| B5 judge against 50 human labels | yes | weak: labels are model-written (D-12); only `hedged` enabled |
| B7 challenger plan agreement shown to approver | wired | not done as a measurement: no number (`CHANGELOG.md:85-87`), disclosed |
| B8 AI SLOs | yes (`observability/ai_slo.py`) | not assessed here |
| C2 hybrid retrieval | yes, opt-in | convincing; BM25 default under the pre-set rule |
| D5 500-name universe | generator only | not convincing: unused by any scenario (D-02) |
| D10 delisting sensitivity | yes | not assessed here |
| E1 | yes, NULL | convincing; replication gate NOT RUN (stated in advance); AQR/FRED unused |
| E2 | yes | fine |
| F5 paper MOC, F10, F11 paper execution, F12 rest, F13 | yes, mostly off by default | F11 impact does not scale with size (stated, `CHANGELOG.md:226-228`); LEARN still says not done (D-08) |
| G2 50x generator | no: 1.18x over 0.8.0 | stated as a miss |
| **G4 backtest hot loop 5-10x** | **no** | **not mentioned anywhere outside the plan: silently dropped** |
| G5 durable runs | yes, opt-in | not assessed here |
| G8, G9, G12, G13 | yes | RUNBOOK inline commands parse; anchor path differs between `RUNBOOK.md:102` (`var/`) and `COOKBOOK.md:685` (`audit/`) |
| G10 chaos tests | partly | event-loop lag test dropped (`CHANGELOG.md:117`) |
| G11 replay gate and status page on Pages | gate yes | status page rendered, "Nothing is pushed" (`CHANGELOG.md:319`) |
| H1, H4, H6, H7, H10-H15 | yes | H3 ratchet ceiling is `null` (`tests/mutation/baseline.json`), so it gates nothing yet (stated) |
| I1, I2, I5 | yes | I5 row in the plan is still the placeholder; R2-DT never reported |

### Scheduled for 0.10.0-0.12.0 in the original train and still open

G4; H2 per-package 95 %; B7 measurement; near-miss abstention (3/25 leak); G2 at 50x (needs an
owner decision on moving goldens); mutation ceiling; OOD golden sign-off; quant.md corpus
re-measurement; the numerical tolerance fixes and the three paper/durable known issues
(`CHANGELOG.md:81-90`); a research evaluation on the 500-name universe; any hosted-model run.

## 6. Number audit

"=" exact at the precision shown. Sources are committed files unless marked.

### README.md

| Doc location | Claim | Source | Actual | Match? |
|---|---|---|---|---|
| README:7 | 50 execution, 30 research scenarios | `latest.json` capability.scenarios / research_scenarios | 50 / 30 | yes |
| README:19,160 | plan valid 15/30 (0.50) | `qwen2.5_7b-instruct.json` planner.plan_valid | 15/30, 0.5 | yes |
| README:19 | tool precision 1.00, recall 0.73 | same, tool_precision / tool_recall | 161/161; 61/84 = 0.7262 | yes |
| README:19 | critic 35/45 (0.78) | same, critic.detection | 35/45, 0.7778 | yes |
| README:19 | 0/39 untraceable numbers | same, claims.untraceable_number | 0/39 (n counts claims) | yes |
| README:19 | golden cause 6/9, verdict 3/3 | same, golden.* | 6/9, 3/3; also no-forbidden 6/9 (omitted) | yes, selective (D-14) |
| README:19 | injection obeyed 3/40, all via question | `..._injection.json` obey, per_surface | 3/40; question 3/10, others 0/10 | yes |
| README:19 | judge enables only `hedged`, kappa 1.00 | `judge_calibration.json` | hedged 1.0; faithful 0.5113; complete -0.0345 | yes |
| README:45 | v0.9.0 | `pyproject.toml:7`, `ceap/__init__.py:16` | 0.9.0 | yes |
| README:45 | "last planned release" | CHANGELOG:81,102 | 0.10.0 work named | no (D-13) |
| README:126-134 | -35.9 bps, +0.4 bps, 9.98, 10.0 %, 9828 us | tool output at tag | -3.7, +0.0, 10.10, 9.2 %, 10008 us | **no (D-01)** |
| README:154 | RS01-RS30 on a 500-name universe | `historical.py:328` | 30 names | **no (D-02)** |
| README:168 | 0.98 / 0.98 / 0.06 | `latest.json` capability | 0.98 / 0.98 / 0.06 | yes |
| README:168 | bar 90 % | `evaluation.md:74-75` | 0.9 | yes |
| README:169 | 0.8333 over 30 | `latest.json` | 0.8333 | yes |
| README:169 | 23/24 clear | lane note, `rag-hybrid-0.9.0.md:83` | prose only, no artefact | untraceable (D-06) |
| README:170 | 500 seeds, hurdle 3.0, 0 false PROMOTEs | `null_sweep_per_seed.json` | 500 seeds, max stat 2.386 | yes |
| README:171 | 6/15 to 15/15, 0/10 false alarms, both sets | `rag-hybrid-0.9.0.md:71-72`; 6/15 also `latest.json` agents.critic.detection | as stated; the "after" has no JSON artefact (scorer command only) | yes (prose source) |
| README:172 | +0.065, CI +0.016 to +0.113, 124 queries | `rag-hybrid-0.9.0.json` rrf_rerank | 0.0645, [0.0161, 0.1129], n 124 | yes |
| README:172 | 3/25 near-miss leak | `latest.json` retrieval.negatives | 3/25 | yes |
| README:173 | holdout 1990-2015, NW t 1.16 vs 1.645, 10 bps | `e1/result_holdout.json` holdout.net10.nw_t | 1.1599 | yes |
| README:174 | 75.1 % then 74.9 % against 95 % | `result_momentum_2026-11.md:16`, `-v2.md:37` | 75.1, 74.9 | yes |
| README:175 | from 2026-11-09; about 1980 sessions | `config/paper.json:3`; `test_forward_and_ledger.py:29-30` | 2026-11-09; 1980 (1979.6 rounded up) | yes |
| README:208 | 8 MCP servers named | `src/ceap/mcp/*` | 8, same names | yes |

### docs/evaluation/evaluation.md

| Doc location | Claim | Source | Actual | Match? |
|---|---|---|---|---|
| :84-90 | 50, 0.98, 0.98, 0.06, 1.00 | `latest.json` | same | yes |
| :91 | mean_duration_ms ~32000 | none committed | - | untraceable; conflicts with LEARN:76 (D-09) |
| :94-96 | TCA acceptance: 0.01 on 200 seeds, 2.3 bps, power 0.83, size 0.05, coverage 0.86-0.91 | none named | - | untraceable (not checked further) |
| :138-139 | 0/500 at 3.0; 15/500 = 3.0 % at 0.40 | `null_sweep_per_seed.json` | 0; 15 | yes |
| :144-145 | 25/30, 23/24, 2/6 | `latest.json` 0.8333; split from lane note | 0.8333; split prose only | partly |
| :150-157 | 0.97 (29/30) under "Current results" | superseded block | - | stale (D-11) |
| :166-176 | hosted model 0.95, 0.81, 1.00, 1.00, 0.10, "under $2" | CHANGELOG 0.3.1 | 5/21 incomplete, 21/21, false-flag 0.33 to 0.10; no 0.95 | **no artefact; conflicts with LEARN 19/21 (D-03)** |
| :200-273 generated block | all rows | `latest.json` | `check_docs.py` passes | yes |
| :211, :286 | golden v1.0.0, SIGNED_OFF 2026-11-08 | `tests/golden/golden_set.json` | 1.0.0, frozen 2026-11-08 | yes |
| :278 | 30 + 15 items | `latest.json` golden | 30, 15 | yes |
| :337 | 15 OOD items, PENDING_OWNER | `golden_ood_v1.json` | 15, PENDING_OWNER | yes |
| :352-354 | 159 queries; 44 / 30 / 20 / 25 | `latest.json` retrieval | 159; 44, 30, 20, 25 (+10 negatives, 20 train, 10 held-out) | yes |
| :378 | one history row per release | `history.csv` | 4 rows; 0.7.0, 0.7.1, 0.8.0 absent | no (D-11) |
| :388 | RAG floors 0.80 / 0.70 | not checked | - | not checked |
| :404-411 | 75.1 %, 74.9 %, 1980, first session 2026-11-09 | as README | same | yes |
| :425 | planner 10, critic 5 cases, reporter 4 | `latest.json` agents | 10, 5 batches, 4 | yes |
| :430 | 200 attacks | `injection_corpus_v1.json` attacks | 200 | yes |
| :434 | local-model one-liner | as README:19 | same | yes |
| :438-440 | 6/15 in table; 15/15 with checks | `latest.json`; `rag-hybrid-0.9.0.md` | 6/15; 15/15 | yes |
| :464 | 0.98 / 0.98 / 0.06 | metric tags | same | yes |
| :468 | 30-name, 20-year, 5040 days | `historical.py` | 30 names | yes (and contradicts README:154) |
| :503-505 | 0.8333 (25/30); bars 0.875, 1/3, 0.80 | `latest.json`; bars from code, not re-derived | 0.8333 | yes / not re-derived |

### docs/evaluation/local_model/README.md

| Doc location | Claim | Source | Actual | Match? |
|---|---|---|---|---|
| :35-47 | 13 rows k/n, rate, CI, per seed | `qwen2.5_7b-instruct.json` metrics.* | every cell equal after rounding (e.g. repair 14/30 0.4667 [0.3023, 0.6386]; fallback 1/30 [0.0059, 0.1667]; recall per seed 0.6429 / 0.75 / 0.7857) | yes |
| :20 | about 5 output tokens/s | none | JSON has latency only: planning median 1,062,547 ms | untraceable; latency omitted (D-14) |
| :21 | temperature 0.7, seeds 1-3 | JSON temperature, seeds | 0.7, [1, 2, 3] | yes |
| :23 | golden slice 3 items | JSON golden_limit; n = 9 and 3 | 3 investigation + 1 research per seed | wording (D-18) |
| :24 | 40 of 200, 10 per surface | injection JSON attacks, per_surface_limit | 40, 10 | yes |
| :58-62 | 3/10 [0.11, 0.60], unusable 2/10; 0/10 [0.00, 0.28] x3; 3/40 [0.03, 0.20] | injection JSON | 3/10 [0.1078, 0.6032], 2/10; 0/10 [0, 0.2775]; 3/40 [0.0258, 0.1986] | yes |
| :64 | authority, direct, French | injection JSON per_style | authority 1/4, direct 1/4, french 1/4 | yes |
| :69-71 | 50 items; 1.00, 0.51, -0.03 | `judge_calibration.json` | 50; 1.0, 0.5113, -0.0345 | yes |
| :89 | seed 1 live golden 0/3, replay 2/3 | replay 0.6667 in JSON; live figure has no artefact | 2/3 replay | replay yes; live untraceable |
| :96 | Ollama 0.40.16 on 2026-11-11 | none committed that I found | - | untraceable |

### docs/ai-agents/rag-hybrid-0.9.0.md

| Doc location | Claim | Source | Actual | Match? |
|---|---|---|---|---|
| :3, :74 | rules and held-out set committed in d04f19d before the checks | git log | d04f19d 17:48; checks 5c35a89 19:38 | yes |
| :6 | 159-query set | `latest.json` | 159 | yes |
| :15 | 10 000 draws, seed 0 | not in JSON | - | not checked |
| :19 | bm25 108, 0.80-0.92, 0.69, 0.796 | JSON bm25 | 108, [0.8006, 0.919], 0.6935, 0.7956 | yes |
| :20 | dense 95, 0.68-0.83, 0.54, 0.674, -0.105 [-0.194, -0.016] | JSON dense | 95, [0.6843, 0.8319], 0.5403, 0.6743, -0.1048 [-0.1935, -0.0161] | yes |
| :21 | rrf 112, 0.84-0.94, 0.65, 0.782, +0.032 [-0.032, +0.097] | JSON rrf | 112, [0.8384, 0.9438], 0.6452, 0.7821, 0.0323 [-0.0323, 0.0968] | yes |
| :22 | bm25_rerank 114, 0.86-0.96, 0.73, 0.833, +0.048 [+0.008, +0.097] | JSON | 114, [0.8579, 0.9556], 0.7339, 0.8325, 0.0484 [0.0081, 0.0968] | yes |
| :23 | rrf_rerank 116, 0.88-0.97, 0.75, 0.848, +0.065 [+0.016, +0.113] | JSON | 116, [0.8778, 0.9669], 0.75, 0.8484, 0.0645 [0.0161, 0.1129] | yes |
| :25-26 | typos 23/30 to 29/30; paraphrase 42/44 to 44/44; multi-hop 14/20 to 13/20 | JSON by_category | same | yes |
| :34 | gated 0.8.0 figure 89/124 | `latest.json` all_positive.recall_at_3 | 89/124 | yes |
| :42 | threshold -8.28 | JSON abstain.chosen_on_dev | -8.2785 | yes |
| :46-49 | 56/67, 3/13; 50/67, 2/13; 44/57, 6/12 (0.25-0.75); 42/57, 1/12 (0.01-0.35) | JSON abstain.* | same; CIs [0.2538, 0.7462], [0.0149, 0.3539] | yes |
| :52-53 | 3/25 pooled: X01, X04, X11 | `latest.json` negatives | 3/25, same ids | yes |
| :66 | caps 0.4 / 0.2 | `CHECK_CAPS` (not opened) | - | not checked |
| :71-72 | 6/15 to 15/15 (0.80-1.00), 0/10, both sets | before: `latest.json` 6/15; after: scorer command, no JSON | Wilson(15, 15) lower 0.796 | yes / after has no artefact |
| :83 | 23/24 (not 21/24), RS02 | no artefact | - | untraceable |
| :85 | see `docs/changes/0.9.0-l3.md` | file system | does not exist | **no (D-10)** |

### CHANGELOG.md, 0.9.0 section

| Doc location | Claim | Source | Actual | Match? |
|---|---|---|---|---|
| :9 | 0.9.0 dated 2026-11-12 | tag commit c14105e / dd6bf3c | 2026-11-12 | yes |
| :20 | local-model one-liner | as README:19 | same | yes |
| :23-24 | 6/15 to 15/15, 0/10 | as above | same | yes |
| :26, :135-137 | t 1.16; Sharpe 0.23, CI [-0.12, 0.59]; in-sample t 1.86; ETF 0.94; replication NOT RUN | `e1/result_holdout.json` | 1.1599; 0.2327, [-0.118, 0.590]; 1.8649; 0.9400 | yes |
| :27, :170-171 | +0.065, CI; 116/124 vs 108/124 | rag JSON | same | yes |
| :31, :70-72 | 0.98, 0.06, 0.8333, 0.9667, 0.8667 (13/15), must-mention 1.00 | `latest.json` | same; 13/15 = 0.8667 | yes |
| :31 | "Unchanged from the 0.8.0 numbers" | CHANGELOG 0.8.0 :369, :373 | 0.8333, 0.8667 there too | yes (no 0.8.0 row in history.csv to confirm) |
| :67-69 | results table | local-model JSON files | same | yes |
| :71 | 23/24 clear, 2/6 near | lane note | no artefact | untraceable |
| :75-77 | OOS IC t 1.99 vs 1.0; measured 0.79, 0.93; Sharpe CI [-0.16, 0.75] | none committed that I found | - | untraceable |
| :94 | 182 new behaviour tests | none | - | not verified |
| :101 | 1326 tests, 93 % branch, floor 85 to 90 | `ci.yml:73` for the floor | floor 90; 1175 `def test_` before parametrisation; count and coverage not reproduced | floor yes; rest not verified |
| :112 | golden v1.1 signed, option A | `golden_set_research_v1_1.json:6-11` | SIGNED 2026-11-09, option A | yes (wording conflict, D-15) |
| :114 | G2 1.18x against 50x | `:265-266` own measurement | no artefact | untraceable, stated as a miss |
| :116, :173-175 | leak 3/25; 6/12 vs 1/12 | JSON | same | yes |
| :118 | bar 490 to 466 = 500 - 17 - 17 | arithmetic | 466 | yes |
| :149 | 200 attacks = 4 x 10 x 5 | corpus file | 200 | yes |
| :153 | 50-item "hand-built" reference | `judge_calibration.json` items; labels by Claude | 50 | count yes; wording (D-12) |
| :203 | 50 late listings, 17 mergers, 17 delistings | not checked | - | not checked |
| :214 | 10 % of a 6 % auction = 0.6 % ADV | arithmetic | 0.6 % | yes |
| :282 | engine 1,532 lines to 443 | `wc -l src/ceap/harness/engine.py` | 447 (plan said 1,512 at `PLAN.md:204`) | no, minor (D-11) |
| :311 | 7 alert rules | `deploy/prometheus/ceap-alerts.yml` | 7 | yes |
| :332 | mypy: 207 files | `find src -name "*.py"` | 230 at the tag | stale (D-11) |
| :353 | ADRs 0001-0005 | `docs/adr/` | 5 | yes |

### Cross-document consistency

| Quantity | Values found | Where |
|---|---|---|
| Clear-case research count | 23/24 and 21/24 | README:169, evaluation.md:144, errata:70 vs LEARN:662, real-data-and-paper.md:51 |
| Hosted-model verdict accuracy (0.3.1) | 0.95 and 19/21 | evaluation.md:166 vs LEARN:645, ai-agents.md:356 |
| Hosted model called? | no / yes | index.html:199, README:15 vs CHANGELOG:1292, LEARN:644 |
| Research universe size | 500 and 30 | README:154 vs evaluation.md:468, LEARN:633 |
| Coverage floor | 85 % and 90 % | DIAGRAMS:345, configuration.md:57 vs CHANGELOG:101, ci.yml:73, COOKBOOK:610 |
| Time for one investigation | 0.9 s and about 32 s | LEARN:76 vs LEARN:478, evaluation.md:91 |
| Number of false positives | "the one" and 3 of 50 | LEARN:486 vs latest.json 0.06 |
| Judge reference labels | hand-labelled and written by Claude | DIAGRAMS:578, overview:164 vs local_model/README:93 |
| 13/15 golden | platform defect and not a code defect | golden v1.1 json:11 vs CHANGELOG:78 |
| Anchor file path | `var/audit_head.json` and `audit/audit_head.json` | RUNBOOK:102 vs COOKBOOK:685 |
| Sample report figures | README and index.html agree with each other, not with the tool | D-01 |

### GitHub Pages site (`docs/index.html`)

Consistent with README and `latest.json` on every figure in the "Measured" block (lines 76-88):
0.98 / 0.98 / 0.06, 0.8333 (25/30), golden 0.9667 / 0.8667, safety 1.00, local-model one-liner, E1,
R5, paper start, 8 servers and 39 tools. Its quick-start commands all parse, including
`ceap paper run-day --date 2026-11-13 --db var/paper/paper.db`. Defects: the stale sample report
(D-01), "no hosted model was called" (D-03), "held-out comparison" (D-12), "last planned release"
(D-13), a direct link to quant.md with no errata note (D-04), and "research golden v1.1" under a
`latest.json` source tag that does not contain it (D-15). `check_docs.py` does not read this file
(`docs/GITHUB_PAGES.md:36-37` says so), which is why it drifted with the README excerpt.
