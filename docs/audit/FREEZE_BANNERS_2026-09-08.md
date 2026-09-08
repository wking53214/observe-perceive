# README freeze banners for the five kept repos

One banner per repo, to be inserted as the first thing after the title line
of each README. Each states what the repo is, why it is frozen, what still
works, and what unfreezes it. Every fact comes from the 2026-09-08 audit;
nothing is aspirational.

The decision they cite lives at
`observe-perceive/docs/audit/COMMERCIAL_RED_TEAM_2026-09-08.md`.

---

## fortress-kernel

```markdown
> **Frozen since 2026-09-08.** fortress-kernel is an optional containment
> pack for the governed action gate in
> [observe-perceive](https://github.com/wking53214/observe-perceive), not a
> product on its own. It bounds the slew and target of an automated actuator
> and keeps an audit chain of interventions. It passes its own suite with no
> sibling present (48 tests) and is consumed by the gate only when
> `fortress_controller` is set.
>
> No feature work here for the 90 days starting 2026-09-08. Bug fixes and
> dependency bumps are fine. It unfreezes when a paying gate customer needs
> magnitude containment on an action. Why: the gate is the wedge, and this
> is one of three optional packs behind it. See
> `docs/audit/COMMERCIAL_RED_TEAM_2026-09-08.md` in observe-perceive,
> Parts 18 and 35.
```

## AUGUR

```markdown
> **Frozen since 2026-09-08.** AUGUR is an optional pre-execution screen
> for the governed action gate in
> [observe-perceive](https://github.com/wking53214/observe-perceive), not a
> product on its own. It runs a seeded behavioural simulation and can only
> refuse, never approve. It passes its own suite with no sibling present
> (34 tests) and is consumed by the gate only when `simulation_screen=True`.
>
> No feature work here for the 90 days starting 2026-09-08. Bug fixes and
> dependency bumps are fine. It unfreezes when a paying gate customer has an
> action worth simulating before it runs. Why: the gate is the wedge, and
> this is one of three optional packs behind it. See
> `docs/audit/COMMERCIAL_RED_TEAM_2026-09-08.md` in observe-perceive,
> Parts 18 and 35.
```

## CCC

```markdown
> **Frozen since 2026-09-08.** CCC is an optional recurrence and continuity
> memory for the governed action gate in
> [observe-perceive](https://github.com/wking53214/observe-perceive), not a
> product on its own. It is dependency-free and passes its own suite with
> no sibling present (135 tests). The gate records into it through an
> adapter after a decision; nothing in the gate requires it.
>
> No feature work here for the 90 days starting 2026-09-08. Bug fixes and
> dependency bumps are fine. It unfreezes when a gate customer wants "we
> refused this shape before" as a feature, which the memo expects after the
> first pilots, not before. See
> `docs/audit/COMMERCIAL_RED_TEAM_2026-09-08.md` in observe-perceive,
> Parts 19 and 35.
```

## sentinel_os

```markdown
> **Frozen since 2026-09-08.** sentinel_os is the custody ledger stage of
> the governance stack: artifact origin, custody, and a Postgres-backed
> ledger. It is the second sellable unit after the governed action gate in
> [observe-perceive](https://github.com/wking53214/observe-perceive), and it
> is not deployable from a clone today: it declares no dependencies (the
> suite needs psycopg2, anthropic, httpx, redis, cryptography, yaml), it
> imports the Conservation Kernel package at module level, and without
> Postgres 228 tests skip and 39 fail. With those installed, 664 tests pass.
>
> The only work allowed here for the 90 days starting 2026-09-08 is a
> dependency manifest and a runnable Postgres fixture, so that the next
> audit can execute the ledger path. No feature work. It unfreezes when a
> gate customer faces an examination or dispute that needs custody
> reconstruction, which is the ledger's economic trigger. See
> `docs/audit/COMMERCIAL_RED_TEAM_2026-09-08.md` in observe-perceive,
> Parts 18, 19 and 27.
```

## GSA-815

```markdown
> **Frozen since 2026-09-08.** GSA-815 is the execution side of the custody
> ledger stage and sells only together with
> [sentinel_os](https://github.com/wking53214/sentinel_os), which it
> vendors as a git submodule at `vendor/sentinel_os`. Without
> `git submodule update --init` 17 test files do not collect; with the
> submodule and Postgres and Redis available, 121 tests pass. Running
> `pytest` from the repo root crashes the collector
> (`gsa-governance-core/test_harness.py` raises SystemExit); CI runs
> `pytest Tests/` and so should you. The governed action gate in
> observe-perceive never imports this repo: "GSA-815" in the chain diagram
> is a callable the caller supplies.
>
> The only work allowed here for the 90 days starting 2026-09-08 is a
> dependency manifest and replacing the submodule with a dependency on a
> published sentinel_os. No feature work. It unfreezes with sentinel_os.
> See `docs/audit/COMMERCIAL_RED_TEAM_2026-09-08.md` in observe-perceive,
> Parts 18, 19 and 27.
```

---

## Applying them

Run from `~` with the five repos checked out and up to date. It inserts the
banner after the first line of each README (the title), commits, and pushes
to the default branch. Push the fortress-kernel patch first if you have not.

```bash
cd ~/fortress-kernel && git am ~/0001-Blending-coefficient-test-assert-the-coefficient-is-.patch && git push   # if not yet done

apply_banner () {   # $1 repo dir, $2 banner file
  cd ~/"$1" || return 1
  git pull -q
  { head -1 README.md; echo; cat "$2"; echo; tail -n +2 README.md; } > README.new && mv README.new README.md
  git add README.md && git commit -q -m "README: frozen since 2026-09-08, and why" && git push -q
  echo "$1 done"
}
# Save each fenced block above to ~/banners/<repo>.md (without the ``` fences), then:
for r in fortress-kernel AUGUR CCC sentinel_os GSA-815; do apply_banner "$r" ~/banners/"$r".md; done
```

fortress-kernel's default branch is `master`, not `main`; `git push` with no
arguments handles that. innovation_os had the same quirk but is archived.
