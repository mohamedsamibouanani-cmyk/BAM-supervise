# Rapport de validation finale — BAM Supervise

**Date :** 8 août 2026  
**Branche validée :** `agent/initial-bam-supervise`  
**Commit validé :** `42c32e5e89fa01c2bd8061173b9a25acba092120`  
**Périmètre :** application Django de supervision SMI / SICOM / SIBO, MySQL 8.4, Docker, interface web, workflow métier, sécurité applicative et traçabilité.

## 1. Conclusion exécutive

Les contrôles automatisés finaux sont concluants sur le même commit. Le pipeline dédié de cinq cycles est terminé avec succès, la CI Django générale est verte et l’intégration indépendante MySQL/Docker est également verte.

**Décision : sur le périmètre logiciel testé, le projet BAM Supervise est terminé et prêt à être intégré dans un environnement BAM de recette/pilote.**

La mise en production reste conditionnée au paramétrage des éléments propres à l’infrastructure et au métier BAM : SMTP réel, adresses réelles des collaborateurs, flux SMI/SICOM/SIBO confirmés, HTTPS/reverse proxy, coffre de secrets et politique de sauvegarde.

## 2. Cadrage d’accès retenu

La conception métier prévoit un seul utilisateur connecté : le superviseur. Les collaborateurs SMI, SICOM et SIBO sont des contacts de notification et corrigent dans leur système source ; ils ne possèdent aucun compte BAM Supervise.

Ce modèle est conservé comme principe de moindre privilège :

- superviseur : authentification et accès aux écrans de supervision, import, validation, notification et gestion des contacts ;
- collaborateurs : aucune authentification ni permission applicative ; adresses utilisées uniquement comme destinataires des notifications ;
- actions sensibles du superviseur : historisées dans `journal_audit`.

## 3. Résultats des cinq cycles

| Cycle | Objet | Vérifications principales | Résultat |
|---|---|---|---|
| 1 | Unité & schéma | `manage.py check`, absence de migration manquante, tests utilitaires/comparaison/protection des données | ✅ Succès |
| 2 | Sécurité applicative | tests négatifs CSRF, XSS, SQLi, authentification, Argon2, en-têtes sécurité, `check --deploy` | ✅ Succès |
| 3 | Workflow métier complet | suite de tests Django complète : comparaison, contacts, email, validation, sécurité, protection | ✅ Succès |
| 4 | MySQL 8.4 | démarrage MySQL réel, migrations, seed, protection historique, checks, suite complète sur MySQL | ✅ Succès |
| 5 | Docker / HTTP / statiques / sécurité | build Compose, démarrage, réponse HTTP, CSS/Bootstrap/Chart.js locaux, headers CSP/X-Frame/nosniff/referrer, `manage.py check` dans le conteneur | ✅ Succès |

**Pipeline :** GitHub Actions run `31231485900` — cinq jobs terminés avec conclusion `success`.

Contrôles indépendants supplémentaires :

- CI générale run `31231485933` : ✅ succès ;
- intégration MySQL + Docker run `31231485924` : ✅ succès ;
- jobs MySQL et Docker de cette intégration : ✅ succès.

## 4. Corrections et améliorations apportées

### 4.1 Fichiers statiques et interface

Problème détecté : HTML rendu sans CSS dans Gunicorn/Docker.

Corrections :

- WhiteNoise ajouté pour servir correctement `/static/` avec Gunicorn ;
- Bootstrap, Bootstrap Icons et Chart.js intégrés localement dans l’image Docker ;
- suppression de la dépendance du navigateur à un CDN externe ;
- vérification automatisée de `app.css`, Bootstrap et Chart.js lors du cycle Docker.

### 4.2 Sécurité de l’authentification

- Argon2 configuré comme hasher principal ;
- validateurs de mot de passe Django conservés et longueur minimale portée à 12 caractères ;
- secret Django par défaut interdit lorsque `DEBUG=0` ;
- sessions fermées à la fermeture du navigateur ;
- cookies HttpOnly/SameSite ; flags Secure activables pour HTTPS.

### 4.3 Protection CSRF, XSS et SQL Injection

- middleware CSRF Django actif ;
- test négatif avec client imposant le contrôle CSRF : modification sans jeton rejetée en HTTP 403 ;
- auto-échappement Django testé avec une charge `<script>` stockée : script non exécuté/rendu brut ;
- recherches construites via Django ORM ; charge de type `' OR 1=1 --` testée sans injection ni mutation de données.

### 4.4 En-têtes navigateur et défense en profondeur

Ajout et validation de :

- `Content-Security-Policy` ;
- `X-Frame-Options: DENY` ;
- `X-Content-Type-Options: nosniff` ;
- `Referrer-Policy: same-origin` ;
- `Permissions-Policy` limitant caméra, microphone et géolocalisation ;
- `Cross-Origin-Opener-Policy: same-origin` ;
- HSTS / redirection SSL configurables pour l’environnement HTTPS BAM.

### 4.5 Chiffrement des données sensibles

- clé applicative `BAM_DATA_ENCRYPTION_KEY` distincte requise en production ;
- valeurs sensibles telles que le téléphone chiffrées au repos avec Fernet ;
- comparaison des valeurs sensibles via empreinte HMAC-SHA256 déterministe, sans stocker la valeur comparable en clair ;
- valeurs sensibles masquées dans les preuves de comparaison affichables ;
- fichiers Excel sources chiffrés avant persistance dans le volume `media` ;
- anciens fichiers/snapshots en clair convertis automatiquement par une commande idempotente de durcissement ;
- ordre des opérations fichier/base conçu pour éviter de supprimer un original avant la mise à jour réussie du pointeur SQL.

### 4.6 Import et robustesse

- parsing Excel en mémoire ;
- copie persistée uniquement sous forme chiffrée ;
- échec d’un classeur invalide conservé dans `fichier_import` avec statut `ECHEC` et diagnostic ;
- protection contre le double import par checksum ;
- support `.xlsx` et `.xls` aligné avec l’interface ;
- filtre métier `ORG_COMMERCIALE = 2000` maintenu.

### 4.7 Gestion des collaborateurs

- ajout, modification et activation/désactivation des contacts SMI/SICOM/SIBO ;
- aucune suppression destructive requise pour conserver l’historique ;
- ancienne et nouvelle valeur historisées lors d’une modification ;
- collaborateur confirmé comme non-authentifiable dans les tests.

### 4.8 SMTP et traçabilité

- paramètres SMTP externalisés ;
- TLS/SSL configurables et incompatibilité simultanée bloquée ;
- timeout SMTP configurable ;
- notification et destinataires historisés en base ;
- routage fondé sur motif final + système à corriger ;
- tests utilisent un backend mémoire/console pour ne pas envoyer vers des adresses réelles.

## 5. Architecture et bonnes pratiques

L’application conserve une séparation claire :

- modèles Django / MySQL : persistance et contraintes ;
- services `importer`, `comparison`, `analysis`, `notifications`, `security` : logique métier ;
- vues/formulaires : orchestration et validation HTTP ;
- templates/static : présentation ;
- Docker Compose : web + MySQL isolés ;
- journal d’audit : traçabilité des actions sensibles.

Les requêtes applicatives utilisent l’ORM Django, ce qui réduit le risque de SQL Injection par rapport à des chaînes SQL assemblées manuellement.

## 6. Interface utilisateur

La version validée comprend :

- dashboard professionnel avec KPI et graphiques ;
- navigation latérale responsive ;
- registre des anomalies avec recherche/filtres ;
- investigation SMI/SICOM/SIBO ;
- motifs probables et score de confiance ;
- décision du superviseur séparée de la prédiction ;
- historique des campagnes ;
- suivi des notifications ;
- gestion des groupes/collaborateurs ;
- palette sobre avec accent rouge institutionnel inspiré MIT ;
- ressources CSS/JS locales validées dans Docker.

## 7. Conformité et limites de la déclaration

Les contrôles réalisés sont alignés avec des pratiques courantes de sécurité web Django/OWASP : authentification robuste, contrôle d’accès, CSRF, échappement XSS, ORM, secrets externalisés, chiffrement au repos, en-têtes de sécurité, audit et tests de déploiement.

**Ce rapport n’est pas une certification formelle ISO 27001, SOC 2, PCI-DSS ou OWASP.** Une certification internationale nécessite un audit organisationnel et infrastructurel indépendant dépassant le code applicatif.

## 8. Éléments à fournir avant production BAM

1. SMTP BAM réel et politique de certificat/TLS.
2. Adresses réelles des collaborateurs/groupes.
3. Sens exacts des flux SMI/SICOM/SIBO validés avec le métier.
4. Secrets forts stockés dans un coffre de secrets : `DJANGO_SECRET_KEY`, `BAM_DATA_ENCRYPTION_KEY`, mots de passe DB/SMTP.
5. Reverse proxy HTTPS, certificat, activation des flags Secure/HSTS.
6. Sauvegarde/restauration MySQL et politique de rétention des fichiers chiffrés.
7. Données historiques labellisées avant d’activer un modèle ML réellement entraîné en production.
8. Recette métier BAM sur des fichiers représentatifs réels.

## 9. Statut final

**STATUT LOGICIEL : VALIDÉ.**  
**PROJET : TERMINÉ SUR LE PÉRIMÈTRE LOGICIEL TESTÉ.**  
**INTÉGRATION : PRÊT POUR INTÉGRATION BAM EN RECETTE / PILOTE.**

La mise en production finale est une étape d’intégration d’infrastructure et de paramétrage métier, non une correction du socle logiciel validé dans ce rapport.
