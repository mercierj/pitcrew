# Contributing to Pitcrew

Pitcrew is a Codex-native plugin with thirteen config-driven skills. Contributions
should remain provider-neutral, bounded, and safe by default.

## Contracts

- `.codex-plugin/plugin.json` defines native discovery.
- `skills/<name>/SKILL.md` defines one crew role.
- `references/CODEX-RUNTIME.md` defines runtime and no-op behavior.
- `references/PROVIDERS.md` and `references/providers/` own provider syntax.
- `references/TOPOLOGY.md` owns cross-skill handoffs.
- `profiles/getbill.json` and `references/profiles/getbill.md` own GetBill defaults.

Acting skills use generic operations; do not embed `gh pr`, `glab mr`, connector
prefixes, or an implicit fallback provider in them. Every run must validate exactly
one project and perform one bounded pass.

## Changes

1. Read the repository instructions and relevant contracts.
2. Add or update a meaningful contract test for behavior or safety changes.
3. Make the smallest coherent change.
4. Validate both generic and GetBill profiles.
5. Run the full suite:

   ```bash
   bash tests/run.sh
   ```

6. Keep commits focused and document verification in the change description.

When adding a skill, give its frontmatter a `description: Use when ...`, add it to
the Codex plugin manifest and plugin contract test, namespace cross-skill
invocations as `$pitcrew:<skill>`, and update the topology.

## Safety

- Never add real credentials, webhooks, team IDs, or private repository details to
  fixtures.
- Preserve sandbox and approval policy; do not recommend unrestricted execution as
  the default.
- New release autonomy is opt-in and must fail closed.
- Preserve GetBill's per-action prod/preprod approvals, database-write approval,
  secret-file prohibitions, and stage-only-owned-files rule.
- Keep Claude support isolated as secondary compatibility; do not reintroduce
  legacy prompt installation into the Codex path.

By contributing, you agree that your work is licensed under the repository's
[MIT License](LICENSE).
