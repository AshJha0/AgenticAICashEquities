# What CEAP demonstrates today, and what would strengthen it

An honest note for the owner, written after the 2026-11-12 review of v0.9.0. It is about how the
project reads to senior quant, AI and trading-technology people. Evidence for every statement is in
`REVIEW.md` (R-IDs) and the reviewer reports.

## 1. What it demonstrates well today

These hold up under a careful reading and can be shown as they are.

**Research discipline, including saying "no".**
The E1 study was preregistered and hash-locked, the code matches the registration, the holdout was
opened once, and the result is reported as NULL (net t 1.16 against 1.645) in every place it is
summarised. R5 was published VOID twice with no statistic computed because the free data could not
meet the coverage rule. A reader who has sat on a research committee will value this more than a
positive backtest, because it is the behaviour that is hard to fake. The write-up of why the
stock-level study could not pass on free data is a useful piece on its own.

**A control design that treats the model as untrusted.**
The model proposes; code computes, validates and decides. Policy runs before every tool call,
HIGH-risk tools always need a human, approvals are bound to a content hash and need two named
people, untrusted text is wrapped in nonce-delimited blocks, and structured output is
grammar-constrained. An independent reviewer tried to forge, replay and self-approve through the
API and could not. This is the right architecture for a regulated desk and it is implemented, not
only drawn.

**Correct core mathematics.**
Checked by running, not only reading: the Perold shortfall legs sum exactly for buys and sells, the
Almgren-Chriss trajectory matches the closed form, the expected-maximum Sharpe agrees with a Monte
Carlo, Ledoit-Wolf agrees with scikit-learn to 4e-16, the auction uncross maximises executable
volume, the NYSE calendar is right.

**Supply-chain and CI hygiene above the usual standard.**
A universal hashed lock installed with `--require-hashes` in CI and in the image, actions pinned to
commit SHAs, least-privilege workflow permissions, SBOM, build provenance and keyless signatures on
release, a non-root image without pip.

**A habit of disclosure.**
The CHANGELOG lists known defects, a missed performance target is recorded as a miss, and the
challenger number was withheld rather than published from a broken path. The retrieval comparison
was run under a rule fixed in advance and the default stayed BM25 when the rule said so.

## 2. What a strong reader would find in the first hour

These are the items that would change how the rest is read. All are in the 0.10.0 plan.

| What they would check | What they would find | R-ID |
|---|---|---|
| "Was a real model measured end to end?" | The end-to-end golden rows labelled as the local model are mock output; the live files say 0/3. | R-01 |
| The forward-log branch | One commit, a row dated before its session, nothing since; the README says it runs daily. | R-02 |
| The README example | It does not match what the command prints, and the report it prints gives two different values for the same change. | R-29, R-03 |
| "Plan valid 15/30" | Mostly a validator rule about `null`; the true figure is much better and the published one undersells the work. | R-04 |
| "Is it agentic?" | A fixed governed pipeline with three bounded model steps. The docs say this; there is no number for how much the model's plan changes the outcome. | AI-2 section 6 |
| The Sharpe interval in the verdict | The annual-data formula on daily data. A quant will see this in the function signature. | R-15 |
| Whether listed features run | PBO, factor-neutral checks, the schedulers, the SLO monitor, tracing, drift checks and the judge have no caller in the product. | R-17, R-23, R-14, R-34 |
| Durable runs | They do not resume through the product. | R-11 |
| Sample sizes | Ten planner cases, fifteen planted faults, a 1,922-word corpus, template-built judge labels. | R-07, R-10 |

The first two are the ones to correct before showing the project to anyone, and both can be
corrected in the documents today without waiting for a release (owner decision O2 in the plan).

## 3. How to describe the project accurately now

A description that survives scrutiny:

> A governed agent harness for cash-equity execution analysis and signal research. Deterministic
> analytics compute every number; a local open-weight model plans, critiques and writes under
> validation, policy and human approval. Two preregistered real-data studies, one NULL and one
> VOID, reported as such. Synthetic data everywhere else. Paper orders only.

Phrases to avoid until the plan lands, because the evidence does not support them yet: "evaluated
end to end on a real model", "runs daily" for the forward log, "resumable" for runs and approvals,
"pinned by digest", "OpenTelemetry" as a present capability, "execution algorithms" as part of the
product, "judge calibrated" and "500-name universe" for the research scenarios.

Results that must stay as they are: E1 is NULL. R5 is VOID. The forward result is UNKNOWN and will
be for years at this sample rate. The 7B model is slow on a laptop (a planning call takes many
minutes) and that belongs in the headline. If the corrected plan validity turns out near 28 of 30,
publish it with the old figure and the reason; if the new golden run is poor, publish that.

## 4. What would most strengthen it, by reader

**For senior AI readers.**
1. A real end-to-end measurement of the local model with a replay gate that cannot fall back
   (T1, T3, T8). This single change converts the weakest claim into the strongest.
2. Metrics that mean what they say, each next to a rule baseline, with case-clustered intervals and
   latency (T4, T5, T6).
3. A number for "agentic": the share of executed steps the model originated, and whether the model's
   plan beats the canonical one (A4). Either answer is interesting; not knowing is the weak position.
4. Planner skills, with the acceptance number registered before the run (A1). Showing that a
   narrower output space makes a small model reliable is a result people in the field care about.
5. A retrieval evaluation on real filings with labels the author did not write alone (A5, A7).
6. Resume that works, shown by killing the process at every node (H1, A3).

**For senior quant readers.**
1. The Sharpe interval and deflated Sharpe corrected, with the list of verdicts that moved (Q1, Q2).
2. The realism toolkit on the verdict path: PBO, effective trials, factor-neutral IC (Q4).
3. A power curve at the hurdle in force, with a plain statement of what the gate can and cannot
   detect (Q5).
4. The research evaluation on 500 names, so a decile is fifty names (Q6).
5. E1 rebuildable from recorded hashes, and a preregistered live-window test on data after the
   literature it follows (Q9, B10). The second is the only genuinely out-of-sample evidence free
   data can give.

**For trading-technology readers.**
1. A forward record that is actually forward, anchored daily, with a staleness alarm that tolerates
   no missed session (X1, X2).
2. A hash chain that survives the schema-rollback attack (X3).
3. An image and a compose stack that start and are tested doing real work (E3, E4).
4. A scheduler on the paper path with a paired study against the Almgren-Chriss trajectory, and a
   plain statement of what the stylised book cannot show (X7).
5. A green nightly with a measured mutation baseline, and coverage of at least 90 % in every package
   (E1, E2).

## 5. What not to add

Each of these was assessed and would weaken the project by adding surface without evidence:
multi-agent debate for the critic (a vote over the existing seeds changes nothing), a knowledge
graph over a corpus with no entities, an agent framework rewrite, agent-to-agent protocols, MCP
sampling and elicitation (both cut against the project's own control rules), SHAP for linear
models, bandits and reinforcement learning on a simulator that cannot separate the algorithms it
already has. The reasons and the evidence are in `AI_ROADMAP.md` section 3. Being able to say why
each was left out is itself a signal of judgement.

## 6. Bottom line

The foundations a careful reader looks for are present: honest research, real controls, correct
core maths, clean supply chain. What is missing is one release in which the AI evaluation and the
operational records are brought up to the standard of the research. After 0.10.0 the project can be
shown without caveats about its own numbers; after 0.11.0 it has results in AI evaluation that are
worth a specialist's time.
