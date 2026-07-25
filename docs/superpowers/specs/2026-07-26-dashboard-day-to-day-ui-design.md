# Refonte du dashboard Pitcrew pour le pilotage quotidien

**Date :** 2026-07-26  
**Statut :** Design validé en conversation, en attente de revue de la spécification écrite

## Objectif

Permettre à l’opérateur de comprendre en moins de cinq secondes :

1. quelles interventions humaines sont requises ;
2. où en sont les tickets et merge requests ;
3. si les agents fonctionnent normalement.

L’interface est conçue en priorité pour un ordinateur portable. Elle doit réduire
le défilement, rendre le flux de livraison central et repousser les détails
techniques des agents dans un second niveau de lecture.

## Principes

- Les interventions humaines passent avant les métriques et la supervision.
- Le ticket ou la merge request est l’unité principale de lecture.
- GitLab reste la source de vérité du cycle de livraison.
- Les actions disponibles sont visibles dans leur contexte.
- Les informations techniques restent accessibles sans saturer la vue principale.
- Un service indisponible ne doit pas effacer les dernières données exploitables.

## Structure de navigation

Le dashboard devient une coque applicative compacte :

- une barre supérieure contient le projet courant, l’état de synchronisation,
  l’actualisation manuelle et l’arrêt global ;
- une navigation latérale donne accès à `Pilotage`, `Agents` et `Historique` ;
- un résumé permanent de la santé du crew affiche les nombres d’agents sains, en
  alerte, en échec et en cours ;
- la page `Pilotage` porte la file d’interventions et le tableau de flux.

L’arrêt global reste protégé par une confirmation. Il demeure visible dans la
barre supérieure mais ne concurrence pas les actions quotidiennes.

## Page Pilotage

### File d’interventions humaines

Une file unique remplace les grands panneaux indépendants de décisions,
propositions et merge requests. Elle apparaît en premier et contient uniquement
les éléments qui attendent une action de l’opérateur :

- question `unblock` à laquelle répondre ;
- proposition à approuver, rejeter ou investiguer ;
- merge request prête à être fusionnée ;
- autre état local explicitement classé comme actionnable.

Chaque entrée affiche le type, le titre, la raison de l’intervention et un bouton
principal. Trois entrées au maximum sont visibles dans la rangée initiale ; les
suivantes sont accessibles par `Voir toutes les actions`.

### Tableau de flux

Le contenu principal est un Kanban compact à quatre colonnes :

1. `À faire` ;
2. `En cours` ;
3. `En revue` ;
4. `Bloqué`.

Les éléments `Terminés` sont repliés par défaut dans une rangée sous le tableau.
Leur ouverture affiche les éléments terminés aujourd’hui sans changer de page.
L’historique complet reste disponible dans la navigation.

Une carte présente :

- l’identifiant et le type GitLab ;
- le titre ;
- l’ancienneté de la dernière mise à jour ;
- les étiquettes réellement utiles à la décision ;
- l’agent responsable ou actif ;
- l’état de la MR et du pipeline lorsqu’ils existent ;
- une seule action principale adaptée à l’état.

Le tableau offre une recherche et un filtre par rôle. Il ne permet pas le
glisser-déposer : les transitions continuent à passer par les actions existantes
et GitLab reste la source de vérité.

## Panneau de détail

Un clic sur une carte ouvre un panneau latéral sans quitter le tableau. Il
regroupe :

- le contexte et le lien GitLab ;
- la MR liée, ses branches, son auteur et son pipeline ;
- l’agent courant, sa phase et son dernier résumé borné ;
- les informations utiles de la dernière exécution ;
- les actions disponibles pour cet état.

La file d’interventions ouvre le même panneau directement sur le bloc d’action
concerné. Le panneau est refermable avec un bouton explicite et la touche
`Escape`. Le focus clavier entre dans le panneau à l’ouverture, y reste pendant
son affichage, puis revient à l’élément déclencheur à la fermeture.

## Page Agents

Les cartes actuelles sont remplacées par une liste compacte. Chaque ligne affiche
par défaut :

- le nom et la description courte du rôle ;
- la santé et l’état courant ;
- le dernier et le prochain passage ;
- un résumé concis de la dernière exécution.

Une zone dépliable expose le modèle, les jetons, le coût estimé, la fréquence, le
PID et les contrôles `Déclencher`, `Réinstaller` et `Arrêter`. Les rôles désactivés
restent dans une section repliée avec leur motif.

## Page Historique

L’historique conserve la période de sept jours et les filtres par agent et
résultat. Il devient une page dédiée afin de ne plus allonger la vue quotidienne.
Chaque entrée peut rouvrir le panneau de détail du ticket ou de l’agent associé
lorsque cette relation est connue.

## Comportement des actions

Les actions restent protégées par le jeton de session local et les validations
serveur existantes. Chaque action suit le même cycle visuel sur l’élément
concerné :

1. désactivation du contrôle et libellé d’attente ;
2. confirmation locale de l’acceptation ;
3. rafraîchissement de la seule donnée nécessaire ;
4. message de succès ou erreur concise et exploitable.

Une actualisation générale ne doit pas supprimer un succès ou une erreur avant
que l’opérateur ait pu le lire. Les actions destructives ou globales conservent
une confirmation explicite.

## Architecture frontend

L’implémentation reste en HTML, CSS et JavaScript natif, sans framework ni
dépendance runtime supplémentaire. Le fichier monolithique `dashboard/app.js`
est découpé en modules à responsabilité unique :

- accès API, cadence de rafraîchissement et erreurs réseau ;
- normalisation de l’état de la vue ;
- file d’interventions ;
- tableau de flux et cartes ;
- panneau de détail ;
- liste des agents ;
- historique et fonctions de formatage partagées.

Le frontend construit un modèle de vue par ticket à partir des réponses
existantes de statut local, historique, décisions, propositions et travail
GitLab. Le backend peut ajouter un identifiant de corrélation borné ou des champs
de présentation manquants, mais cette refonte ne crée ni nouvelle base de données
ni nouveau système de cycle de vie.

Les modifications actuellement non committées relatives au lancement d’agents
depuis les tickets et à la synchronisation des tickets fusionnés sont conservées
et intégrées au nouveau rendu.

## Rafraîchissement et données dégradées

- Les données locales sont interrogées toutes les dix secondes.
- Les données GitLab sont interrogées au plus toutes les soixante secondes, sauf
  actualisation manuelle.
- Le dernier résultat valide de chaque source reste affiché si la requête suivante
  échoue.
- La zone concernée porte alors l’étiquette `Données anciennes`, l’heure de la
  dernière réussite et une action `Réessayer`.
- Une panne GitLab ne bloque ni la lecture ni les contrôles des agents locaux.
- Un échec local ne masque pas les données GitLab déjà chargées.

Les états de chargement, vide, arrêté, bloqué et indisponible ont chacun un texte
court et une prochaine action explicite.

## Responsive et accessibilité

La cible principale est le laptop. À largeur réduite, le tableau peut défiler
horizontalement dans sa propre zone sans faire défiler toute la page. La
navigation latérale se compacte mais les actions prioritaires restent en premier.

Toutes les actions restent accessibles au clavier. Les couleurs ne sont jamais
le seul signal d’état. Les boutons ont des libellés explicites, les changements
d’état importants utilisent une région `aria-live`, et les préférences de
réduction des animations sont respectées.

## Hors périmètre

- Glisser-déposer des tickets entre colonnes.
- Modification libre des étiquettes ou du contenu GitLab.
- Nouvelle application mobile dédiée.
- Nouvelle base de données, analytics permanents ou transcripts complets.
- Contrôles de déploiement, de production ou de préproduction.
- Migration vers un framework frontend.

## Validation

Les tests automatisés couvrent :

- la normalisation des éléments de la file et des colonnes ;
- l’ordre `interventions`, `flux actif`, `terminés` ;
- les états de chargement, vide, succès, erreur et données anciennes ;
- l’ouverture, la fermeture et la restauration du focus du panneau latéral ;
- la disponibilité contextuelle et les états d’attente des actions ;
- le rendu compact et le dépliage des détails agents ;
- la conservation des protections de session, d’origine et de cible.

Un parcours dans un vrai navigateur valide :

1. la lecture en moins de cinq secondes sur une largeur laptop ;
2. la réponse à une décision ;
3. l’examen d’une proposition ;
4. la fusion d’une MR éligible ;
5. le lancement d’un agent depuis un ticket ;
6. l’affichage conservé après une panne GitLab simulée ;
7. la navigation complète au clavier.

## Critères de réussite

La refonte est réussie lorsque l’opérateur peut, sans défilement initial :

- voir toutes les interventions humaines en attente ;
- comprendre la répartition du travail actif ;
- identifier un blocage ou une MR prête ;
- confirmer la santé globale du crew ;
- ouvrir le détail ou lancer l’action adaptée en un clic.
