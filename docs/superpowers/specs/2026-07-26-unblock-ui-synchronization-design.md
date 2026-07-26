# Synchronisation UI après déblocage de ticket

## Contexte

Après l’envoi d’une décision de déblocage, le dashboard lance immédiatement
une actualisation complète. Le panneau de détail reste cependant ouvert, les
cartes peuvent encore refléter un état GitLab précédent, et le statut ARIA du
panneau conserve le succès de la décision précédente lorsqu’un autre ticket
est ouvert.

## Objectif

Une décision acceptée doit mettre à jour les listes concernées avant de fermer
automatiquement le panneau. Ouvrir ensuite n’importe quel autre ticket ne doit
jamais annoncer ou afficher le succès d’une décision antérieure.

## Hors périmètre

- Attendre la fin du processus `unblock` avant de rendre la main.
- Changer les transitions GitLab exécutées par le skill `unblock`.
- Modifier les comportements des propositions ou des merge requests.

## Architecture

Le flux `submitDecision` devient le point de coordination de l’interface. Après
la réponse HTTP acceptée, il invalide l’état local de la décision traitée,
force le rechargement des décisions, des exécutions et de GitLab, rerend les
vues de pilotage, puis ferme le panneau de détail.

Le statut de détail est associé au contenu actuellement ouvert : l’ouverture
d’un nouvel élément réinitialise le live region et les statuts d’action ne
sont plus réinjectés pour une décision qui n’est plus présente. L’annonce de
succès reste disponible pendant l’opération courante, mais ne fuit pas vers le
ticket suivant.

## Flux utilisateur

```text
Répondre au blocage
  -> réponse API acceptée
  -> actualisation forcée des sources liées
  -> listes rerendues
  -> panneau fermé
  -> ouverture d’un autre ticket : statut vierge
```

Si une source ne répond pas, le panneau reste ouvert et le message d’erreur
explicite indique que la décision n’a pas pu être resynchronisée visuellement.
Les données précédentes restent signalées comme anciennes selon le mécanisme
existant du `sourceStore`.

## Tests

Les tests de dashboard couvrent :

- le flux source : actualisation forcée et fermeture seulement après le rendu ;
- la réinitialisation du statut de détail à l’ouverture d’un nouvel élément ;
- la conservation du message d’erreur et du panneau ouvert lorsqu’une
  actualisation de synchronisation échoue.
