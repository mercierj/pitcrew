# Dashboard : remonter le bloc GitLab

## Objectif

Réduire le défilement nécessaire pour accéder au travail GitLab en plaçant
le bloc « Travail GitLab » plus haut dans le document du dashboard.

## Décision

Déplacer le nœud HTML complet `#gitlab-work`, sans utiliser de réordonnancement
CSS. Le bloc sera placé immédiatement après `#overview`, avant `#live-agents`
et `#agents`.

L’ordre principal deviendra :

1. propositions et décisions urgentes ;
2. état opérationnel ;
3. travail GitLab ;
4. agents en cours ;
5. agents activés ;
6. historique des agents.

## Périmètre technique

- modifier uniquement l’ordre des sections dans `dashboard/index.html` ;
- conserver les identifiants, le contenu, le rendu dynamique et les appels
  GitLab existants ;
- ne pas modifier le chargement, le filtrage ou les actions de merge request ;
- conserver un ordre DOM cohérent pour l’accessibilité, le clavier et les
  ancres de navigation.

## Vérification

- vérifier que le bloc GitLab apparaît avant les cartes d’agents ;
- vérifier que les sections historiques restent après les agents ;
- exécuter la suite de tests du dashboard.

## Critère d’acceptation

Depuis le haut de la page, l’utilisateur atteint le travail GitLab sans
traverser les cartes des agents ni l’historique.
