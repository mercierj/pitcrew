# Arrêt global des agents et des exécutions futures

## Objectif

Permettre à l’utilisateur de couper rapidement toute consommation future de
tokens depuis le dashboard, tout en arrêtant les agents planifiés et en
empêchant les déclenchements manuels tant que le verrou global est actif.

## Choix de comportement

Le bouton « Tout arrêter » demande une confirmation explicite. Après
confirmation, l’application :

1. active un état global persistant `stopped` pour le projet ;
2. décharge tous les jobs launchd activés ;
3. laisse les exécutions en cours se terminer proprement via le mécanisme
   existant de verrouillage ;
4. refuse les nouvelles exécutions planifiées ou manuelles sans appeler Codex.

La reprise est explicite via « Réactiver les agents ». Elle repasse l’état à
`running` et réinstalle les jobs planifiés, sans lancer immédiatement de
passage surprise.

## Architecture

Le verrou est stocké dans le runtime du projet, à côté de `config.json` et de
l’historique. Le scheduler expose deux commandes globales : `stop-all` et
`resume-all`. Les commandes sont idempotentes et ciblent uniquement le projet
sélectionné.

Le runner vérifie le verrou avant toute invocation de Codex. Cette vérification
doit aussi s’appliquer lorsqu’un job launchd se déclenche pendant la fenêtre
entre l’action dashboard et son déchargement.

Le dashboard expose l’état global dans `GET /api/status` et ajoute deux actions
authentifiées à `POST /api/actions`. Les actions globales ne nécessitent pas de
skill ; les actions existantes par agent gardent leur contrat actuel.

## Interface utilisateur

Un bouton d’urgence rouge « Tout arrêter » est visible dans l’en-tête du
dashboard. Il ouvre la confirmation suivante :

> Arrêter tous les agents et bloquer les futures exécutions ?

Lorsque le verrou est actif, l’interface affiche clairement « Exécutions
bloquées » et remplace l’action principale par « Réactiver les agents ». Les
contrôles individuels sont désactivés ou indiquent que le verrou global doit
d’abord être levé. Le statut opérationnel annonce le résultat de l’action.

## Erreurs et sécurité

- Une erreur de déchargement d’un job est remontée sans masquer les autres
  tentatives ; l’état global reste `stopped` pour ne pas autoriser une nouvelle
  consommation par défaut.
- Les actions restent limitées à la session dashboard authentifiée et au
  projet configuré.
- Aucun `kill -9` ni recherche de PID hors du périmètre launchd n’est ajouté.
- Une exécution déjà lancée conserve son historique et son verrouillage ; le
  verrou global empêche seulement les nouveaux appels.

## Vérification

Ajouter ou mettre à jour des tests pour couvrir :

- l’état persistant et les commandes `stop-all` / `resume-all` ;
- le refus du runner lorsque le verrou est actif ;
- l’API dashboard et ses validations de requêtes ;
- le rendu des boutons, de la confirmation et du statut bloqué ;
- la compatibilité des contrôles individuels lorsque l’état global est
  `running`.

