# pip issue #12999: diagnostic fix plan

Status: design/review note, not an implementation and not an upstream proposal yet.

Issue: https://github.com/pypa/pip/issues/12999

Base reviewed: `kaaburgh/pip@a7002c9771a6c3f0317a4e6b9fbdcd22e643f7b6` (same pip main state reviewed on 2026-10-02).

Related prototype branch discussed in the issue:
https://github.com/shashb27/pip/tree/12999-only-binary-error-message

This note records the current plan after an independent review of the first proposal. Its purpose is to make the intended behavior, the reason for the design, and the positive/negative validation cases explicit before code is written.

## 1. Problem to solve

With a binary-only policy such as:

```
pip install --only-binary :all: ...
```

dependency resolution can fail because a dependency has a usable source distribution but no usable wheel for the current environment. Today the final error can look like an ordinary dependency conflict and can spend substantial output on intermediate packages.

Issue #12999 asks for a more useful explanation. The important part, clarified in the discussion, is not necessarily to tell the user to remove `--only-binary`. It is to identify the leaf package for which no matching binary distribution is available while a source distribution exists but is excluded by the active format policy.

The diagnostic must be conservative. It must not blame `--only-binary` when the candidate would still be unusable because of version constraints, Requires-Python, platform tags, hashes, a direct URL/explicit candidate path, or another independent restriction.

## 2. Scope

The first patch should be a diagnostics-only change.

It should:

- keep resolver behavior and candidate ordering unchanged;
- keep successful-install behavior unchanged;
- avoid adding network/index traffic to successful installs;
- ideally avoid a second index traversal even on failure;
- add a precise format-control explanation only when pip has evidence for it;
- avoid claiming that removing `--only-binary` would make the whole dependency graph resolvable.

It should not, in the first patch:

- redesign resolvelib or candidate selection;
- reconstruct a full dependency path such as `pdr -> dustgoggles -> cytoolz`;
- suppress or rewrite all existing conflict reporting;
- claim that the format flag is the unique/global cause of `ResolutionImpossible`;
- broadly redesign the existing "Additionally, ... no matching distributions" logic;
- change how pip handles builds, wheels, sdists, tags, or Requires-Python.

Support for `--no-binary` is optional. It is symmetric in concept, but it should not enlarge the first patch if wheel-specific platform/tag behavior makes the implementation or validation materially more complex.

## 3. Current architecture relevant to the fix

### 3.1 Format filtering happens in LinkEvaluator

`LinkEvaluator.evaluate_link()` already knows when a link is being rejected specifically because the active format control disallows it.

Examples include the equivalent of:

- binaries are not allowed for this project;
- sources are not allowed for this project.

At this point pip is looking at a concrete link.

Other rejection reasons are separate, including wheel platform/tag mismatch and Requires-Python mismatch.

The current `LinkType.format_unsupported` category is broader than "rejected by FormatControl", so the implementation may need a more precise internal reason/record if error reporting is to distinguish policy rejection from other format-related rejection.

### 3.2 PackageFinder has already seen the links

`PackageFinder.find_all_candidates(project_name)` collects links and evaluates them. Links rejected by the binary/source policy disappear from the candidate set returned to the resolver.

That means an error-only helper that performs a fresh permissive project search is not the only way to answer the diagnostic question. The original finder pass has already seen the relevant links.

### 3.3 CandidateEvaluator applies requirement-level selection later

The finder-level accepted candidates are still not the same as "candidates for this concrete requirement".

`CandidateEvaluator` applies, among other things, the requirement specifier and hashes/release selection.

This distinction matters for correctness.

### 3.4 Resolver candidate sets can combine multiple requirements

In `Factory._iter_found_candidates()`, the real candidate set for an identifier is built from the intersection of all applicable `InstallRequirement` specifiers plus constraints and hashes.

Therefore, checking one individual requirement is not enough to prove:

> "the resolution failed because of --only-binary"

At most, error reporting can safely prove a local statement such as:

> "For requirement dep>=2, a matching source distribution exists, but source distributions are excluded by the current binary-only setting."

That is still useful, and it is much safer than claiming that changing the flag would solve the whole graph.

### 3.5 Explicit candidates and URL constraints are a separate path

`Factory.find_candidates()` can use explicit candidates from requirements and links from constraints instead of the normal index-finder candidate universe.

A finder-based availability diagnostic must therefore not assume that every resolution failure for a project can be explained by index candidates.

The helper should bail out, or otherwise explicitly handle, cases where explicit candidates/direct URLs/link constraints are materially involved.

This is related to the class of false diagnostics discussed around pip issue #14193 / PR #14194.

## 4. Existing prototype and why it should not be copied as-is

The branch currently linked from #12999 implements a useful proof of concept:

- it allows a `PackageFinder` lookup with binary/source format restrictions disabled;
- it compares the normal restricted project candidate view with a permissive-format project view;
- if the restricted view is empty and the permissive view is non-empty, it reports a format-specific explanation;
- it includes positive functional tests.

The direction is valuable, but the project-level comparison is insufficiently precise.

### 4.1 False positive: wrong version exists only as sdist

Suppose the requirement is:

```
dep==2.0
```

and the index contains only:

```
dep-1.0.tar.gz
```

under `--only-binary :all:`.

A project-level check sees:

- restricted candidate view: empty;
- permissive-format project view: non-empty.

It can therefore incorrectly report that binary-only policy explains the missing candidate.

But `dep==2.0` is impossible even without the binary-only setting.

### 4.2 False negative: an unrelated wheel masks the matching sdist

Suppose the requirement is:

```
dep==2.0
```

and the index contains:

```
dep-1.0-py3-none-any.whl
dep-2.0.tar.gz
```

under `--only-binary :all:`.

A project-level restricted view is non-empty because wheel 1.0 exists, so the prototype can omit the format explanation.

But for the actual requirement `dep==2.0`, the source distribution is exactly the relevant distribution being excluded.

### 4.3 A concrete Requirement still does not prove global causality

Even if the diagnostic is improved to consider the specifier of one requirement, there can be another requirement or constraint for the same identifier that conflicts independently.

Example:

```
A -> dep>=2
B -> dep<2

dep-1.0-py3-none-any.whl
dep-2.0.tar.gz
--only-binary :all:
```

For `dep>=2`, it is true that a matching sdist is excluded by binary-only policy.

But removing `--only-binary` would not make the full graph resolvable because `dep>=2` and `dep<2` still conflict.

This is why the message should report a local observed fact instead of prescribing "remove --only-binary to fix the resolution".

## 5. Recommended design

### 5.1 Preserve links rejected specifically by FormatControl

During the normal link-evaluation pass, preserve enough structured information to know that a concrete link was rejected specifically because the active binary/source format control disallowed it.

Possible implementations include:

- a more precise `LinkType` for FormatControl rejection; or
- a separate internal collection of format-control-rejected links.

The exact representation is an implementation detail. The important invariant is that error reporting can distinguish:

```
this link was rejected because source/binary is forbidden by FormatControl
```

from other reasons such as an unsupported wheel tag or invalid format.

Do not make this diagnostic state alter candidate selection.

### 5.2 Re-evaluate the same rejected links, not the whole project index

On the error-reporting path only:

1. obtain the links for the relevant project that were rejected specifically by FormatControl;
2. re-run those same links through a `LinkEvaluator` with both `binary` and `source` allowed;
3. keep all the other normal link-level checks active.

This answers the counterfactual narrowly:

> "If the format-control restriction alone were removed for this exact link, would the link pass the other LinkEvaluator checks?"

This has advantages over an uncached `find_all_candidates_ignoring_formats(project_name)` pass:

- no second index/network traversal;
- no race where the index changes between resolution and error formatting;
- no need to duplicate project collection work;
- fewer diagnostic side effects;
- the diagnostic is tied to links pip actually observed during this resolution attempt.

Re-evaluating is important because the original restricted pass may have stopped at the format-control rejection before reaching later checks such as Requires-Python or wheel compatibility.

### 5.3 Apply the concrete requirement filter before reporting a hint

A permissively re-evaluated link must still match the concrete requirement being discussed.

For normal specifier requirements, apply the same relevant requirement-level filtering as far as practical, including the requirement specifier and hashes/release rules used by the finder/evaluator.

The diagnostic condition should be conceptually:

```
there is no usable candidate for this requirement in the restricted view
AND
at least one link rejected specifically by FormatControl
becomes an otherwise usable candidate for this requirement
when only the format restriction is relaxed
```

This is deliberately requirement-local, not a claim about the entire resolver criterion.

### 5.4 Be conservative around explicit candidates and constraints

Do not emit a finder-based format-availability hint when the relevant resolution path is an explicit/direct URL candidate and the index is not authoritative for that requirement.

Also account for URL/link constraints, which can introduce explicit candidates even when the cause shown by resolvelib is not itself an `ExplicitRequirement`.

If the implementation cannot establish this safely with the state available at error-reporting time, prefer no format hint.

A missing hint is better than a confidently wrong hint.

### 5.5 Message only what was proven

Preferred wording is factual and local. For example:

```
No matching binary distribution was found for cytoolz.
A matching source distribution is available, but source distributions
are excluded by the current --only-binary setting.
```

Or, if it integrates better with the existing conflict block:

```
cytoolz: no matching binary distribution was found; a matching source
distribution is available but is excluded by --only-binary
```

Avoid stronger wording such as:

```
Resolution failed because --only-binary ...
```

and avoid prescriptive wording such as:

```
Remove --only-binary to fix this.
```

unless the implementation actually proves the whole graph would resolve, which this patch should not try to do.

The term "matching binary distribution" is preferable to a vague "compatible binary package" because the diagnostic is intended to account for version/environment selection, not merely file format.

### 5.6 Placement in the existing error

For a single-requirement "no distribution found" path, add the format explanation next to the existing no-matching-distribution diagnostics.

For multi-cause `ResolutionImpossible`, append one concise format-specific reason for the affected requirement/project rather than repeating the same explanation for every parent version that led to the same leaf dependency.

Do not attempt a general rewrite of all parent/conflict lines in the first patch.

## 6. Validation strategy

The core invariant is:

> Resolution behavior must not change. Only post-failure diagnostics should change.

Tests must cover both "show the hint when true" and, more importantly, "do not show the hint when a nearby but different condition is true".

### 6.1 Positive: basic sdist-only dependency

Graph:

```
parent -> dep==1.0
```

Available:

```
dep-1.0.tar.gz
```

Command uses `--only-binary :all:`.

Expected:

- resolution fails as before;
- output contains the new source-excluded/binary-missing explanation.

### 6.2 Positive: backtracking/multiple parent versions

Several versions of a parent all depend on the same sdist-only leaf dependency.

Expected:

- the resolver still backtracks exactly as before;
- the final diagnostic identifies the leaf format problem;
- the new format explanation is not duplicated once per parent version.

This models the motivating `pdr -> dustgoggles -> cytoolz` shape.

### 6.3 Negative: only the wrong sdist version exists

Requirement:

```
dep==2.0
```

Available:

```
dep-1.0.tar.gz
```

With `--only-binary`.

Expected:

- no format-control hint;
- removing the format restriction would not produce a matching version.

This is the key false-positive guard against the existing prototype.

### 6.4 Positive: unrelated wheel plus matching sdist

Requirement:

```
dep==2.0
```

Available:

```
dep-1.0-py3-none-any.whl
dep-2.0.tar.gz
```

With `--only-binary`.

Expected:

- format-control hint is present.

This is the key false-negative guard against the existing prototype.

### 6.5 Negative: ordinary dependency/version conflict

Available compatible wheels exist, but two parents require mutually incompatible versions.

Expected:

- normal dependency-conflict output;
- no format-control explanation.

### 6.6 Positive: matching sdist plus incompatible wheel

For the required version, provide:

- a source distribution that is otherwise usable;
- a wheel that is not compatible with the target Python/platform tags.

With `--only-binary`.

Expected:

- format-control hint is present because no matching usable wheel exists while the source distribution would otherwise be usable.

This approximates the motivating new-Python-version scenario where wheels may exist for other ABIs but not for the current environment.

### 6.7 Negative: sdist fails Requires-Python

Provide a source distribution for the requested version whose Requires-Python metadata excludes the current interpreter.

With `--only-binary`.

Expected:

- no claim that binary-only policy is the reason a usable source candidate is unavailable;
- the existing Requires-Python diagnostic remains authoritative.

The re-evaluation with permissive formats is important for catching this case.

### 6.8 Positive: package-specific format control

Use a package-specific setting such as:

```
--only-binary=dep
```

Expected:

- the hint is emitted for `dep`;
- unrelated packages are unaffected.

This verifies that the implementation uses `FormatControl` semantics rather than merely checking whether the command line contained `:all:`.

### 6.9 Negative: no format restriction

Run the same graph without `--only-binary`.

Expected:

- normal source candidate behavior;
- no format-control diagnostic;
- no regression in candidate selection.

### 6.10 Negative: explicit/direct URL requirement

Exercise a direct URL/explicit candidate path where normal index candidates are irrelevant.

Expected:

- no misleading finder-based "matching source distribution exists" hint.

### 6.11 Negative: URL/link constraint

Exercise a named requirement whose candidate is constrained by an explicit link/URL.

Expected:

- no hint inferred solely from unrelated index contents.

### 6.12 Optional symmetric `--no-binary` coverage

Only if the first patch intentionally supports `--no-binary`, mirror the important positive and negative cases with wheel-only distributions and verify platform-tag handling.

If that symmetry increases scope substantially, defer it.

## 7. Unit and functional test split

Use low-level/unit tests for the diagnostic mechanism itself:

- FormatControl rejection is recorded distinctly;
- re-evaluating an exact rejected link with formats enabled preserves all other checks;
- wrong-version links do not become a matching diagnostic candidate;
- Requires-Python/platform failures do not get misclassified as format-control availability.

Use functional resolver tests for user-visible behavior:

- basic source-only leaf;
- backtracking/multiple parent versions;
- unrelated wheel + matching sdist;
- ordinary conflict negative case;
- explicit/direct URL negative case;
- package-specific `--only-binary`.

Functional tests should assert both presence of the intended message and absence of the message in negative scenarios.

## 8. Regression/performance invariants

Before considering the patch correct, verify:

1. Candidate ordering is unchanged.
2. Resolver inputs are unchanged.
3. The selected candidate for successful installs is unchanged.
4. Successful installs perform no additional candidate/index scan for this diagnostic.
5. Failure diagnostics do not trigger a fresh project index traversal if the rejected-link design is used.
6. Existing Requires-Python diagnostics remain unchanged when they are the real reason.
7. Existing unsupported-platform-wheel behavior remains unchanged.
8. Existing hash and constraint behavior remains unchanged.
9. The new diagnostic is absent unless a format-control-rejected link can be shown to satisfy the local requirement after only the format restriction is relaxed.

## 9. Why this design is preferred

The key distinction is between:

```
"there are some files for this project if format filtering is disabled"
```

and:

```
"pip saw this exact distribution link, rejected it specifically due to the
active binary/source policy, and the same link would otherwise be a matching
candidate for the requirement being reported"
```

The second statement is materially stronger and can be tested without asking the resolver to solve a different graph.

It also keeps the diagnostic honest: it reports what pip observed, not what pip speculates would happen if the user changed command-line options.

## 10. Open implementation decisions

These should be decided while writing the patch, not assumed in advance:

- whether a dedicated `LinkType` or a separate rejected-link record gives the cleanest internal API;
- where to store the rejected-link diagnostic data so it follows existing PackageFinder lifetime/caching semantics;
- how much of `CandidateEvaluator` should be reused for the local requirement check instead of duplicating filtering logic;
- how to identify/bail out for explicit candidates and link constraints using the state already held by `Factory`;
- whether the message belongs in the existing "Additionally..." block or next to each affected cause;
- whether `--no-binary` should be included in the first patch.

The implementation should prefer existing filtering code over reimplementing version/hash/prerelease rules.

## 11. Acceptance criteria

A proposed implementation is ready for upstream review only if all of the following are true:

- it reproduces #12999 with a clearer leaf-package diagnostic;
- it passes the wrong-version negative test;
- it passes the unrelated-wheel + matching-sdist positive test;
- it does not mislabel Requires-Python or platform incompatibility;
- it does not misdiagnose explicit/direct-URL paths;
- it does not alter successful resolution;
- it does not add avoidable network/index work;
- the message states only the local fact the code can prove;
- the author can explain the candidate-flow and every new test without relying on the generated plan itself.

## 12. Notes on contribution process

The repository's `AI_POLICY.md` permits use of LLM-based development tools only with full human ownership and understanding of the contribution. This plan is therefore intentionally a review/design aid rather than a substitute for understanding the relevant pip code.

Before opening an upstream PR:

- re-read the final implementation and tests manually;
- run the focused and relevant broader test suites;
- verify the current state of issue #12999 and any new PRs to avoid duplicate work;
- be prepared to explain why every new diagnostic is true and why every negative case remains silent.
