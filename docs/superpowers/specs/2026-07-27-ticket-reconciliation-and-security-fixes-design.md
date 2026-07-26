# Réconciliation des tickets et correctifs de sécurité

## Objectif

Empêcher le tableau Pitcrew d’associer une merge request au mauvais ticket,
réconcilier automatiquement les tickets dont la merge request est fusionnée,
et corriger les trois constats GetBill encore réellement ouverts : fuite de
jetons dans les logs, validation Stripe App fail-open et exposition de messages
d’exception par les APIs Bridge.

## Périmètre

### Pitcrew

- Une référence de ticket dans un texte de MR doit correspondre au numéro entier
  du ticket. Ainsi `#1` ne peut pas correspondre à `#15`.
- La même règle est appliquée par l’adaptateur de forge et par la vue GitLab du
  dashboard, afin que leurs données concordent.
- Une issue Pitcrew ouverte liée à une MR GitLab réellement fusionnée est admise
  par `stale-sweep`, quel que soit son label de cycle, puis passée à `done` par
  le sweep après sa propre revalidation GitLab.

### GetBill

- `AIProviderTokenService` ne journalise ni jeton, ni préfixe de jeton, ni
  identifiant dérivé du bearer. Les logs conservent seulement des métadonnées
  opérationnelles non sensibles.
- Un secret Stripe App absent est un échec de configuration : la signature n’est
  jamais contournée, y compris dans l’environnement `dev`.
- Les trois contrôleurs Bridge concernés transforment les exceptions attendues
  en messages publics constants et journalisent le détail côté serveur avec un
  contexte non sensible. Les statuts HTTP restent 400 ou 409 selon le type
  d’échec.

## Flux

1. Le dashboard extrait des références de ticket avec une frontière numérique.
2. Lorsqu’une MR associée a `state=merged` et `merged_at`, il met en file un
   `stale-sweep` dirigé.
3. Le sweep revérifie cette preuve puis applique `pitcrew-state::done` et ferme
   l’issue.
4. Les tickets #1, #2 et #12 ne sont fermés qu’après une MR de correctif
   fusionnée ; aucune association textuelle ambiguë ne suffit.

## Tests

- Tests Pitcrew : `#1` ne lie pas une MR qui cite `#15`, tandis qu’une référence
  exacte reste détectée ; les tickets à tous les cycles sont admis pour la
  réconciliation uniquement avec une MR fusionnée.
- Tests GetBill : les contextes de logs ne contiennent aucun bearer ; un secret
  Stripe App manquant est refusé en dev ; les réponses Bridge ne révèlent pas le
  texte de l’exception tout en gardant leur statut HTTP.

## Hors périmètre

- Aucun déploiement ni action preprod/prod.
- Aucun ticket n’est clos sans la preuve GitLab de la MR fusionnée et les tests
  ciblés du correctif correspondant.
