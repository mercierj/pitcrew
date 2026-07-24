# Scheduled Codex tasks

Schedulers invoke namespaced Pitcrew skills from a configured project directory.
Every scheduled invocation is a single bounded pass and must emit the runtime
structured result.

## Research template

Run at a low, predictable cadence such as once per business day:

```text
Use $pitcrew:research-run for project getbill. Perform one bounded read-only pass,
record only high-confidence findings, then stop. Read applicable AGENTS.md
instructions and return a structured no-op if no grounded improvement is eligible.
```

## Reviewer template

Run after reviewable changes are expected, for example every two hours on workdays:

```text
Use $pitcrew:reviewer-run for project getbill. Review one eligible merge request,
respect GetBill approval gates, then stop. Return a structured no-op when none is
eligible.
```

## GetBill policy

For GetBill, keep schedules read-oriented unless the profile explicitly authorizes a
bounded action. Never schedule production or preproduction access implicitly.
GetBill release scheduling is disabled: release preparation and deployment remain
human-initiated, with the required approval for each remote action.
The operator must review the first few scheduled runs before enabling additional
acting roles.
