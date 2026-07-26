# Réconciliation fidèle des tickets fusionnés

## Objectif

Un ticket Pitcrew ouvert avec une merge request GitLab fusionnée doit être
reconnu comme une dérive de cycle, quel que soit son état local actuel, puis
être fermé par `stale-sweep` avec le label `pitcrew-state::done`.

## Problème confirmé

Le tableau de bord détecte correctement une MR fusionnée liée à un ticket
ouvert et programme un `stale-sweep`. En revanche, le validateur du
planificateur n’accepte actuellement `stale-sweep` que pour les tickets déjà
étiquetés `pitcrew-state::done`. Les tickets `todo`, `processing`, `review` ou
`blocked` sont donc rejetés avant que le sweep puisse les réconcilier.

## Design retenu

### Validation de cible

Pour le rôle `stale-sweep`, accepter une issue GitLab ouverte qui porte le
label `pitcrew-agent` et l’un des cinq labels de cycle configurés : `todo`,
`processing`, `review`, `blocked` ou `done`.

Cette permission est spécifique au rôle de réconciliation. Les autres rôles
gardent leurs règles d’éligibilité inchangées.

### Source de vérité et mutation

Le validateur élargi autorise seulement le lancement du sweep ; il ne décide
jamais qu’un ticket est terminé. `stale-sweep` doit toujours reconsulter la MR
et n’exécuter `CLOSE_LIFECYCLE` que si GitLab fournit `state=merged` avec un
`merged_at` non nul. Une MR ouverte ou fermée sans fusion ne peut pas faire
passer un ticket en `done`.

### Réparation de l’état actuel

Après le correctif et ses tests, lancer une réconciliation dirigée des tickets
Pitcrew dont les MRs associées sont déjà fusionnées. Pour l’état observé le
26 juillet 2026, les tickets #11, #13 et #14 doivent être clos avec
`pitcrew-state::done`. Le ticket #12 reste `pitcrew-state::blocked`, car sa
MR n’est pas la preuve d’une clôture du ticket.

## Tests d’acceptation

1. Un `stale-sweep` peut lier un ticket `pitcrew-agent` + `blocked`.
2. Il peut aussi lier les quatre autres états de cycle configurés.
3. Un ticket sans `pitcrew-agent`, sans état de cycle, fermé, ou hors projet
   reste rejeté.
4. Les règles de validation des autres rôles restent inchangées.
5. La suite des tests du dispatcher et des contrats de skill passe.

## Hors périmètre

- Ne pas modifier les décisions humaines de blocage qui n’ont pas de MR
  fusionnée.
- Ne pas relâcher la preuve de fusion GitLab exigée par `stale-sweep`.
- Ne pas modifier les règles de merge, de validation ou de release.
