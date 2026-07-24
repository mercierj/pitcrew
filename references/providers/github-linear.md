# GitHub + Linear provider contract

Use this provider pair only when configuration declares GitHub as the forge and
Linear as the tracker. Verify the local GitHub identity before mutations:

```sh
gh auth status
```

Stop and report a blocked result if the authenticated GitHub account, host, or
repository access does not match the configured project. Linear access requires an
explicit configured team/workspace and a valid authenticated integration; do not
infer a team from issue text or fall back to GitHub Issues.

## Idempotent operations

Before creating a branch, pull request, comment, label, or Linear issue, search for
the configured external identifier and exact operation marker. Reuse the existing
resource if it represents the same requested operation. After uncertain network
results, re-query before retrying and never create a duplicate.
