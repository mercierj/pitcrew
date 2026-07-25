# CTA d’agents sur les tickets GitLab

## Objectif

Permettre de lancer depuis une carte ticket du dashboard l’agent adapté à son
état de cycle de vie, en ciblant le ticket affiché. Les états concernés sont
`todo`, `blocked` et `done`.

Le label `done` ne sera pas considéré comme une preuve de clôture réelle : le
CTA associé doit permettre à l’opérateur de déclencher la réconciliation entre
la MR, la branche et le ticket.

## Mapping fonctionnel

| État | Agent | Libellé UI | Raison |
| --- | --- | --- | --- |
| `todo` | `implementer-run` | Lancer l’implémentation | Prend en charge un ticket agent-todo et produit une modification revue. |
| `blocked` | `unblock` | Débloquer ce ticket | Resurface la décision humaine ou reprend le contexte bloqué. |
| `done` | `stale-sweep` | Vérifier la clôture | Réconcilie une MR fusionnée avec un ticket qui n’aurait pas été fermé. |

`validator-run` n’est pas utilisé pour `done` : il valide une modification
ouverte avant fusion. `releaser-run` n’est pas utilisé : il est réservé aux
releases explicitement armées.

## Architecture

Le backend enrichit chaque ticket GitLab avec une action calculée à partir de
son état. Cette action contient l’agent, le texte du bouton, la référence
GitLab canonique et l’état de disponibilité de l’agent. Le mapping reste donc
côté serveur et ne peut pas être modifié par le navigateur.

Le endpoint d’action accepte une action de lancement avec `skill` et une cible
ticket. Il valide que la compétence est autorisée, activée et que la cible
appartient au projet GitLab configuré. Le runner transmet ensuite cette cible
au mécanisme `DIRECTED-TARGET` existant afin que l’agent traite exactement la
carte sélectionnée.

Un agent désactivé ou déjà en cours ne peut pas être lancé depuis la carte. Le
dashboard affiche alors un CTA désactivé avec une explication courte.

## Flux utilisateur

1. Le dashboard charge les groupes GitLab et les agents planifiés.
2. Une carte `todo`, `blocked` ou `done` affiche son CTA associé.
3. L’opérateur clique sur le CTA.
4. Le navigateur envoie l’agent et la référence du ticket à l’API locale.
5. Le serveur valide la cible et lance un passage borné.
6. La carte indique que le lancement est accepté, puis le dashboard actualise
   son état.

Aucun CTA ne fusionne une MR, ne ferme un ticket et ne change directement son
état GitLab.

## Erreurs et sécurité

- Une cible hors du projet configuré est refusée.
- Un état non mappé ne reçoit aucune action.
- L’arrêt global, l’absence d’agent configuré et le verrouillage par agent
  restent applicables.
- Les erreurs d’action sont annoncées dans la zone d’état opérationnel et ne
  doivent pas exposer de sortie de commande ou de secret.

## Vérification

- Tests backend : mapping des états, validation de la cible, refus d’un agent
  désactivé et transmission de la cible au runner.
- Tests HTTP : contrat JSON de l’action de lancement et refus des champs
  inattendus.
- Tests dashboard : présence des trois CTA, libellés, état désactivé et corps
  de requête ciblé.
- Suite de tests déterministe du projet.
