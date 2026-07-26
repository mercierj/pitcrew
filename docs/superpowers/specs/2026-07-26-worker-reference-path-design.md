# Worker reference path design

## Problem

Scheduled Codex workers run with the configured project checkout as their working
directory. Skills currently describe Pitcrew runtime files with relative paths such
as `references/CODEX-RUNTIME.md`, so a worker can fail closed when those files live
in the Pitcrew repository instead of the project checkout.

## Design

The launcher will include the absolute Pitcrew repository path in every worker
prompt and explicitly map skill-relative `references/...` paths to
`$REPO_ROOT/references/...`. The worker keeps its existing project checkout,
runtime-directory grants, sandbox mode, and provider behavior. No reference files
are copied into the customer repository.

## Failure handling

The prompt will require the worker to fail closed if the absolute reference path is
missing or unreadable. Existing skill contracts remain authoritative; this change
only removes ambiguity about where their shared references are located.

## Verification

Add a launcher test using the existing fake Codex binary and assert that the prompt
contains the Pitcrew reference root and the explicit mapping. Run the focused CLI
test plus the repository's shell/Python contract tests relevant to the launcher.
