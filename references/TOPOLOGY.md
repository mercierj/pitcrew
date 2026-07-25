# Crew topology

Pitcrew consists of thirteen Codex skills. Each invocation is self-contained and
performs one bounded pass for one validated project.

| Skill | Reads | Produces |
|---|---|---|
| `$pitcrew:research-run` | repository and architecture references | one local finding |
| `$pitcrew:security-run` | security reference and repository | one security proposal |
| `$pitcrew:product-discovery-run` | product references and repository | one feature proposal |
| `$pitcrew:qa-run` | configured test flows | one QA result/finding |
| `$pitcrew:manager-run` | curated findings | paced tracker work |
| `$pitcrew:implementer-run` | one eligible tracker item | one code change |
| `$pitcrew:reviewer-run` | one open change (PR/MR) | review verdict |
| `$pitcrew:validator-run` | one open change (PR/MR) | local validation verdict |
| `$pitcrew:unblock` | one blocked item | one structured human decision |
| `$pitcrew:investigate-run` | one blocker | read-only findings |
| `$pitcrew:coverage-run` | capability/surface map | one grounded test-flow change |
| `$pitcrew:dev-verify-run` | one landed dev change | dev verification result |
| `$pitcrew:ops-run` | configured health surface | observation or incident item |
| `$pitcrew:releaser-run` | one eligible merged change | gated release action |
| `$pitcrew:stale-sweep` | lifecycle state | one reconciliation |

## Lifecycle

```text
todo -> processing -> review -> done
           |            |
           +-> blocked <-+
```

Provider-specific state names and labels are configuration. Eligibility must be
explicit; a skill never invents work or switches projects when the queue is empty.

## Handoffs

```text
research/qa/security -> findings -> manager -> tracker
product-discovery -> dashboard approval -> manager -> tracker
                                      |
                                      v
investigate <-> unblock <- blocked <- implementer -> change (PR/MR)
                                                     |          |
                                                     v          v
                                                  reviewer   validator
                                                     \          /
                                                      -> merge
                                                           |
                                                dev-verify/releaser
                                                           |
                                                     stale-sweep

ops observes configured environments and files lifecycle work through the tracker.
coverage proposes test-flow changes independently of a product implementation.
```

The board and local state are the only handoff channels. Skills do not rely on
conversation memory from a previous run.

## Generic provider operations

Acting skills reason in capabilities:

- list eligible work;
- claim work;
- create change;
- review change;
- merge change;
- close lifecycle.

The selected provider reference maps those capabilities to GitHub pull requests,
GitLab merge requests, Linear issues, or forge-hosted issues. Provider-specific CLI
syntax belongs only in those references. Every binding validates provider, host,
workspace, owner/group, and repository and fails closed when the mapping is
unavailable.

## Gates

- `AGENTS.md` is read before work selection.
- Research and investigation remain read-only.
- Review and validation are independent gates.
- Ambiguous work routes to `$pitcrew:unblock`.
- Release autonomy must be explicitly enabled by project policy.
- GetBill leaves release autonomy off and requires a fresh approval before every
  prod or preprod action.
- A run with no eligible work returns the structured no-op from
  [CODEX-RUNTIME.md](CODEX-RUNTIME.md).

Directed invocations still preserve every gate; see
[DIRECTED-TARGET.md](DIRECTED-TARGET.md).
