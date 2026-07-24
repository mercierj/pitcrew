# GitLab provider contract

Use GitLab only when it is selected in project configuration. Verify the local
identity and configured GitLab host before mutations:

```sh
glab auth status
```

If access to the configured group/project is missing, return a blocked result; do
not redirect the work to another forge or tracker.

## Merge requests and idempotency

For each requested branch change, locate an existing open merge request with the
same source branch and project before creating one. Update that merge request when
the requested operation is already represented. For comments, labels, and issues,
use an operation marker and search first. Following an uncertain result, query
GitLab before retrying so the run cannot create duplicates.
