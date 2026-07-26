# Dashboard : retirer le doublon GitHub · GitLab

## Objectif

Le dashboard ne doit plus afficher une seconde vue du travail GitHub · GitLab
sous le kanban principal. Les tickets doivent continuer d'alimenter le premier
kanban (`Workflow`), qui est la vue de suivi unique.

## Décision

Retirer le bloc visuel `#forge-work` et son rendu dédié (`renderForgeWork` et
`renderChanges`). Conserver le chargement de `/api/forge-work` et le modèle
`sources.work`, car le kanban principal les consomme via `renderPilotageView`.
L'action locale de fusion reste dans le détail d'un ticket du kanban principal.

Le libellé général du header sera ajusté pour ne plus présenter GitHub · GitLab
comme une section de dashboard distincte.

## Hors périmètre

- Changer la collecte forge, son cache, ou l'endpoint `/api/forge-work`.
- Retirer les tickets terminés du kanban principal : sa règle actuelle reste
  la source de vérité.
- Modifier les actions de lancement d'agents depuis les cartes Workflow.

## Vérification

- Les assets ne contiennent plus `#forge-work` ni les cartes de changements.
- Le chargement forge reste présent et `renderPilotageView` reçoit toujours
  `sources.work`.
- Les tests du dashboard et du modèle de workflow passent.
