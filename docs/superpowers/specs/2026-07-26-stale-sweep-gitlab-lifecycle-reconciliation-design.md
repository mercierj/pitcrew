# Stale-sweep GitLab lifecycle reconciliation

## Goal

Make `stale-sweep` repair every agent-managed GitLab issue whose lifecycle no
longer matches its merge request, including the two observed drift shapes:

- an open `pitcrew-state::blocked` issue whose related merge request was merged;
- an open `pitcrew-state::done` issue that was labelled terminal but never closed.

## Chosen approach

Keep the existing skill-driven architecture and make the smallest provider-aware
contract change. `stale-sweep` will query all five configured lifecycle labels
(`review`, `processing`, `todo`, `blocked`, and `done`) for open agent-managed
issues. It will inspect both merged and closed merge requests because GitLab
represents those as distinct states.

Alternatives rejected:

- Rely only on `implementer-run` closeout. This protects future merges performed
  by the implementer but cannot repair manual merges or interrupted closeouts.
- Add a new executable reconciliation service. This would duplicate the existing
  stale-sweep responsibility and add unnecessary runtime and state complexity.

## State mapping

- Related merge request is `merged`: preserve non-state labels, apply
  `pitcrew-state::done`, post one idempotent audit note, and close the issue.
- Related merge request is `closed` with no merge timestamp: preserve labels,
  apply `pitcrew-state::blocked`, and post one idempotent triage note.
- Related merge request is open or absent: leave the issue unchanged.
- Issue is already labelled `done` but still open: require the same merged-MR
  evidence before closing it.

For GitLab, `state=merged` with a non-null `merged_at` is the terminal merged
signal. GitLab normally leaves `closed_at` null on merged merge requests, so
`closed_at` must not be required for this classification.

## Safety and idempotence

- Continue to touch only issues carrying `pitcrew-agent`.
- Preserve every non-lifecycle label and the assignee.
- Search existing notes for an operation marker before posting.
- Re-read the issue after closeout and verify both built-in state `closed` and
  lifecycle label `pitcrew-state::done`.
- Never close an issue merely because it already carries the `done` label.

## Tests

Add contract tests that fail unless:

1. `stale-sweep` queries open `blocked` and `done` issues in addition to the
   existing states.
2. GitLab merged merge requests are queried explicitly and classified by
   `state=merged` plus `merged_at`, without requiring `closed_at`.
3. Successful stale reconciliation uses the provider `CLOSE_LIFECYCLE`
   operation and verifies that the issue is closed with the done label.
4. Notes use idempotency markers so repeated sweeps cannot duplicate audit
   comments.

The existing plugin validation and full repository test suite remain required
before reinstalling the local plugin.
