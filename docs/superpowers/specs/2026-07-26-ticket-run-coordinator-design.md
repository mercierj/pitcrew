# Coordinateur durable des exécutions par ticket

## Contexte

Le dashboard sait lancer un agent ciblé depuis un ticket GitLab, mais le
mécanisme d'exécution reste verrouillé par rôle. Deux tickets `todo` lancés
presque simultanément peuvent donc recevoir une réponse HTTP positive alors
qu'un seul passage `implementer-run` acquiert réellement le verrou
`<skill>.lock`; l'autre termine en `noop`.

L'état d'un lancement ciblé n'est pas non plus persistant par ticket. Le
navigateur désactive le bouton pendant la requête HTTP, puis le réactive dès
que les données GitLab en cache indiquent encore que l'agent est disponible.
Un rafraîchissement de page, un second onglet ou deux requêtes concurrentes
peuvent ainsi relancer le même ticket.

Le mécanisme doit permettre plusieurs tickets simultanés, conserver une limite
de ressources explicite, mettre les demandes excédentaires en file, et fournir
au dashboard une source de vérité durable.

## Objectifs

- Autoriser plusieurs exécutions concurrentes d'un même rôle, avec une capacité
  configurable et une valeur par défaut de trois.
- Garantir qu'un ticket donné ne possède jamais plus d'une exécution active.
- Mettre les demandes excédentaires en file FIFO et les démarrer
  automatiquement lorsqu'une place se libère.
- Coordonner les lancements du dashboard et les passages planifiés avec le même
  mécanisme d'admission.
- Conserver l'état au travers des rafraîchissements, des onglets concurrents et
  des redémarrages du dashboard ou de la machine.
- Afficher des états fidèles par ticket et interdire un nouveau clic tant que
  le ticket est en attente ou en cours.
- Libérer un ticket après un échec et laisser l'opérateur décider d'une relance
  manuelle.

## Hors périmètre

- Distribuer la file entre plusieurs machines.
- Ajouter Redis, un broker ou une base externe.
- Réessayer automatiquement une exécution en échec.
- Modifier les règles métier propres à chaque skill pour choisir un ticket.
- Déployer, fusionner une MR ou fermer un ticket directement depuis le CTA de
  lancement.

## Architecture

Un coordinateur SQLite local devient l'unique porte d'entrée des exécutions
planifiées ou demandées depuis le dashboard. La base vit dans le runtime du
projet, à l'emplacement `<runtime>/runs.sqlite3`, utilise WAL et applique les
transitions d'état dans des transactions courtes.

Une demande ciblée est enregistrée avec sa cible canonique dès son admission.
Une demande planifiée sans cible commence avec une clé de déduplication propre
au rôle. Lorsqu'un skill planifié choisit un ticket, il l'attache à son
`run_id` par une opération atomique. Si la cible est déjà réservée, le claim
échoue sans mutation distante et le skill doit choisir un autre ticket ou
terminer en `noop`.

Le coordinateur démarre les entrées `queued` les plus anciennes tant que la
capacité du rôle le permet. Chaque worker reçoit un `run_id` opaque et met à
jour son heartbeat. À la fin du worker, le coordinateur enregistre l'état
terminal, libère la capacité et draine immédiatement la prochaine entrée de la
file.

Le dashboard et chaque passage planifié déclenchent également une
réconciliation légère. Elle détecte les workers interrompus, actualise les
états périmés et relance le drainage de la file. Le système ne dépend donc pas
d'un daemon supplémentaire pour progresser.

## Composants

### Registre et coordinateur

Un module Python dédié possède le schéma, les transactions et les transitions
de run. Il expose des opérations explicites:

- `enqueue_targeted(project, skill, target, source)`;
- `enqueue_scheduled(project, skill)`;
- `bind_target(run_id, target)`;
- `claim_ready_runs(project)`;
- `mark_started(run_id, pid)`;
- `heartbeat(run_id, phase)`;
- `finish(run_id, outcome, error)`;
- `reconcile(project)`;
- `list_runs(project, active_only)`.

Le dashboard appelle ces opérations comme une bibliothèque. Le runner et les
skills les utilisent par une CLI étroite afin que tous les processus partagent
les mêmes transactions.

### Runner

`pitcrew-codex.sh` soumet les passages planifiés au coordinateur au lieu
d'acquérir directement un verrou de rôle. Le coordinateur relance ensuite le
runner avec un drapeau interne contenant le `run_id`; ce chemin interne exécute
Codex sans ré-enfiler la demande.

Un worker coordonné utilise un verrou secondaire propre au `run_id`. Il publie
son état live sous `live/runs/<run_id>.json`. Les vues par rôle agrègent ces
fichiers et les lignes SQLite, ce qui remplace le fichier live unique
`live/<skill>.json`.

### Dashboard

Le service enrichit chaque carte GitLab avec l'exécution active correspondant
à sa cible canonique. La disponibilité d'un CTA dépend du ticket et de l'état
du coordinateur, pas du booléen `running` historique du rôle.

Le snapshot des agents expose également:

- `running_count`;
- `queued_count`;
- `max_concurrent`;
- les phases des exécutions actives.

## Modèle de données

La table `runs` contient au minimum:

| Colonne | Rôle |
| --- | --- |
| `run_id` | UUID généré par le coordinateur, clé primaire. |
| `project` | Projet Pitcrew propriétaire. |
| `skill` | Rôle exécuté. |
| `source` | `dashboard` ou `scheduled`. |
| `target` | URL GitLab canonique, nullable avant un claim planifié. |
| `dedupe_key` | Identité active: ticket canonique ou passage planifié du rôle. |
| `state` | `queued`, `running`, `succeeded`, `failed` ou `cancelled`. |
| `queue_sequence` | Ordre FIFO monotone. |
| `pid` | PID du worker une fois démarré. |
| `created_at` | Date d'admission UTC. |
| `started_at` | Date de démarrage UTC. |
| `heartbeat_at` | Dernier signe de vie UTC. |
| `finished_at` | Date terminale UTC. |
| `phase` | Phase publique courte pour le dashboard. |
| `error_code` | Code stable et non sensible. |
| `error_message` | Message public expurgé. |
| `predecessor_run_id` | Run précédent lors d'une relance manuelle. |

Un index unique partiel sur `(project, dedupe_key)` pour les états `queued` et
`running` rend l'admission idempotente. Deux requêtes simultanées pour la même
cible retournent donc le même run actif. Un index sur
`(project, skill, state, queue_sequence)` rend le calcul de capacité et le
drainage FIFO déterministes.

Le schéma est créé automatiquement et versionné avec `PRAGMA user_version`.
Les migrations futures s'exécutent sous transaction avant toute admission.

## Configuration de capacité

La configuration accepte une section optionnelle:

```json
{
  "execution": {
    "default_max_concurrent_per_skill": 3,
    "max_concurrent_per_skill": {
      "implementer-run": 3,
      "unblock": 2
    }
  }
}
```

L'absence de section conserve la compatibilité et applique la valeur trois.
Les capacités doivent être des entiers compris entre 1 et 16. Une diminution
sous le nombre de workers déjà actifs ne les interrompt pas; aucun nouveau run
du rôle ne démarre avant que le compteur repasse sous la limite.

## États et transitions

Le cycle normal est:

```text
queued -> running -> succeeded
                  \-> failed
                  \-> cancelled
queued -----------> cancelled
```

`failed` couvre une erreur du runner et un worker interrompu. `cancelled`
couvre un arrêt explicite, l'arrêt global, ou une cible devenue inéligible
avant son démarrage. Les états terminaux libèrent immédiatement la cible et la
capacité. Une relance manuelle crée un nouveau `run_id` et référence le run
précédent.

Un run `running` dont le PID n'existe plus ou dont le heartbeat dépasse le
délai de sécurité passe à `failed` avec le code `interrupted`. Il n'est jamais
réessayé automatiquement.

## Admission et validation d'un ticket

Le dashboard canonicalise puis valide la cible avant l'admission:

- URL HTTPS;
- hôte GitLab configuré;
- projet configuré;
- IID positif;
- ticket encore ouvert;
- lifecycle compatible avec le skill demandé;
- absence d'une exécution active sur cette cible.

La lifecycle est relue juste avant le passage de `queued` à `running`. Si elle
a changé ou si le ticket est fermé, le run passe à `cancelled` avec le code
`stale_target` et aucun processus Codex n'est créé.

Pour un run planifié sans cible, le skill conserve sa sélection métier
existante, mais doit appeler `bind-target` avant toute mutation GitLab ou
écriture dans le checkout. Cette opération vérifie la forme canonique de la
cible et applique la même unicité active.

## File et ordonnancement

La capacité est comptée par `(project, skill)`. Deux rôles différents
progressent indépendamment. À l'intérieur d'un rôle, les runs démarrent dans
l'ordre `queue_sequence`.

Un passage planifié non ciblé utilise initialement la clé
`scheduled:<skill>`. Cette clé empêche les déclenchements périodiques de remplir
la file avec des doublons identiques. Lorsqu'il attache un ticket, sa clé
devient `ticket:<target canonique>`; un passage planifié futur peut alors
chercher un autre travail.

La fin d'un worker déclenche le drainage dans un bloc `finally`. Si le worker
est tué avant ce bloc, le prochain poll du dashboard, démarrage du serveur ou
passage planifié exécute `reconcile` puis `claim_ready_runs`.

## API locale

### Création

`POST /api/ticket-runs` accepte exactement:

```json
{
  "skill": "implementer-run",
  "target": "https://gitlab.example/group/project/-/issues/42"
}
```

La réponse `202` contient:

```json
{
  "run_id": "opaque-uuid",
  "state": "queued",
  "queue_position": 1,
  "created": true
}
```

Une demande répétée retourne le run actif avec `created: false`. Le contrat
reste ainsi idempotent même entre plusieurs onglets. Une cible invalide ou un
mapping lifecycle/skill incohérent est refusé sans créer de ligne.

### Consultation

`GET /api/runs` retourne les runs actifs, les derniers échecs conservés, et la
capacité agrégée par rôle. Les réponses ne contiennent ni commande, ni prompt,
ni sortie brute de Codex.

Le endpoint GitLab inclut pour chaque ticket un résumé `active_run` lorsqu'une
ligne active correspond à sa cible. Le navigateur peut donc reconstruire son
interface après un rechargement sans état local durable.

## Interface utilisateur

Le clic désactive immédiatement le bouton. La réponse serveur devient ensuite
la source de vérité:

- `queued`: `En attente · position N`;
- `running`: `En cours · X/3 places utilisées`;
- `failed`: `Échec · Relancer`;
- `cancelled`: motif public et relance possible si la lifecycle reste éligible.

Le bouton reste désactivé pour `queued` et `running`, y compris après un
rafraîchissement ou dans un second onglet. Deux tickets distincts restent
cliquables tant qu'ils ne possèdent pas leur propre run actif.

Le polling local utilise un intervalle de deux secondes lorsqu'au moins un run
est actif, puis revient à dix secondes. Il ne force pas GitLab à chaque fois:
les états SQLite sont superposés aux cartes en cache. Chaque transition
terminale invalide le cache GitLab et provoque une actualisation forcée unique.
Le timestamp du cache n'est avancé qu'après une requête GitLab réussie.

Les cartes d'agents affichent une capacité agrégée plutôt qu'un booléen, par
exemple `2 en cours · 1 en attente · capacité 3`.

## GitLab et effets de bord

Les endpoints de lecture du dashboard ne mutent plus GitLab. La
réconciliation d'une MR fusionnée avec son ticket est soumise comme un travail
coordonné et possède la même réservation par ticket. Les mises à jour de
labels relisent l'état distant avant écriture et n'écrasent pas une lifecycle
plus récente.

Une exécution ciblée n'est pas considérée comme réussie parce que le processus
a seulement démarré. Le dashboard affiche `running` après le claim du worker,
puis attend l'état terminal et les données GitLab actualisées.

## Arrêt global et récupération

`Tout arrêter` bloque toute nouvelle admission et tout nouveau claim. Les runs
encore `queued` restent dans la file mais sont affichés comme suspendus. Les
workers actifs reçoivent le signal d'arrêt, puis passent à `cancelled` avec le
code `global_stop`.

`Reprendre` réactive l'admission et draine automatiquement les runs restés en
file. Les runs annulés ne sont pas relancés.

Après un redémarrage de la machine:

1. les runs `running` sans worker vivant deviennent `failed: interrupted`;
2. les anciennes entrées `live/<skill>.json` encore valides comptent
   temporairement dans la capacité;
3. les runs `queued` reprennent dans leur ordre initial;
4. aucun run terminal n'est réessayé.

## Migration et compatibilité

La base est créée au premier usage. Aucune commande de migration manuelle
n'est nécessaire et les configurations existantes restent valides.

Pendant la transition, un passage lancé avec l'ancien fichier live par rôle
occupe une place dans la capacité. Les nouveaux runs utilisent
`live/runs/<run_id>.json`. Le verrou historique `<skill>.lock` n'est plus
l'autorité pour les runs coordonnés; un verrou propre au `run_id` empêche
seulement le double démarrage du même worker.

Les runs terminaux sont purgés après sept jours, en cohérence avec l'historique
du dashboard. La purge ne supprime jamais une ligne `queued` ou `running`.

Les modifications locales déjà présentes dans le dashboard, notamment les
messages d'état par ticket et l'ordre de rafraîchissement, sont intégrées sans
être écrasées.

## Sécurité et erreurs

- Toutes les mutations SQLite sont transactionnelles et paramétrées.
- Les chemins runtime existants conservent leurs permissions privées.
- Les cibles, skills et corps HTTP sont validés par listes fermées et contrats
  exacts.
- Les messages publics utilisent des codes stables et des textes expurgés.
- Les PID ne servent jamais d'identité métier; seul `run_id` identifie une
  exécution.
- Une erreur d'admission ne démarre aucun processus.

## Vérification

### Registre

- Deux admissions concurrentes de la même cible retournent un seul `run_id`.
- Trois cibles du même rôle passent `running`; la quatrième reste `queued`.
- La quatrième démarre automatiquement après la première fin.
- Les rôles différents utilisent des capacités indépendantes.
- L'ordre FIFO est stable sous concurrence.
- Un bind planifié vers une cible déjà active est refusé atomiquement.

### Runner et récupération

- Un worker publie PID, phase et heartbeat par `run_id`.
- Une sortie réussie ou échouée produit le bon état terminal et draine la file.
- Un crash est réconcilié en `failed: interrupted`.
- L'arrêt global annule les workers actifs, suspend la file et la reprise
  redémarre seulement les entrées restées `queued`.

### Service et HTTP

- Le mapping skill/lifecycle est relu avant admission et avant démarrage.
- Le même POST répété est idempotent.
- Les cibles étrangères, fermées ou devenues périmées sont refusées ou
  annulées sans lancer Codex.
- Les endpoints exigent la session locale et rejettent les champs inattendus.

### Dashboard

- Un double clic n'envoie qu'une admission utile.
- Un second onglet et un rafraîchissement conservent le CTA désactivé.
- Plusieurs cartes distinctes progressent en parallèle.
- La position de file, la capacité et les erreurs sont actualisées.
- Un échec terminal réactive seulement le ticket concerné.
- Le parcours est validé dans un navigateur réel.

### Régression

- Les tests déterministes existants restent verts.
- Les anciens marqueurs live et les configurations sans section `execution`
  restent compatibles.
- Les mutations GitLab ne sont plus déclenchées par un endpoint de lecture.
