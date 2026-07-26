# Mode correctifs autonomes

## Objectif

Permettre à chaque projet Pitcrew d'activer un mode qui ne laisse plus les
travaux `improvement` bloqués par une approbation humaine, une revue humaine ou
une CI rouge/absente. Les bugs et les propositions de fonctionnalités restent
dans leurs circuits humains existants.

## Périmètre et limites

Le paramètre persistant est propre à chaque projet et vaut `off` par défaut.
Lorsqu'il est activé, il s'applique uniquement aux tickets portant le label
configuré `improvement`, pris depuis les états `todo` ou `blocked` puis arrivé
en `review`. Il conserve la revue automatique, mais autorise la fusion après
son approbation même si la CI est rouge ou absente et sans `go` humain.

Le mode ne change pas les protections indépendantes : aucune action de prod ou
préprod, migration, lecture de secret, écriture de base de données ou opération
Git destructive n'est autorisée par ce paramètre. Il ne s'applique jamais aux
tickets `bug`, ni aux fonctionnalités : celles-ci continuent d'emprunter le
registre de propositions local et nécessitent l'action dashboard `Approuver`
avant d'être transformées en travail distant.

## Configuration et persistance

Ajouter un champ de politique validé au profil de projet :

```json
"delivery": {"fix_autonomy": "off"}
```

Les seules valeurs admises sont `off` et `on`. L'absence du champ est
interprétée comme `off`, afin de préserver les profils existants. Le dashboard
modifie ce champ par une écriture atomique du profil validé, sous verrou, puis
relit la configuration. Une erreur de validation ou d'écriture laisse l'état
précédent intact et est renvoyée sans ambiguïté au navigateur.

## Dashboard

La vue Pilotage affiche un interrupteur explicite « Correctifs autonomes » et
un état lisible (`Désactivé` / `Activé`). L'action est protégée par le jeton de
session local existant et appelle une route dédiée. Le contrôle n'est jamais
masqué par l'arrêt global : il décrit une politique durable, pas l'exécution
immédiate d'un agent.

L'activation indique que les correctifs peuvent être fusionnés malgré CI ou
revue distante non concluante ; la désactivation rétablit immédiatement les
gates humains pour les prochaines décisions de fusion. Le dashboard conserve
la file « Propositions à examiner » et ses boutons d'approbation sans aucun
changement.

## Flux de livraison

Les décisions de fusion des rôles de livraison consultent la politique du
projet au moment de décider, après avoir rechargé l'issue et la demande de
changement. Avec `fix_autonomy=on`, la revue automatique reste obligatoire et
doit être liée au SHA courant; le ticket doit porter uniquement la catégorie
`improvement`, sans label bug, proposition, fonctionnalité ou investigation,
et conserver son marqueur de prise en charge depuis `todo` ou `blocked`.
Ensuite,
mais les gates `human go` et `CI green` ne bloquent plus la fusion d'un ticket
`improvement` arrivé en `review` depuis `todo` ou `blocked`. Le rôle ajoute au
commentaire/ticket et à l'historique le fait que la fusion a été effectuée en
mode autonome, avec le résultat CI observé.

Avec `off`, le comportement actuel est inchangé. Les tickets `bug` et de
fonctionnalité ne peuvent pas devenir autonomes : l'approbation d'une
fonctionnalité est déterminée exclusivement par le registre de propositions,
avant son routage vers un agent.

## Vérification

- La validation de profil accepte `on` et `off`, rejette toute autre valeur et
  conserve la compatibilité avec un profil sans politique.
- L'API dashboard lit l'état, commute la politique de façon atomique et refuse
  les requêtes non authentifiées ou invalides.
- L'interface rend l'interrupteur, son état initial et son état après succès ou
  erreur.
- La décision de livraison conserve les gates avec `off`, les conserve aussi
  pour `bug` et les fonctionnalités avec `on`, et choisit la fusion pour un
  `improvement` revu et approuvé malgré une CI rouge/absente et sans `go`
  humain.
- Les tests de propositions confirment que le mode ne crée ni n'approuve une
  idée de fonctionnalité.
