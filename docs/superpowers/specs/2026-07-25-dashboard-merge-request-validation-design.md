 # Dashboard : validation des merge requests ouvertes

 ## Objectif

 Permettre de suivre depuis le dashboard local les merge requests GitLab
 ouvertes produites par les agents, de vérifier clairement leurs branches et
 de les fusionner depuis la même interface. Après une fusion réussie, la
 branche source est supprimée et la MR disparaît de la liste au prochain
 rafraîchissement.

 ## Périmètre

 Inclus :

 - afficher uniquement les merge requests GitLab ouvertes du projet configuré ;
 - afficher le titre, l’IID, le lien GitLab, l’auteur, la branche source, la
   branche cible, l’état du pipeline et le ticket lié lorsqu’il est détectable ;
 - autoriser la fusion même si le pipeline n’est pas vert ;
 - demander une confirmation avant l’action irréversible ;
 - fusionner la MR puis supprimer sa branche source ;
 - rafraîchir les données et retirer la MR fusionnée de la liste ;
 - conserver un état dégradé lisible si GitLab est indisponible.

 Hors périmètre : approbation GitLab séparée, édition de MR, affichage des MR
 fusionnées ou fermées, modification des pipelines, déploiement, action sur un
 autre projet ou une autre forge.

 ## Architecture et flux

 Le panneau « Travail GitLab » existant devient la surface de validation. Le
 backend continue d’utiliser la configuration runtime validée et le projet
 GitLab configuré ; il ne prend jamais un projet, une URL ou une branche depuis
 le navigateur pour choisir la cible de l’opération.

 Le relevé GitLab demande les MR avec `state=opened` et normalise les champs
 utiles pour le frontend. Les champs de branche et de pipeline sont conservés
 à partir de la réponse GitLab. Les MR sont rendues dans une section distincte
 du regroupement des tickets, afin que leur statut ne soit pas confondu avec le
 cycle de vie `todo`/`processing`/`review`.

 Le bouton d’action envoie l’IID de la MR et une intention explicite de fusion.
 Le service :

 1. valide l’IID et acquiert le verrou d’action du dashboard ;
 2. recharge la MR dans le projet configuré et refuse toute MR qui n’est plus
    ouverte ;
 3. refuse les MR ciblant `preprod` ou `prod`, conformément au contrat GetBill ;
 4. fusionne la MR via l’API GitLab sans exiger un pipeline réussi ;
 5. supprime la branche source uniquement après confirmation de la fusion ;
 6. invalide le cache GitLab et renvoie un résultat JSON explicite.

 L’interface désactive le bouton pendant l’appel, affiche le résultat dans la
 zone de statut accessible, puis force un rafraîchissement GitLab. En cas
 d’échec de la fusion, la branche n’est pas supprimée. En cas de fusion réussie
 mais d’échec de suppression de branche, l’opération est signalée comme
 partiellement réussie ; la MR disparaît néanmoins de la liste des MR ouvertes
 au prochain relevé, et aucune seconde fusion n’est tentée automatiquement.

 ## Sécurité et garde-fous

 L’action reste protégée par le jeton de session existant et par une confirmation
 explicite côté interface. Le serveur utilise exclusivement le projet GitLab de
 la configuration runtime et les endpoints API encodés par l’IID validé. Les
 messages d’erreur sont nettoyés des secrets comme les autres erreurs GitLab.

 La suppression de branche est une conséquence limitée de la fusion demandée,
 et n’est effectuée que sur la branche source renvoyée par GitLab pour cette MR.
 Les branches `preprod` et `prod` ne sont jamais mutées par cette action.

 ## Tests

 - tests backend : filtrage des MR ouvertes, normalisation des branches et du
   pipeline, rejet d’un IID invalide, rejet d’une MR fermée et garde-fou des
   branches de déploiement ;
 - tests backend : séquence API fusion puis suppression de branche, invalidation
   du cache, et absence de suppression si la fusion échoue ;
 - tests HTTP : session obligatoire, validation des champs et codes d’erreur ;
 - tests frontend contractuels : texte de branche source/cible, confirmation,
   état de soumission et rafraîchissement après succès ;
 - tests de non-régression : état dégradé GitLab et affichage existant des
   tickets restent fonctionnels.

 ## Critères d’acceptation

 1. Une MR ouverte visible dans GitLab apparaît dans le dashboard avec
    `source → cible` lisible.
 2. Une MR dont le pipeline est rouge ou absent reste fusionnable après
    confirmation.
 3. Une fusion réussie supprime la branche source et la MR n’apparaît plus
    dans la liste ouverte après actualisation.
 4. Une fusion échouée ne supprime jamais la branche source.
 5. Une MR ciblant `preprod` ou `prod` est refusée par le backend, même si le
    frontend tente l’action.
 6. GitLab indisponible ne fait pas disparaître les données locales ni ne rend
    l’action silencieusement disponible.
