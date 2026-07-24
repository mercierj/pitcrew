# Provider selection

Provider choice is explicit project configuration. Use this shape:

```json
{
  "providers": {
    "forge": "github",
    "tracker": "linear"
  }
}
```

This is the `config.json` shape used by the profile loader.

`providers.forge` supports `github` and `gitlab`. `providers.tracker` supports
`linear`, `github`, `gitlab`, and `none` when the selected provider supports the
configured work-item operation. Validate both values before any provider call.

Never fall back to another provider, workspace, owner, or repository when
authentication, configuration, or an API operation fails. Return a blocked or no-op
result naming the configured provider, then let the operator correct the
configuration or authorization.

Use [GitHub + Linear](providers/github-linear.md) when forge and tracker differ;
use [GitLab](providers/gitlab.md) when GitLab owns the merge-request workflow.
