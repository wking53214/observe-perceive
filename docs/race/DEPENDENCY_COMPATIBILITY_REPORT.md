# Dependency compatibility report: CNS v2.3.0

Evidence classes: EXECUTED (run), INSPECTED (read in source or CI), DOCUMENTED (stated in prose), UNKNOWN.

## What failed

CI run 137 on `main` at `bcc5697` (PR #68) concluded `failure`. The `test (3.11)` and `test (3.12)` jobs passed. Both `cns` jobs failed at **Install**, so their test step was skipped and the CNS integration tests did not run. (EXECUTED, CI.)

Resolver message (EXECUTED, CI log): `Cannot install cns 2.3.0 (from CNS.git@v2.3.0) and cognitive-continuity-constitution[cns]==0.1.0 because these package versions have conflicting dependencies. cognitive-continuity-constitution[cns] 0.1.0 depends on cns 2.2.0 (from CNS.git@v2.2.0)`.

## Cause (INSPECTED, each library's `pyproject.toml` at the pinned commit)

`requirements-demo.txt` installed four libraries with their `[cns]` extra, at commits whose extra pins an older CNS:

| Library | Commit pinned in the demo | CNS its `[cns]` extra pins there | Pin on its current main |
|---|---|---|---|
| Governance Gateway | `ad8b644` | v2.2.0 | v2.3.0 |
| CCC | `9806573` | v2.2.0 | v2.3.0 |
| DIT | `c801374` | v2.2.0 | v2.3.0 |
| Conservation Kernel | `4e7c0d9` | v2.2.0 | v2.3.0 |

The file's own comment said these commits pinned v2.3.0. That was false. The resolver reports the first conflict, so CCC was named, but all four conflict.

## Real incompatibility or restrictive pin?

Restrictive pins, on the evidence available:

* CNS's changelog describes v2.3.0 as additive: optional arguments, new functions and files, the `cns` package shapes unchanged. (DOCUMENTED.)
* With the kernel at `4e7c0d9` and CNS at the v2.3.0 commit `41592fb` on `PYTHONPATH`, this repository's suite passes (975 passed, 62 skipped, 5 xfailed). (EXECUTED locally. The skips are sibling libraries not installed, so this does not reproduce the CI `cns` job.)
* A `[cns]` extra only adds the `cns` requirement, which `requirements-demo.txt` already states directly. Each library's connector imports `cns` only when it is used.

## The fix, and what it does not do

`requirements-demo.txt` installs the four libraries at the same tested commits **without** their `[cns]` extras. This is the treatment AUGUR already had for the same reason. CNS comes from the single explicit v2.3.0 line.

Not done, deliberately:

* The libraries were not moved to newer commits. That brings unrelated changes and needs its own review.
* The Conservation Kernel cannot follow its `main` yet. Every kernel commit that pins CNS v2.3.0 (`c51a246` and later) also makes an `evidence_guard` mandatory when a kernel is built; this repository's `ConservationKernel(allow_unguarded_roots=True)` and its examples then raise `UnguardedKernelError`. (EXECUTED against kernel main.)
* No constraint was loosened and no coverage was removed.

## Other changes in this PR

* README and the `pyproject.toml` comment no longer state v2.2.0 as the pin.
* The default `test` job and the future-clock pass now run with `-rs`, so skipped tests are listed in the CI log. The `cns` job already did, and sets `OBSERVE_PERCEIVE_REQUIRE_CNS=1` so a missing library is an error there, not a skip.
* One test changed (see the PR description): the demo-requirements test asserted the `[cns]` extra on four libraries.

## Not verified here

A clean install from a fresh environment and a passing `cns` job. The libraries are private and this environment cannot install them. The CI run on this PR is the verification, and the PR description reports it.
