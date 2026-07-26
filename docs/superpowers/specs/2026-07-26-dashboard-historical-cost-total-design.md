# Total historique des coûts sur l’accueil

## Objectif

Rendre visible sur la page principale du tableau de bord le coût estimé cumulé
depuis le début de l’historique Pitcrew, sans retirer l’indicateur existant sur
sept jours.

## Portée

- Un compteur d’usage cumulatif local persiste au-delà de la rétention de sept
  jours. Il est initialisé à partir des passages encore retenus lors du premier
  déploiement, puis enrichi à chaque nouveau passage enregistré.
- L’accueil affiche une nouvelle carte `Coût estimé · total historique` à côté
  des métriques globales existantes.
- La carte montre le montant en USD et une note indiquant le nombre de passages
  mesurés inclus, ainsi que ceux exclus faute de données de consommation.

## Données et comportement

Le stockage d’historique conserve un résumé cumulatif séparé de ses lignes de
détail, qui restent purgées après sept jours. Ce résumé est mis à jour de façon
atomique quand un passage est ajouté. S’il n’existe pas encore, il est créé à
partir des lignes actuellement retenues : les coûts plus anciens déjà purgés ne
peuvent pas être reconstitués.

Le serveur expose ce résumé sous `usage_total` dans le snapshot. Il utilise le
modèle enregistré par passage et les quatre catégories de jetons déjà prises en
compte par le calcul de prix.

Si aucun passage ne comporte de mesure fiable, la carte affiche `—`. Les
enregistrements incomplets ne sont pas estimés artificiellement : ils restent
exclus et sont signalés dans la note.

## Interface

L’indicateur sept jours reste inchangé afin de conserver la lecture du rythme
récent. La nouvelle carte, placée dans le même groupe de métriques, présente le
total depuis le début de l’historique. La note existante sur l’usage est étendue
pour préciser la couverture des données du total historique.

## Tests

- Un test backend vérifie l’initialisation depuis les lignes retenues et la
  conservation du cumul après la purge de ces lignes.
- Un test d’interface vérifie la présence de la nouvelle métrique et son rendu
  à partir du snapshot.
- Les tests existants de formatage et d’absence de données restent verts.

## Hors périmètre

- Conversion vers une autre devise.
- Sélecteur de période ou graphiques.
- Rétro-estimation des passages dont les jetons n’ont pas été enregistrés.
