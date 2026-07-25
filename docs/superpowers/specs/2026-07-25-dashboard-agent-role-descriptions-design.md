# Descriptions des rôles dans les cards des agents

## Objectif

Afficher dans chaque card du dashboard une description courte du rôle de
l’agent, afin que son nom technique soit immédiatement compréhensible.

## Design

Le backend lit la description présente dans le frontmatter de chaque
`skills/<role>/SKILL.md` et l’ajoute aux données publiques de l’agent dans le
snapshot du dashboard. Le frontend affiche cette description sous le nom de
l’agent. Les descriptions restent optionnelles : une card sans description
continue d’être affichée normalement.

## Tests

Les tests du snapshot vérifient qu’une description de rôle est exposée pour
les agents activés et désactivés. Le rendu frontend utilise ce champ sans
modifier les actions, le statut ou les métriques existantes.
