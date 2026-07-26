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

`bugfixer-run` supports only these matched native pairs in its first version:

| Forge | Tracker | Reference |
| --- | --- | --- |
| `github` | `github` | `providers/github.md` |
| `github` | `linear` | `providers/github-linear.md` |
| `gitlab` | `gitlab` | `providers/gitlab.md` |

The `forge=github, tracker=github` pair uses [Native GitHub Issues](providers/github.md).
Any other pair returns a structured no-op; do not combine provider capabilities.
