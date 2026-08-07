# BAM Supervise

Application web de supervision de la synchronisation des colis entre **SMI**, **SICOM** et **SIBO**.

Le moteur compare les données à trois niveaux :

1. **Envoi** : `CODE_ENVOI` absent dans au moins un système.
2. **Service** : service `ARTICLE` absent pour un envoi présent dans les trois systèmes.
3. **Attribut** : attribut absent ou valeur différente.

Après détection, l'application analyse les données présentes dans les systèmes sources, propose un ou plusieurs **motifs probables**, puis le **superviseur** accepte, modifie ou déclare le motif inconnu. La combinaison **motif validé + système à corriger** sélectionne le groupe responsable. Un e-mail est envoyé à ses collaborateurs. La résolution n'est confirmée qu'au prochain import si l'écart a disparu.

## Fonctionnalités implémentées

- authentification d'un superviseur unique ;
- import manuel des 3 fichiers Excel SMI / SICOM / SIBO ;
- filtre `ORG_COMMERCIALE = 2000` ;
- conservation des snapshots d'envois, services et attributs ;
- détection déterministe des anomalies aux trois niveaux ;
- analyse du motif par règles métier et point d'intégration ML Scikit-learn/Joblib ;
- validation humaine du motif et du système à corriger ;
- routage `motif + système → groupe` ;
- gestion des groupes et ajout d'une adresse e-mail de collaborateur depuis l'interface ;
- envoi SMTP et historisation des destinataires ;
- recontrôle automatique lors de la campagne suivante ;
- historique des statuts et journal d'audit ;
- interface Django/Bootstrap ;
- MySQL 8 ;
- migrations Django versionnées ;
- Dockerfile + Docker Compose ;
- tests Django et CI GitHub Actions.

## Les 28 tables métier

`systeme`, `superviseur`, `service_reference`, `attribut_definition`, `service_attribut_regle`, `flux_synchronisation`, `motif`, `regle_metier`, `groupe_responsable`, `contact_groupe`, `regle_affectation`, `fichier_import`, `envoi_snapshot`, `service_snapshot`, `valeur_attribut_snapshot`, `campagne_supervision`, `campagne_import`, `anomalie`, `detail_comparaison`, `modele_ml`, `prediction_motif`, `validation_motif`, `exemple_apprentissage`, `notification`, `notification_destinataire`, `verification_resolution`, `historique_anomalie`, `journal_audit`.

> Django crée aussi ses tables techniques d'authentification, sessions et administration.

## Démarrage local avec MySQL déjà installé

### 1. Créer la base

Depuis MySQL/phpMyAdmin, exécuter `sql/001_create_database.sql` ou créer une base `bam_supervise` en `utf8mb4`.

Si MySQL tourne sur votre PC Windows, copiez `.env.example` vers `.env` et utilisez par exemple :

```env
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=bam_supervise
MYSQL_USER=bam_user
MYSQL_PASSWORD=bam_password
```

### 2. Installer Python et les dépendances

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
# source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Initialiser la base avec les migrations versionnées

```bash
python manage.py migrate
python manage.py seed_bam
python manage.py createsuperuser
```

`seed_bam` crée SMI/SICOM/SIBO, les groupes par défaut, les attributs et motifs initiaux. Les **flux exacts** restent à configurer dans `/admin/`, car ils dépendent du fonctionnement métier confirmé pour chaque échange.

### 4. Lancer

```bash
python manage.py runserver
```

Ouvrir `http://127.0.0.1:8000`.

## Configuration SMTP

Dans `.env` :

```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.votre-domaine.ma
EMAIL_PORT=587
EMAIL_HOST_USER=utilisateur
EMAIL_HOST_PASSWORD=mot-de-passe
EMAIL_USE_TLS=1
DEFAULT_FROM_EMAIL=bam-supervise@votre-domaine.ma
```

Pour un test sans envoyer de vrai mail :

```env
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
```

Les tests automatiques utilisent un backend de test et vérifient le routage, les destinataires et le contenu sans utiliser de véritables adresses BAM.

## Ajouter les cinq collaborateurs

Dans l'application : **Collaborateurs → groupe SMI/SICOM/SIBO → Ajouter email**.

La table `contact_groupe` conserve les membres. `notification_destinataire` fige les adresses réellement utilisées au moment de chaque envoi.

## Docker

```bash
cp .env.example .env
# renseigner au minimum les mots de passe et, si besoin, les paramètres SMTP

docker compose up --build
```

L'application est exposée sur `http://localhost:8000` et MySQL est conservé dans le volume `mysql_data`.

Pour créer automatiquement le premier superviseur au premier démarrage Docker, renseigner dans `.env` :

```env
DJANGO_SUPERVISEUR_USERNAME=superviseur
DJANGO_SUPERVISEUR_PASSWORD=change-me-now
DJANGO_SUPERVISEUR_EMAIL=superviseur@example.com
DJANGO_SUPERVISEUR_NOM=Superviseur BAM
```

## Tests

```bash
DB_ENGINE=sqlite python manage.py makemigrations --check --dry-run
DB_ENGINE=sqlite EMAIL_BACKEND=django.core.mail.backends.locmem.EmailBackend python manage.py test
```

L'intégration GitHub valide également les migrations et les tests sur **MySQL 8.4**, puis construit et démarre l'application avec **Docker Compose** et vérifie la réponse HTTP de la page de connexion.

Les tests couvrent notamment : normalisation minimale, détection d'un envoi absent, routage/envoi d'e-mail et ajout d'un collaborateur.

## Points à configurer avant production BAM

- paramètres SMTP réels de Barid Al-Maghrib ;
- adresses réelles des collaborateurs ;
- flux SMI/SICOM/SIBO confirmés ;
- règles métier détaillées par attribut/service/système ;
- données historiques étiquetées si un modèle ML doit être entraîné et activé ;
- secrets via coffre-fort ou variables d'environnement ;
- HTTPS/reverse proxy ;
- sauvegarde MySQL ;
- volumes et politiques de rétention des fichiers importés.
