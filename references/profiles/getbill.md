# GetBill Codex profile

Use this profile only from the GetBill repository root `/Users/jo/Prog/getbill`.
Before work, read the repository's applicable `AGENTS.md` instructions: this is the
authoritative live policy for local work, Git safety, and all remote-environment
boundaries.

## Required project context

Read the relevant `.claude` reference before changing its area: security,
accessibility, performance, SEO, schema, infrastructure, modal system, background
jobs, or troubleshooting. For architecture questions, read the Graphify report (and
its wiki index when present) before broad source exploration; refresh Graphify after
code-file changes with the project command. Prefer RTK for high-output inspection,
tests, logs, and package commands.

## Providers and safety gates

GetBill uses the GitLab provider for issues and merge requests. Preserve existing
changes and stage only files created or modified by the current run. Never use
destructive git operations such as reset, restore, clean, stash, or revert unless
the user explicitly approves them.

Ask for approval before **every** action against `prod` or `preprod`; do not chain
remote actions. This includes SSH, remote database access, console commands,
deployment, CloudWatch, and SSM. Explicit approval is also required for
migrations, fixtures, schema mutations, raw SQL writes, and mutating `app:*` commands. GetBill
has release autonomy off; release scheduling is disabled.

Never read or display the secret denylist: `.env.local`, `.env.*.local`, AWS credentials/configuration,
SSH material, `*.pem`, `*.key`, or files named for credentials, secrets, or passwords.
Use existing tooling without exposing values.
