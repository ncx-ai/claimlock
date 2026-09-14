---
name: design-lenses
description: Use when about to call a design, implementation, review, or investigation complete, when an assessment reads as uniformly positive, or when all tests pass and the work feels done
---

# Design Lenses

## Overview

A single lens applied well produces work that is correct and unusable, or fast
and wrong. Switching lenses finds what deepening one never will.

**"This is correct" and "this is fast" are different claims**, and passing tests
only ever support the first.

Claim integrity is enforced mechanically by `using-claimlock`.

## The output

One line per lens: what you checked, what you found — including the lenses that
found nothing, so a reader knows they were applied rather than skipped.

## The eight lenses

| Lens | The question | The tell you skipped it |
|---|---|---|
| **Correctness** | Does it produce the right answer? | Every test passes and you are ready to ship |
| **Scale** | What does it cost as data grows, on shared infrastructure? | Your fixtures are small enough that two implementations are indistinguishable — and you are reassured by them |
| **Concurrency** | What happens when two things happen at once? | The design was reasoned about with one actor in mind |
| **Falsifiability** | Could this test, guard, or metric have failed? | A green result you did not try to break |
| **Cost accounting** | Is the work measured and attributable to whoever caused it? | "It should be counted somewhere downstream" — said, not asserted |
| **Consumer experience** | Could someone build real things on this without first writing a wrapper? | The API's documentation is teaching the implementation |
| **Operability** | Can someone diagnose this at 3am without reading the source? | The failure mode is a silence |
| **Claim integrity** | Can you name the enforcement site for every promise? | The justification sounds reasonable and nobody has re-derived it |

## Quick reference

- **Uniformly positive assessment** → only one lens was applied. Go find the others.
- **Weight by domain.** Shared/multi-tenant infrastructure: scale, concurrency and cost accounting outrank convenience. A local CLI: consumer experience and operability do.
- **A lens that finds nothing is still worth the pass** — say you applied it.

## Why correctness tests cannot see cost

**The fixture that makes an answer easy to check is the fixture that makes the
cost defect invisible.** Three rows cannot distinguish an index seek from a full
scan. Cost defects need a fixture big enough for the two paths to diverge — the
opposite of what correctness testing pulls you toward.

## Two failures of SCOPE, not of judgement

Both produce a **wrong verdict from a correct observation**. Neither is caught by
applying a lens harder. They are caught by pointing it somewhere else.

### A measurement implicates a component. That component is now in scope.

A lens pass is almost always scoped to *the change in front of you*, and that is
the wrong scope the moment a measurement points at something you did not touch.

A load run showed a per-client concurrency ceiling that does not grow with
capacity — ten times the workers, still 8. The fact was observed, written into a
commit message, and then classified as "a deliberate fairness bound" because the
subsystem was not the one under review. The scale lens had already answered the
question; the consumer-experience lens answered it more sharply, since the
number is invisible to the developer it constrains. The lenses were not missing.
The **subject** was.

> When evidence implicates code you did not write in this session, that code is
> under review now. "Not my change" is a statement about authorship, not about
> correctness.

### Having a fix in hand decides the diagnosis

The strongest bias is not attachment to your own design — it is the
**availability of a remedy**. In the case above, a rewrite was already planned
that happened to make the symptom disappear, so a platform limit quietly became
"a thing applications should be written to avoid" rather than a defect.

**The tell: you are explaining how an application should be written to avoid a
platform limit the developer cannot see.** That is never a design. Contention and
physical layout belong to the platform, so a remedy that exists only in
application code is evidence the problem has been put on the wrong side of the
line.

Ask it explicitly: *if a competent developer hit this without knowing the
internals, what would they experience, and what could they do about it?* If the
honest answer is "nothing, because they cannot see it", it is a platform defect
— however convenient the application-side workaround happens to be.

### Both were supplied by a person, not by the process

Repeatedly, the missing lens arrived as a question from a human rather than from
a pass. That ratio is the argument for these two entries existing: the pass keeps
finding what it is aimed at, and keeps missing what it is not.

## Common mistakes

**Deepening instead of switching.** Finding nothing on the lens you are already
using is the expected outcome, not evidence.

**Treating them as sequential gates.** They are independent questions about one
artifact. A design can pass correctness and fail scale so badly it is unusable.

**Applying them only to code.** Tests, metrics and guards are artifacts too —
falsifiability most often fails on your own checks. A detector matching one
error shape and missing another reports a good test as weak.

**Stopping at the first finding.** The lens that found one usually has more.

## Real-world impact

A database aggregate engine passing 29 tests with every answer correct: the
scale lens found it read the whole table on every query and that its group
lookup was O(rows x groups); claim integrity found a comment in the source
arguing *for* the defect. All of it already reviewed and committed as green.
