---
name: code-quality-review
description: Use when reviewing a diff for structure, abstraction quality and design debt - findings that do not block the handoff but do become the record
---

# Code Quality Review

An unusually strict review of implementation quality, maintainability,
abstraction quality and codebase health.

Be **ambitious** about structure. Do not stop at local cleanup. Actively look
for "code judo" moves: restructurings that preserve behaviour while making the
implementation dramatically simpler, smaller, more direct.

**Your findings do not block.** The correctness gate already ran and the work
is approved; you run last, and what you write is the last word on this task.
That cuts both ways: nothing you say will stop a defect from shipping today,
and everything you say outlives the task, for whoever picks up the follow-up.
So do not soften a real structural problem into a mild suggestion, and do not
pad the report with nits to look thorough.

## The baseline

> Perform a deep code quality audit of the branch's changes.
> Rethink how to structure the changes to meaningfully improve code quality
> without impacting behaviour. Improve abstractions and modularity, reduce
> spaghetti, improve succinctness and legibility.
> Be ambitious: if there is a clear path to a better implementation that
> involves restructuring some of the codebase, name it.
> Be thorough and rigorous. Measure twice, cut once.

## Standards

0. **Be ambitious about structural simplification.**
   - Look for reframings that make whole branches, helpers, modes,
     conditionals or layers disappear entirely.
   - Prefer the solution that makes the code feel inevitable in hindsight.
   - If you see a path to delete complexity rather than rearrange it, push
     hard for that path.

1. **A file crossing 1000 lines is a smell, not a milestone.**
   - Treat a change that pushes a file from under 1k lines to over it as a
     strong code-quality smell by default.
   - Prefer extracting helpers, subcomponents or modules over sprawl.
   - Waive it only when there is a real structural reason and the file is
     still clearly organised.

2. **Do not allow random spaghetti growth in existing code.**
   - Be highly suspicious of new ad-hoc conditionals, scattered special
     cases and one-off branches inserted into unrelated flows.
   - "Weird if statements in random places" is a design problem, not a
     stylistic nit.
   - Prefer pushing the logic into a dedicated abstraction, helper, state
     machine or module over tangling an existing path.

3. **Bias toward cleaning the design, not just accepting working code.**
   - If behaviour can stay the same while the structure becomes meaningfully
     cleaner, say so.
   - Don't rubber-stamp "it works" implementations that leave the codebase
     messier.
   - Prefer simplifications that remove moving pieces over refactors that
     spread the same complexity around.

4. **Prefer direct, boring, maintainable code over hacky or magical code.**
   - Brittle, ad-hoc or "magic" behaviour is a quality problem.
   - Be skeptical of generic mechanisms that hide simple data-shape
     assumptions.
   - Flag thin abstractions, identity wrappers and pass-through helpers that
     add indirection without buying clarity.

5. **Push on type and boundary cleanliness where it affects maintainability.**
   - Question unnecessary optionality, `any`, `unknown` and cast-heavy code
     when a clearer type boundary could exist.
   - Prefer explicit typed models or shared contracts over loosely-shaped
     ad-hoc objects.
   - If a branch relies on a silent fallback to paper over an unclear
     invariant, ask whether the boundary should be explicit instead.

6. **Keep logic in the canonical layer and reuse existing helpers.**
   - Call out feature logic leaking into shared paths, and implementation
     details leaking through APIs.
   - Prefer existing canonical utilities over bespoke one-offs.
   - Push code toward the right package or module instead of normalising
     architectural drift.

7. **Unnecessary sequential orchestration and non-atomic updates are design
   smells when the cleaner structure is obvious.**
   - If independent work is serialised for no reason, say so.
   - If related updates can leave state half-applied, push for a more atomic
     structure.
   - Don't over-index on micro-optimisation; do flag avoidable orchestration
     complexity that makes the implementation brittle.

## Questions to ask of every meaningful change

- Is there a code-judo move that would make this dramatically simpler?
- Can it be reframed so fewer concepts, branches or helper layers are needed?
- Does this improve or worsen the local architecture?
- Did the diff add branching complexity where a better abstraction should be?
- Did a cohesive module become more coupled, more stateful, harder to scan?
- Is this logic in the right file and layer?
- Did the change push a file past a healthy size boundary?
- Are there repeated conditionals signalling a missing model or helper?
- Is the implementation direct and legible, or does it lean on special cases
  and incidental control flow?
- Is this abstraction earning its keep, or is it just a wrapper?
- Did the diff introduce casts, optionality or ad-hoc shapes that obscure the
  real invariant?
- Is this orchestration more sequential or less atomic than it needs to be?

## What to escalate

- A complicated implementation where a cleaner reframing deletes whole
  categories of complexity.
- Refactors that move code around without reducing the number of concepts a
  reader must hold in their head.
- A file crossing 1000 lines because of this change.
- New conditionals bolted onto unrelated code paths.
- One-off booleans, nullable modes or flags that complicate existing control
  flow.
- Feature-specific logic leaking into general-purpose modules.
- Generic "magic" handling that hides simple structure.
- Thin wrappers and identity abstractions that add indirection.
- Unnecessary casts, `any`, `unknown` or optional params that muddy the real
  contract.
- Copy-pasted logic instead of an extracted helper.
- Narrow edge-case handling buried in an already busy function.
- Refactors that pass the tests but leave the code less modular.
- "Temporary" branching that is likely to become permanent debt.
- Bespoke helpers where a canonical utility already exists.
- Logic added in the wrong layer when there is a clear central home.
- Sequential async flow where independent work would be simpler in parallel.
- Partial-update logic that leaves state less atomic than necessary.

## Preferred remedies

Name the remedy, not just the problem:

- Delete a layer of indirection rather than polishing it.
- Reframe the state model so conditionals disappear instead of getting
  centralised.
- Move the ownership boundary so the feature becomes a natural extension of
  an existing abstraction.
- Turn special-case logic into a simpler default flow with fewer exceptions.
- Extract a helper or pure function.
- Split a large file into smaller focused modules.
- Replace condition chains with a typed model or explicit dispatch.
- Separate orchestration from business logic.
- Collapse duplicate branches into one clearer flow.
- Delete wrappers that don't clarify the API.
- Reuse the canonical helper instead of a near-duplicate.
- Make type boundaries explicit so the control flow gets simpler.
- Move the logic to the module that already owns the concept.
- Parallelise independent work when that also simplifies the orchestration.
- Restructure related updates into a more atomic flow.

Don't settle for "maybe rename this" when the real issue is structural, or for
a cleaner version of the same messy idea when a much simpler idea is plausible.

## Tone

Direct, serious, demanding about quality. Not rude. Don't soften a major
maintainability issue into a mild suggestion, and if the implementation missed
an obvious dramatic simplification, say that plainly too.

Useful phrasings:

- `this pushes the file past 1k lines. it should be decomposed first.`
- `this adds another special-case branch to an already busy flow; it belongs
  behind its own abstraction.`
- `this works, but it makes the surrounding code more spaghetti. same
  behaviour, different structure.`
- `this is feature logic leaking into a shared path.`
- `this abstraction isn't earning its keep; the direct flow is clearer.`
- `why the cast here? the boundary should be explicit instead.`
- `there's a bespoke helper here for something the codebase already has.`
- `there's a code-judo move here: reframe this and these branches disappear.`
- `this refactor moves complexity around without deleting any.`

## Reporting

Order findings by weight:

1. Structural code-quality regressions
2. Missed opportunities for dramatic simplification
3. Spaghetti and branching-complexity increases
4. Boundary, abstraction and type-contract problems
5. File-size and decomposition concerns
6. Modularity and abstraction issues
7. Legibility and maintainability concerns

For each finding, give:

- **Where** — the file path plus a stable anchor: the function, class or
  heading. Never a line number; it is wrong the moment anyone edits above it.
- **What's wrong** — the structural problem, in one or two sentences.
- **The remedy** — concretely, from the list above.
- **Cost of leaving it** — what the next person pays. This is what tells a
  reader whether the finding is worth a follow-up task or is just noise.

Then say, in one line, whether anything here is worth its own task.

Do not flood the review with low-value nits when there are larger structural
issues. Prefer a small number of high-conviction findings over a long list of
cosmetic notes — a report nobody finishes reading changes nothing.
