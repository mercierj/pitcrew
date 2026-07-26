# Admission automatique des tickets du manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Démarrer automatiquement `implementer-run` pour chaque ticket non risqué créé par `manager-run` sur la route agent.

**Architecture:** Après la création confirmée d’un ticket `agent/todo`, le manager appelle la CLI existante du `RunDispatcher` avec l’URL canonique. La file SQLite applique déjà capacité, validation et déduplication. Les tickets `investigate` restent exclus.

**Tech Stack:** Instructions de skill Markdown, Python 3.13 `unittest`, `scripts/pitcrew_run_dispatcher.py`, SQLite locale existante.

---

## File map

- Modify `skills/manager-run/SKILL.md`: imposer l’admission locale après chaque création confirmée d’un ticket agent.
- Modify `tests/test_skill_contracts.py`: verrouiller le contrat manager → implémenteur et l’exclusion investigate.
- Modify `references/TOPOLOGY.md`: documenter le handoff local immédiat.
- Modify `README.md`: expliquer l’absence de polling GitLab pour ces tickets.

### Task 1: Écrire le test de contrat en échec

**Files:**

- Modify: `tests/test_skill_contracts.py`
- Test: `tests/test_skill_contracts.py`

- [ ] **Step 1: Add the failing test**

Ajouter à `ReferenceContractTest` :

```python
def test_manager_admits_each_new_agent_ticket_to_the_local_dispatcher(self):
    manager = (ROOT / "skills/manager-run/SKILL.md").read_text(encoding="utf-8")
    self.assertIn("ADMIT_NEW_AGENT_TICKET", manager)
    self.assertIn("python3 $REPO_ROOT/scripts/pitcrew_run_dispatcher.py enqueue", manager)
    self.assertIn("--skill implementer-run", manager)
    self.assertIn("--target \"<canonical ticket URL>\"", manager)
    self.assertIn("only for a newly created agent-route ticket", manager)
    self.assertIn("Never admit an investigate-route ticket", manager)
```

- [ ] **Step 2: Verify RED**

Run:

```bash
python3 -m unittest tests.test_skill_contracts.ReferenceContractTest.test_manager_admits_each_new_agent_ticket_to_the_local_dispatcher
```

Expected: FAIL because `manager-run` does not define `ADMIT_NEW_AGENT_TICKET`.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/test_skill_contracts.py
git commit -m "test: require manager ticket admission contract"
```

### Task 2: Admettre le ticket après sa création confirmée

**Files:**

- Modify: `skills/manager-run/SKILL.md:STEP 4`
- Test: `tests/test_skill_contracts.py`

- [ ] **Step 1: Add the minimal admission procedure**

Insérer dans STEP 4, après la création et relecture GitLab du ticket agent et
avant `state.filed[key]` :

```text
7. **ADMIT_NEW_AGENT_TICKET (agent route only):** For every newly created
   agent-route ticket, re-read its configured-provider document and obtain its
   canonical HTTPS ticket URL. Run exactly:

   python3 $REPO_ROOT/scripts/pitcrew_run_dispatcher.py enqueue \
     --project "$PROJECT" \
     --skill implementer-run \
     --target "<canonical ticket URL>"

   The dispatcher output must be valid JSON with a non-empty `run_id` and a
   `state` of `queued` or `running`. This operation is idempotent for the same
   canonical URL; do not retry after an ambiguous response. If it fails or the
   response is malformed, do not write `state.filed[key]` or the manager
   history: stop with a structured failed result so the unchanged finding is
   retryable. Perform this only for a newly created agent-route ticket; a
   deduplicated existing ticket is not a creation event. Never admit an
   investigate-route ticket.
```

Conserver l’ordre pour les propositions architecture : l’attachement tracker
réussit, puis `ADMIT_NEW_AGENT_TICKET`, puis l’écriture d’état manager.

- [ ] **Step 2: Verify GREEN**

Run:

```bash
python3 -m unittest tests.test_skill_contracts.ReferenceContractTest.test_manager_admits_each_new_agent_ticket_to_the_local_dispatcher
```

Expected: PASS.

- [ ] **Step 3: Verify the existing dispatcher behavior**

Run:

```bash
python3 -m unittest tests.test_run_dispatcher -v
```

Expected: PASS; targeted enqueue keeps idempotency, target validation,
capacity, and automatic draining.

- [ ] **Step 4: Commit the implementation**

```bash
git add skills/manager-run/SKILL.md tests/test_skill_contracts.py
git commit -m "feat: admit manager tickets to implementer queue"
```

### Task 3: Documenter le handoff

**Files:**

- Modify: `references/TOPOLOGY.md:Handoffs`
- Modify: `README.md`
- Test: `tests/test_docs.py`

- [ ] **Step 1: Add failing documentation assertions**

Ajouter un test ciblé dans `tests/test_docs.py` qui lit les deux documents et
vérifie les chaînes `manager-run -> implementer-run` et `without polling GitLab`.

- [ ] **Step 2: Verify RED**

Run:

```bash
python3 -m unittest tests.test_docs -v
```

Expected: FAIL because the handoff is undocumented.

- [ ] **Step 3: Document the exact flow**

Ajouter dans `references/TOPOLOGY.md` :

```text
manager-run creates agent/todo -> local enqueue -> implementer-run
```

Ajouter dans `README.md` une phrase : un ticket nouvellement créé par
`manager-run` est admis immédiatement dans la file locale `implementer-run`,
without polling GitLab; les tickets créés hors de ce flux ne sont pas couverts.

- [ ] **Step 4: Verify GREEN and commit**

Run:

```bash
python3 -m unittest tests.test_docs -v
git add references/TOPOLOGY.md README.md tests/test_docs.py
git commit -m "docs: describe manager ticket handoff"
```

Expected: test PASS and one documentation commit.

### Task 4: Vérifier l’intégration complète

**Files:**

- Verify: `skills/manager-run/SKILL.md`
- Verify: `tests/test_skill_contracts.py`
- Verify: `tests/test_run_dispatcher.py`
- Verify: `references/TOPOLOGY.md`
- Verify: `README.md`

- [ ] **Step 1: Run all targeted checks**

Run:

```bash
python3 -m unittest tests.test_skill_contracts tests.test_run_dispatcher tests.test_docs -v
```

Expected: PASS with zero failures.

- [ ] **Step 2: Inspect the final diff**

Run:

```bash
git diff --check HEAD~3..HEAD
git status --short
```

Expected: no whitespace errors; only intentional files are changed.
