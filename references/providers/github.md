# Native GitHub provider contract

Use this contract only for the matched pair
`providers.forge=github` and `providers.tracker=github`.

Resolve `GITHUB_HOST`, `GITHUB_USER`, `GITHUB_OWNER`,
`GITHUB_REPOSITORY`, tracker labels, and tracker states only from the validated
configuration. Verify:

```sh
gh auth status --hostname "$GITHUB_HOST"
gh api --hostname "$GITHUB_HOST" user
gh api --hostname "$GITHUB_HOST" "repos/$GITHUB_REPOSITORY"
```

The authenticated login, returned owner, and full repository name must match
the configuration exactly. Never fall back to another host, owner, repository,
or tracker.

Before an acting role starts, list repository labels and verify every configured
tracker state/routing label exists exactly. A missing label returns a structured
no-op; runs never create or approximate labels.

## Tracker operations

- **List eligible work:** `GET /repos/:owner/:repo/issues` with explicit state
  and labels; discard entries containing a `pull_request` field.
- **Inspect work:** `GET /repos/:owner/:repo/issues/:number`, then its comments.
- **Claim or transition:** re-fetch first, preserve every non-state label,
  replace only the configured state label, then PATCH the complete label list.
- **Close lifecycle:** apply the configured done label, post the idempotency-
  marked audit comment, PATCH `state=closed`, and verify both conditions.

## Pull-request operations

- **Create change:** lookup-before-create by exact head repository and branch;
  reuse an open PR when it represents the same ticket operation.
- **Read checks:** inspect the current head SHA and required check runs.
- **Merge change:** re-fetch PR, reviews, checks, discussions, and expected head
  SHA immediately before squash merge; delete only the confirmed source branch.

Every comment, transition, PR, and closeout carries an operation marker. After
an uncertain response, re-read before retrying.
