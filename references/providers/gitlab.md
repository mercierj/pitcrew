# GitLab provider contract

Use GitLab only when it is selected in project configuration. Verify the local
identity and configured GitLab host before mutations:

```sh
glab auth status --hostname "$GITLAB_HOST"
```

If access to the configured group/project is missing, return a blocked result; do
not redirect the work to another forge or tracker.

## Resolve the configured binding

Read these values only from the validated project configuration:

```sh
GITLAB_HOST=$(jq -r '.gitlab.host' "$CONFIG_FILE")
FORGE_USER=$(jq -r '.gitlab.user' "$CONFIG_FILE")
FORGE_OWNER=$(jq -r '.gitlab.owner' "$CONFIG_FILE")
GITLAB_PROJECT=$(jq -r '.gitlab.project_path' "$CONFIG_FILE")
GITLAB_PROJECT_ID=$(jq -r '.gitlab.project_id' "$CONFIG_FILE")
ASSIGNEE_USERNAME=$(jq -r '.gitlab.tracker.assignee_username // .gitlab.user' "$CONFIG_FILE")
TICKET_PREFIX=$(jq -r '.gitlab.tracker.ticket_prefix' "$CONFIG_FILE")
AGENT_LABEL=$(jq -r '.gitlab.tracker.labels.agent' "$CONFIG_FILE")
INVESTIGATE_LABEL=$(jq -r '.gitlab.tracker.labels.investigate' "$CONFIG_FILE")
QUICK_WIN_LABEL=$(jq -r '.gitlab.tracker.labels.quick_win' "$CONFIG_FILE")
BUG_LABEL=$(jq -r '.gitlab.tracker.labels.bug' "$CONFIG_FILE")
IMPROVEMENT_LABEL=$(jq -r '.gitlab.tracker.labels.improvement' "$CONFIG_FILE")
STATE_TODO=$(jq -r '.gitlab.tracker.states.todo' "$CONFIG_FILE")
STATE_PROCESSING=$(jq -r '.gitlab.tracker.states.processing' "$CONFIG_FILE")
STATE_REVIEW=$(jq -r '.gitlab.tracker.states.review' "$CONFIG_FILE")
STATE_BLOCKED=$(jq -r '.gitlab.tracker.states.blocked' "$CONFIG_FILE")
STATE_DONE=$(jq -r '.gitlab.tracker.states.done' "$CONFIG_FILE")
```

For GitLab issues, the generic `STATE_*_ID` values are the configured state-label
strings above. `TRACKER_TEAM`, `TRACKER_WORKSPACE`, and
`AGENT_BACKLOG_PROJECT_ID` all resolve to `GITLAB_PROJECT_ID`. Label IDs are not
required by GitLab; when a skill asks for one, resolve it from
`GET /projects/:id/labels` and fail closed if it does not exist.

Confirm the binding before acting:

```sh
glab api --hostname "$GITLAB_HOST" user
glab api --hostname "$GITLAB_HOST" "projects/$GITLAB_PROJECT_ID"
```

The authenticated username must equal `FORGE_USER`, and the returned
`path_with_namespace` must equal `GITLAB_PROJECT`.

## Tracker operations

Every `glab api` invocation must include `--hostname "$GITLAB_HOST"` and target
the numeric configured project ID. URL-encode query values.

- **List eligible work:** `GET /projects/:id/issues?state=opened&labels=<labels>`.
- **Inspect work:** `GET /projects/:id/issues/:iid`, then
  `GET /projects/:id/issues/:iid/notes` when comments are required.
- **Create work:** `POST /projects/:id/issues` with `title`, `description`,
  comma-separated `labels`, and the resolved assignee ID.
- **Comment:** `POST /projects/:id/issues/:iid/notes` with `body`.
- **Claim or transition:** fetch the issue first, preserve all non-state labels,
  replace the existing `pitcrew-state::*` label with the configured destination
  label, and `PUT /projects/:id/issues/:iid` with the complete label set.
- **Close lifecycle:** apply `STATE_DONE`, leave the required audit comment, then
  `PUT /projects/:id/issues/:iid` with `state_event=close`.

GetBill uses GitLab scoped lifecycle labels. Applying one
`pitcrew-state::*` value replaces the previous value in that scope. Never treat
the GitLab built-in `opened`/`closed` value as `processing`, `review`, or
`blocked`; those states are represented by labels while the issue stays open.
The `done` transition is both the configured done label and a closed issue.

Before creating a label, query the project labels and create only an exact
configured label that is absent. Never invent a label from ticket content.

## Merge requests and idempotency

For each requested branch change, locate an existing open merge request with the
same source branch and project before creating one. Update that merge request when
the requested operation is already represented. For comments, labels, and issues,
use an operation marker and search first. Following an uncertain result, query
GitLab before retrying so the run cannot create duplicates.

Map generic change capabilities as follows:

- **List changes:** `GET /projects/:id/merge_requests` with explicit `state`,
  `author_username`, `source_branch`, or `target_branch` filters.
- **Inspect change:** `GET /projects/:id/merge_requests/:iid`; use `/changes`,
  `/diffs`, `/notes`, and `/approvals` only when the role requires them.
- **Create change:** first query open merge requests with the same source branch,
  then `POST /projects/:id/merge_requests`.
- **Review change:** post one idempotency-marked note. Do not approve as the same
  identity that authored the merge request unless project policy explicitly
  permits self-approval.
- **Merge change:** re-fetch the merge request, pipeline, discussions, and
  expected head SHA immediately before `PUT /merge_requests/:iid/merge`.

For GetBill, normal code changes target `develop`. Treat merge requests targeting
`preprod` or `prod` as deployment changes: reviewer, validator, implementer, and
stale cleanup must not merge or mutate them unattended.
