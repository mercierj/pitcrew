# Admission événementielle des tickets du manager

## Problème

`manager-run` crée et étiquette des tickets GitLab éligibles à l’implémentation,
mais ne les transmet pas au coordinateur local. Les tickets apparaissent donc
dans la vue GitHub · GitLab sans qu’`implementer-run` ne démarre.

## Objectif

À chaque création par `manager-run` d’un ticket non risqué portant le label
agent et l’état `todo`, PitCrew admet immédiatement une exécution ciblée
`implementer-run` dans la file SQLite locale. Le coordinateur la lance selon
sa capacité et sa déduplication existantes.

## Hors périmètre

- Aucun polling GitLab pour découvrir des tickets créés hors de PitCrew.
- Aucun traitement automatique des tickets risqués : ils continuent vers
  `investigate-run` puis `unblock`.
- Aucun changement des règles de création, de priorité ou de pacing du manager.

## Conception

`manager-run` appelle l’admission existante du `RunDispatcher` immédiatement
après avoir créé et relu un ticket agent : `skill=implementer-run` et
`target=<url canonique>`. Le `RunStore` conserve son index d’unicité : une
reprise du manager ou une réponse GitLab incertaine ne peut pas créer deux
exécutions actives pour le même ticket.

L’admission n’est tentée qu’après la création GitLab confirmée et seulement
pour la route agent. Un ticket investigate ne produit aucune admission
d’implémentation. Si la file locale est indisponible, le ticket GitLab reste
créé et le passage manager retourne un résultat actionnable : il ne prétend pas
que le ticket est pris en charge.

## Flux

```text
finding approuvé
  -> manager-run crée le ticket GitLab agent/todo
  -> manager-run relit son URL canonique
  -> manager-run appelle RunStore.enqueue(implementer-run, target)
  -> RunDispatcher.drain()
  -> implementer-run
```

Les suites `implementer -> reviewer -> validator` continuent d’utiliser le
chaînage événementiel existant.

## Vérification

- Un test de régression montre qu’une création manager sur la route agent
  appelle l’admission ciblée et le drainage.
- Le même test montre qu’un ticket investigate n’admet pas l’implémenteur.
- Une seconde admission de la même URL réutilise le run actif.
- Les tests ciblés du dispatcher et du scheduler restent verts.
