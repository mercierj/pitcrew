# Audit d'efficacité tokens — Pitcrew

Généré le 2026-07-26.

## Résumé exécutif

- Le gaspillage principal arrive **avant** le choix du modèle : 64 appels mesurés ont consommé 66,96 M tokens avant de conclure par un no-op structuré. Cela représente **20,1 %** des 333,05 M tokens mesurés.
- L'échantillon couvre 195 appels mesurés entre le 2026-07-25 00:31 UTC et le 2026-07-25 22:11 UTC. L'équivalent API estimé par le catalogue du dépôt est d'environ **842 USD** ; ce n'est pas une facture d'abonnement.
- Le preflight actuel connaît seulement les cooldowns. Il ne vérifie pas l'éligibilité métier avant de lancer Codex ; le modèle fait donc lui-même les requêtes « y a-t-il du travail ? ».
- Les cadences les plus coûteuses sont du polling : implementer/reviewer/validator toutes les 15 minutes, puis research/investigate/unblock toutes les 30 minutes.
- Tous les rôles héritent actuellement de `model_reasoning_effort = "high"` depuis la configuration utilisateur, car le runner fixe le modèle mais pas l'effort. C'est disproportionné pour manager, stale-sweep, les no-ops et la majorité des validations mécaniques.
- Les modèles par défaut sont globalement cohérents par rôle, mais la sélection est trop statique : un no-op, une correction documentaire et une modification sensible utilisent le même modèle et le même effort au sein d'un rôle.
- Les trois plus gros workflows — implementer, validator et unblock — totalisent 18 467 mots d'instructions. Le test d'éligibilité arrive après le chargement de ces contrats monolithiques.
- `reviewer-run` peut lancer une équipe entière lorsque plusieurs changements sont présents. Cela contredit le contrat « un passage borné / un item » et multiplie les contextes au moment où la file est la plus chargée.
- La priorité recommandée est : **gate déterministe → backoff/event triggers → effort par rôle → routage modèle par risque → découpage progressif des skills**.

### Snapshot de mesure

Source locale : `~/.codex/pitcrew/getbill/history.jsonl`, projection limitée aux timestamps, rôles, modèles, statuts et compteurs d'usage ; aucun résumé n'a été copié dans ce document.

| Mesure | Valeur |
|---|---:|
| Enregistrements | 398 |
| Appels avec usage mesuré | 195 |
| Tokens totaux | 333 054 004 |
| Input non caché | 171 945 554 |
| Input caché | 160 288 768 |
| Output | 819 682 |
| Appels modèle terminés en no-op structuré | 64 |
| Tokens de ces no-ops | 66 963 020 |
| Équivalent API estimé | 842,23 USD |

La mesure sous-estime les appels inutiles : elle ne compte comme no-op que les réponses JSON dont `status == "noop"`. Plusieurs réponses libres comme « no investigation tickets queued » ou « no changes to review » ne sont pas toutes classées ainsi.

## Modèle mental de l'architecture

`launchd` déclenche dix rôles activés à des intervalles fixes. `bin/pitcrew-codex.sh` valide le projet, consulte un état global et un circuit breaker, résout un modèle statique, puis démarre une session Codex éphémère. Le skill choisi contient à la fois la détection d'éligibilité, les règles de sécurité, les opérations provider, l'exécution métier et le format de sortie. Le processus Python verrouillé collecte le dernier message et les compteurs d'usage dans l'historique.

Le tableau de bord agrège ensuite cet historique et permet de changer le modèle par rôle. L'architecture est sûre et bornée côté mutations, mais son ordonnancement reste fondé sur du polling. La décision la moins chère — « faut-il lancer un agent ? » — est donc prise à l'intérieur de l'agent le plus cher.

## Findings

| ID | Catégorie | Fichier:ligne | Sévérité | Effort | Constat | Recommandation |
|---|---|---|---|---|---|---|
| F001 | Orchestration | `scripts/pitcrew_preflight.py:86` | High | M | Le preflight ne teste que les cooldowns provider/rôle ; une absence de ticket, MR ou finding n'est pas détectée avant Codex. | Ajouter des probes read-only tri-state : `eligible`, `empty`, `unavailable`. Seul `empty` supprime l'appel ; `unavailable` conserve l'échec/retry actuel. |
| F002 | Orchestration | `bin/pitcrew-schedule.py:28` | High | M | Trois rôles activés pollent toutes les 15 minutes et trois toutes les 30 minutes, indépendamment d'un changement de file. | Déclencher les rôles downstream sur changement d'état et conserver un polling lent de secours. |
| F003 | Backoff | `scripts/pitcrew_preflight.py:14` | High | S | Le cooldown fixe de 30 minutes expire au même rythme que plusieurs jobs ; une file vide relance donc régulièrement un modèle sans nouvelle activité. | Backoff exponentiel par rôle (30 min, 1 h, 2 h, 4 h, plafond 8–24 h), remis à zéro sur changement provider ou travail trouvé. |
| F004 | Raisonnement | `bin/pitcrew-codex.sh:123`, `/Users/jo/.codex/config.toml:2` | High | S | Le runner transmet `--model` mais aucun `model_reasoning_effort`. Dans l'installation auditée, tous les jobs héritent du niveau utilisateur `high`. | Ajouter `reasoning_effort` au profil par rôle et passer `-c model_reasoning_effort="<niveau>"` explicitement. |
| F005 | Routage modèles | `scripts/pitcrew_config.py:147` | Medium | M | Le schéma n'accepte qu'un unique `model` par rôle ; il ne peut pas escalader selon la taille, le risque ou la phase. | Supporter une policy `default_model/default_effort` + règles d'escalade déterministes par labels, type de diff et catégorie risquée. |
| F006 | Délégation | `skills/reviewer-run/SKILL.md:180` | High | S | Le reviewer traite tous les changements survivants et peut créer une équipe par changement substantif, au lieu de sélectionner un seul item borné. | Sélectionner un seul MR par passage ; réserver la délégation à une invocation humaine explicite ou à un plafond séparé. |
| F007 | Contexte | `skills/implementer-run/SKILL.md:459` | High | M | L'éligibilité implementer n'arrive qu'après 458 lignes de contrat ; les skills implementer/validator/unblock pèsent ensemble 136 Ko. | Garder un `SKILL.md` court : bootstrap + gate + routage. Charger ensuite une référence ciblée uniquement après sélection d'une cible. |
| F008 | Contexte | `skills/reviewer-run/SKILL.md:146` | Medium | M | Le reviewer charge règles provider, état et triage détaillé avant de savoir si un MR nécessite une review. | Déplacer la liste/filtre des MR dans un helper déterministe et fournir au skill un seul MR déjà validé. |
| F009 | Sortie structurée | `bin/pitcrew-codex.sh:124`, `bin/pitcrew-codex.sh:169` | Medium | S | Le runner active JSONL mais n'impose pas d'output schema ; il redétecte ensuite les no-ops via quelques regex textuelles. | Ajouter `--output-schema` pour `status/reason/project/skill/target/next_action`, puis parser le JSON au lieu du texte. |
| F010 | Historique | `scripts/pitcrew_locked_exec.py:300` | Medium | S | Tout exit code 0 est enregistré comme `success`, même si le dernier message contient `status=noop`. Le dashboard doit reconstruire le statut a posteriori. | Normaliser le résumé avant l'append et enregistrer directement `outcome=noop/blocked/success`. |
| F011 | Observabilité | `bin/pitcrew-codex.sh:85` | Medium | S | Un no-op décidé par preflight sort avant `pitcrew_locked_exec`; il n'est donc pas ajouté au même historique que les lancements modèle. | Enregistrer les preflight no-ops avec `model_invoked=false` et `usage=0` pour mesurer le taux de gate et les économies. |
| F012 | Recherche | `bin/pitcrew-schedule.py:31`, `references/SCHEDULED-TASKS.md:51` | High | S | `research-run` scanne une cellule toutes les 30 minutes alors que la documentation recommande une cadence quotidienne ; le skill possède déjà des fingerprints Git. | Ne lancer que si le fingerprint de la prochaine cellule a changé, si la couverture est périmée, ou si la file manager est sous son seuil ; sinon quotidien. |
| F013 | Unblock | `skills/unblock/SKILL.md:178`, `scripts/pitcrew_models.py:54` | High | S | En mode unattended, un état `blocked` déjà persisté est relu par Sol uniquement pour réémettre la même question puis s'arrêter. | Faire retourner cet état directement par le runner/dashboard sans appel modèle ; relancer l'agent seulement après une réponse humaine ou un changement du ticket. |
| F014 | Isolation de contexte | `bin/pitcrew-codex.sh:117` | Medium | M | Les jobs héritent de la configuration utilisateur complète ; aucun profil Pitcrew minimal ne fixe effort, multi-agent ou outils nécessaires. | Ajouter un profil Codex Pitcrew explicite, garder le plugin disponible, désactiver multi-agent pour les rôles mono-item et limiter les outils non requis. |
| F015 | Budget | `scripts/pitcrew_locked_exec.py:285` | Medium | M | Le runner draine la session jusqu'à sa fin ; un signal de timeout interrompt le child sans checkpoint métier garanti. Un implementer mesuré consomme en moyenne 4,95 M tokens, médiane 1,74 M. | Commencer par des budgets consultatifs et des limites de scope. N'appliquer un arrêt dur qu'après ajout de checkpoints, reprise et réconciliation des actions distantes. |
| F016 | Attribution | `scripts/pitcrew_history.py:22` | Medium | M | L'historique connaît rôle, résumé, modèle et usage, mais pas une unité de travail normalisée ni un signal qualité. Impossible de calculer tokens par MR utile, finding accepté ou validation correcte. | Ajouter `model_invoked`, `target_id`, `work_kind`, `did_work`, `quality_outcome` et `gate_reason`. |

## Adéquation modèle/rôle

La bonne politique n'est pas un modèle unique moins cher. Elle doit conserver Sol sur les cas où une erreur coûte cher et éviter Sol pour la simple découverte d'éligibilité.

| Rôle | Actuel | Candidat à évaluer | Effort recommandé |
|---|---|---|---|
| security-run | Sol | Garder Sol | high pour analyse réelle ; aucun modèle si repo inchangé |
| implementer-run | Sol | Piloter Terra sur quick-win/docs/tests ; garder Sol sur logique métier, sécurité, argent, auth, migrations ou gros diff | medium par défaut, high à l'escalade |
| reviewer-run | Sol | Piloter Terra sur petit/moyen diff ; garder Sol sur catégories risquées ou large blast radius | medium, high à l'escalade |
| investigate-run | Sol | Piloter Terra hors security/auth/tenant/money/crypto ; garder Sol sur risque élevé et non-convergence | medium, high à l'escalade |
| unblock | Sol | Piloter Terra pour synthèse/décision ; garder Sol pour arbitrage sensible ; aucun modèle tant que la question attend une réponse | low/medium |
| validator-run | Terra | Garder Terra ; Luna possible pour docs/config purement déterministes | medium, low sur chemin mécanique |
| research-run | Terra | Garder Terra, mais conditionner au changement Git et réduire la cadence | medium |
| product-discovery-run | Terra | Garder Terra | medium |
| manager-run | Luna | Garder Luna | low |
| stale-sweep | Luna | Garder Luna | low |
| ops-run | Luna | Garder Luna | low |
| qa/dev-verify/coverage | Terra | Cohérent tant qu'ils restent désactivés sans configuration | medium |
| releaser-run | Terra | Terra pour préparation ; Sol seulement pour décision complexe, jamais pour contourner les gates humaines | medium/high |

## Top 5 — si rien d'autre n'est fait

1. **F001 — Gate d'éligibilité hors modèle**
   - Créer un probe read-only par rôle : `decision=eligible|empty|unavailable`, `reason`, `target_id`, `fingerprint`.
   - Faire passer le runner directement à `noop` uniquement pour `empty`.
   - Conserver le chemin d'échec/retry actuel pour `unavailable`, afin de ne jamais masquer un problème d'auth ou réseau.
   - Gain plancher observé : jusqu'à 66,96 M tokens sur 333,05 M, soit 20,1 %.

2. **F004 — Fixer l'effort explicitement**
   - `low` : manager, stale-sweep, ops, no-op/triage.
   - `medium` : research, validator, product, unblock ordinaire.
   - `high` : security et escalades risquées.
   - Mesurer pendant sept jours les tokens par unité de travail et les retours reviewer/validator avant d'élargir.

3. **F012/F013 — Supprimer les deux boucles manifestement prématurées**
   - Research : fingerprint + cadence quotidienne de secours.
   - Unblock : ne pas relancer tant que `unblock-state.json` attend une décision.
   - Ces changements ne réduisent pas la profondeur d'analyse lorsqu'un travail existe.

4. **F005 — Router par risque, pas uniquement par rôle**
   - Classifier avec les labels déjà présents et `risky_categories_regex`.
   - Tester Terra sur un sous-ensemble normal ; Sol reste la référence et l'escalade explicite.
   - Exiger avant bascule : taux de findings reviewer acceptés, taux de validation, défauts échappés et seuil de rollback non dégradés.
   - Conserver un override manuel dans le dashboard et journaliser la raison du routage.

5. **F007 — Progressive disclosure des skills**
   - Phase A courte : bootstrap, sécurité minimale, probe.
   - Phase B ciblée : charger seulement `new-ticket`, `review-gate`, `validation-web`, `validation-docs`, etc.
   - Le contrat complet reste disponible, mais n'est plus répété dans chaque tour d'un no-op.

## Quick wins

- [ ] F004 : ajouter `reasoning_effort` au catalogue et au runner.
- [ ] F006 : limiter reviewer-run à un seul MR et retirer le chemin `TeamCreate` des passages planifiés.
- [ ] F009 : imposer un output schema et supprimer les regex de statut.
- [ ] F011 : enregistrer les preflight no-ops avec `model_invoked=false`.
- [ ] F013 : court-circuiter l'état unblock déjà `blocked`.

## Ce qui paraît mauvais mais est acceptable

- Le volume d'output n'est pas le problème principal : 819 682 tokens, soit environ 0,25 % du total mesuré. Réduire les résumés seuls aurait peu d'effet.
- `--ephemeral` est adapté aux passages planifiés : les handoffs sont déjà dans GitLab et les ledgers, pas dans la mémoire de conversation.
- Les modèles Sol pour security-run et les escalades sensibles sont justifiés ; leur coût doit être réduit par gating, pas par un downgrade aveugle.
- La séparation reviewer/validator protège deux qualités différentes — conformité du code et comportement. Les fusionner économiserait des appels mais diminuerait la qualité.
- Le cache fonctionne : l'input caché représente 160,29 M tokens. Il réduit le coût équivalent, mais pas le volume de tokens comptabilisé ni les appels inutiles.

## Questions ouvertes

- Quel taux de findings research est effectivement accepté ou transformé en ticket ? Sans ce signal, sa cadence optimale reste une hypothèse.
- Les 31 passages implementer mesurés ont-ils produit 31 unités utiles, ou certains ont-ils seulement avancé une phase d'un même ticket ?
- Faut-il optimiser la limite d'abonnement, l'équivalent API, la latence, ou les trois ? Le choix entre modèle, effort et cadence n'a pas exactement le même optimum.
- Le profil Pitcrew peut-il désactiver les tools/apps non utilisés sans empêcher la découverte du plugin ?
- Quel niveau de faux négatif est acceptable pour un probe d'éligibilité provider ? La recommandation suppose un fallback de polling lent pour ne pas perdre de travail.
