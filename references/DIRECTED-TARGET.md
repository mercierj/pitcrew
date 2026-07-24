# Directed targets

Acting skills normally discover one eligible item from the configured providers. A
caller may instead name one target. Directed mode changes only item selection: all
identity, scope, approval, review, validation, and release gates still apply.

## Accepted forms

```text
GitHub PR: https://github.com/<owner>/<repo>/pull/<number>
GitLab MR: https://<host>/<group>/<project>/-/merge_requests/<number>
Linear issue: https://linear.app/<workspace>/issue/<id>
GitLab issue: https://<host>/<group>/<project>/-/issues/<number>
Short change: <configured-repo>!<number>
Short issue: <configured-repo>#<number>
```

A bare Linear identifier is accepted only when its prefix matches the configured
tracker binding. The project is resolved by `references/CODEX-RUNTIME.md`; target
parsing must never read a separate default project.

## Validation

Before any lookup or mutation:

1. Read `providers.forge` and `providers.tracker`.
2. Validate the configured provider and authenticated identity.
3. Validate the URL host against the configured provider host.
4. Validate the owner or group and the repository/project against `repos[]`.
5. For tracker items, validate the configured workspace/team/project.
6. Resolve a short reference only inside the configured repository named by it.

Any mismatch returns a structured no-op. Never fall back to a different provider,
host, workspace, owner, group, project, or repository.

## Role behavior

- `implementer-run` implements the selected eligible issue.
- `reviewer-run` reviews the selected change.
- `validator-run` validates the selected change.
- `investigate-run` investigates the selected issue read-only.
- `unblock` resumes or triages the selected blocked issue.
- `releaser-run` still requires explicit release arming and every applicable
  approval gate.

Operate on exactly one validated target, then stop.
