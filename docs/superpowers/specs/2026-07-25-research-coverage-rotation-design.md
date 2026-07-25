# Research Coverage Rotation Design

## Goal

Make repeated `research-run` executions advance through different repository
areas instead of only rotating between repository/mode cells.

## Decision

Keep the existing one-cell-per-run scheduler and add a deterministic coverage
cursor inside each cell. A scan cell derives a bounded list of tracked source
areas from the repository checkout. Each run selects the least recently scanned
area for that cell, records the selected area and a scan epoch in
`researcher-state.json`, and starts a new epoch after all areas have been
visited. Existing state files remain valid and migrate lazily when the new
coverage fields are first written.

The scan prompt will include the selected area as a scope constraint. Findings
remain deduplicated by the existing stable ledger key, so revisiting an area in
a later epoch updates occurrences rather than creating duplicate work.

## Alternatives considered

- Rotate by top-level directories: recommended. It is deterministic, cheap,
  understandable, and resilient to file churn.
- Rotate by individual files and Git fingerprints: rejected for now because it
  is more expensive and would make coverage state noisy when files are renamed
  or generated content changes.

## State shape

Existing cell fields are preserved. New optional fields are:

```json
{
  "coverage": {
    "areas": ["src", "tests"],
    "next_area": "src",
    "epoch": 0,
    "visited": {"src": "2026-07-25T12:00:00Z"}
  }
}
```

The implementation must tolerate missing, malformed, or stale coverage data by
rebuilding it from the current checkout. Areas are Git-tracked top-level
directories containing relevant code or documentation; if none can be found,
the repository root is used.

## Verification

Contract tests will verify that the research skill documents the cursor and
scope behavior. Pure Python tests will verify deterministic area discovery,
oldest-area selection, epoch rollover, and legacy-state migration.
