# Mode correctifs autonomes

## Objectif

Permettre à chaque projet Pitcrew d'activer un mode qui ne laisse plus les
correctifs bloqués par une approbation humaine, une revue humaine ou une CI
rouge/absente. Les propositions de fonctionnalités restent soumises à une
approbation explicite dans le dashboard avant toute création ou progression de
ticket.

## Périmètre et limites

Le paramètre persistant est propre à chaque projet et vaut `off` par défaut.
Lorsqu'il est activé, il s'applique uniquement aux changements qualifiés de
correctifs par le flux de livraison. Il autorise leur fusion après les
vérifications locales minimales du rôle, même si les contrôles distants de CI,
de revue ou de `go` humain sont absents ou négatifs.

Le mode ne change pas les protections indépendantes : aucune action de prod ou
préprod, migration, lecture de secret, écriture de base de données ou opération
Git destructive n'est autorisée par ce paramètre. Les fonctionnalités
continuent d'emprunter le registre de propositions local et nécessitent
l'action dashboard `Approuver` avant d'être transformées en travail distant.

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

Les décisions de fusion des rôles de correction consultent la politique du
projet au moment de décider, après avoir rechargé l'issue et la demande de
changement. Avec `fix_autonomy=on`, les gates `review approved`, `human go` et
`CI green` ne bloquent plus la fusion. Le rôle ajoute au commentaire/ticket et
à l'historique le fait que la fusion a été effectuée en mode autonome, avec le
résultat CI observé.

Avec `off`, le comportement actuel est inchangé. Un ticket de fonctionnalité
ne peut pas devenir autonome : son approbation est déterminée exclusivement
par le registre de propositions, avant son routage vers un agent.

## Vérification

- La validation de profil accepte `on` et `off`, rejette toute autre valeur et
  conserve la compatibilité avec un profil sans politique.
- L'API dashboard lit l'état, commute la politique de façon atomique et refuse
  les requêtes non authentifiées ou invalides.
- L'interface rend l'interrupteur, son état initial et son état après succès ou
  erreur.
- La décision de livraison conserve les gates avec `off` et choisit la fusion
  avec `on` malgré une CI rouge/absente et sans `go` humain.
- Les tests de propositions confirment que le mode ne crée ni n'approuve une
  idée de fonctionnalité.
