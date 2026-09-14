---
name: evidence-standards
description: Use when about to call something verified, fixed, or passing; when reading a test, gate, benchmark or metric as evidence; when comparing two measurements; or when writing a check that could silently pass
---

# Evidence Standards

## Overview

`design-lenses` asks *is this good?* This asks *how do you know that's true?*

Every discipline below was learned from a green result that meant nothing. The
recurring shape is not a wrong answer — it is **a mechanism that reads as
enforcement and enforces nothing**, and it is indistinguishable from success
right up until someone checks.

Record what you establish with `using-claimlock`.

## The output

For each claim you make, name the evidence and what would have falsified it. A
claim with no falsifier is an opinion.

## The standards

| Standard | The tell you skipped it |
|---|---|
| **Exit status is not evidence** | You reported success from `$?` or "no errors" |
| **A pass is evidence only if it could have failed** | A green result you never tried to break |
| **An absent counter means zero, not missing** | Your check crashed on absence, or treated it as success |
| **Name the enforcement site, or record NONE** | "It flows through the X layer" — said, not asserted |
| **A delta needs a null pair** | Two numbers from different days compared as if paired |
| **Count, don't time** | You measured milliseconds on a shared machine |
| **Census the population, then assert coverage** | The checker was correct and pointed at the wrong set |
| **Sentinel the instrument** | A perfect result from something that measured nothing |
| **A dated doc records intent, not state** | You read "NOT STARTED" as current |
| **A check that cannot fail is not a check** | The failure path has never once executed |

## The ones that bite hardest

**A run is evidence only with a non-zero pass count matching what you expected
to run.** Green exits are produced by: a runner over skipped or disabled tests
(`0 passed`), a filter that excluded everything (`N deselected`), a
self-skipping test that returns early (counted as a **pass**), and a
harness that rejects its own arguments while the wrapper around it still exits
0. Read the count, and compare it to what you expected.

**Zero is the flattest curve.** An instrument that measures nothing produces the
*most convincing possible* pass — a cost test whose meter returned 0 for
everything reports perfect flatness. No tolerance-tuning catches this; only a
liveness assertion does. Prove the instrument sees a known non-zero before you
believe any "flat", "no leak", or "no change".

**A delta is meaningful only against a null pair on the same machine on the same
day.** Two runs of *identical* code differed by 4.1%; four adjacent runs drifted
monotonically upward regardless of which code ran, so an A-then-B pair confounds
treatment with position. Quote the null band alongside the delta, or make no
claim. Wall-clock cannot separate "regression" from "the machine got busier" —
that is what counted measurements (keys read, calls issued, rows examined) are
for.

**The scope of the check is the usual defect, not its logic.** Repeatedly the
checker was correct and pointed at the wrong set: a parser tracking a syntax the
codebase had moved off (3 sites recognised out of 166), a sweep over one file
extension that did not recurse into subdirectories, a per-file scan where one
file held 13 instances. So a syntax-matching checker needs a **coverage
assertion**, not just correctness tests: report what fraction of the construct
it recognised, and treat near-zero recognition in a codebase that plainly uses
the construct as a **failure of the checker**. "Found nothing" and "cannot see
anything" must never print the same.

## Quick reference

- **Before trusting a gate, ask when it last ran.** A gate that never runs is
  indistinguishable from a gate that always passes.
- **Mutate to verify a detector.** Break the thing on purpose; confirm the
  detector goes red for *its own reason*, not incidentally.
- **A skip is where anyone can write anything and be believed.** Print skips and
  their reasons on every run, clean ones included.
- **Prefer a claim you can name a falsifier for** over a claim that is merely
  true.
- **Beware self-matching process checks.** `pgrep -f` / `pkill -f` match the
  shell running them, so they can never return false.
