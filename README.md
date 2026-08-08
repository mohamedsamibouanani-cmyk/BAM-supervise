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
- gestion des groupes et contacts collaborateurs ;
- envoi SMTP avec identité e-mail propre à BAM Supervise et historisation des destinataires ;
- recontrôle automatique lors de la campagne suivante ;
- historique des statuts et journal d'audit ;
- interface Django/Bootstrap ;
- MySQL 8 ;
- migrations Django versionnées ;
- Dockerfile + Docker Compose ;
- tests Django et CI GitHub Actions.

## Les 28 tables métier

`systeme`, `superviseur`, `service_reference`, `attribut_definition`, `service_attribut_regle`, `flux_synchronisation`, `motif`, `regle_metier`, `groupe_responsable`, `contact_groupe`, `regle_affectation`, `fichier_import`, `envoi_snapshot`, `service_snapshot`, `valeur_attribut_snapshot`, `campagne_supervision`, `campagne_import`, `anomalie`, `detail_comparaison`, `modele_ml`, `prediction_motif`, `validation_motif`, `exemple_apprentissage`, `notification`, `notification_destinataire`, `verification_resolution`, `historique_anomalie`, `journal_audit`.

> Django crée aussi ses tables techniques d'authentification et de sessions.

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

`seed_bam` crée SMI/SICOM/SIBO, les groupes par défaut, les attributs et motifs initiaux. Les flux exacts restent à configurer selon le fonctionnement métier confirmé pour chaque échange.

### 4. Lancer

```bash
python manage.py runserver
```

Ouvrir `http://127.0.0.1:8000`.

## Boîte e-mail professionnelle BAM Supervise

L'application doit utiliser **sa propre boîte de service**, et non l'adresse personnelle d'un collaborateur. L'adresse exacte doit être créée par l'administrateur de messagerie du domaine officiel BAM, par exemple sous la forme :

```text
notifications@<domaine-officiel-BAM>
```

Ne pas inventer ou utiliser une adresse qui n'existe pas réellement.

Configuration `.env` :

```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.<domaine-officiel-BAM>
EMAIL_PORT=587
EMAIL_HOST_USER=notifications@<domaine-officiel-BAM>
EMAIL_HOST_PASSWORD=<secret-smtp>
EMAIL_USE_TLS=1
EMAIL_USE_SSL=0
EMAIL_TIMEOUT=20
EMAIL_FROM_NAME=BAM Supervise
EMAIL_FROM_ADDRESS=notifications@<domaine-officiel-BAM>
EMAIL_REPLY_TO=support@<domaine-officiel-BAM>
```

L'expéditeur visible par le collaborateur sera alors :

```text
BAM Supervise <notifications@<domaine-officiel-BAM>>
```

Les identifiants SMTP ne doivent jamais être commités dans Git : ils restent dans `.env` ou dans un coffre-fort de secrets.

Pour vérifier une vraie configuration SMTP :

```bash
python manage.py send_test_email --to destinataire@domaine.ma
```

La commande refuse le backend console et les adresses d'expéditeur de démonstration/localhost afin d'éviter un faux test positif.

Pour un test de développement qui ne doit pas envoyer de vrai mail :

```env
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
```

Dans ce mode, les messages sont affichés dans les logs et ne quittent pas l'application.

## Ajouter les collaborateurs

Dans l'application : **Collaborateurs → groupe SMI/SICOM/SIBO → Ajouter**.

La table `contact_groupe` conserve les membres. `notification_destinataire` fige les adresses réellement utilisées au moment de chaque envoi.

## Docker

```bash
cp .env.example .env
# renseigner les secrets, le mot de passe superviseur et les paramètres SMTP réels

docker compose up --build
```

L'application est exposée sur `http://localhost:8000` et MySQL est conservé dans le volume `mysql_data`.

Pour créer automatiquement le premier superviseur au premier démarrage Docker, renseigner dans `.env` :

```env
DJANGO_SUPERVISEUR_USERNAME=superviseur
DJANGO_SUPERVISEUR_PASSWORD=<mot-de-passe-fort-d-au-moins-12-caracteres>
DJANGO_SUPERVISEUR_EMAIL=<adresse-du-superviseur>
DJANGO_SUPERVISEUR_NOM=Superviseur BAM
```

Le bootstrap refuse les mots de passe de démonstration connus et applique les validateurs Django.

## Tests

```bash
DB_ENGINE=sqlite python manage.py makemigrations --check --dry-run
DB_ENGINE=sqlite EMAIL_BACKEND=django.core.mail.backends.locmem.EmailBackend python manage.py test
```

L'intégration GitHub valide également les migrations et les tests sur **MySQL 8.4**, puis construit et démarre l'application avec **Docker Compose** et vérifie la réponse HTTP de la page de connexion.

Les tests couvrent notamment : normalisation, détection d'anomalies, sécurité, routage/envoi d'e-mail, identité professionnelle de l'expéditeur et gestion des collaborateurs.

## Points à configurer avant production BAM

- création réelle de la boîte professionnelle BAM Supervise sur le domaine officiel ;
- paramètres SMTP réels de Barid Al-Maghrib ;
- adresses réelles des collaborateurs ;
- flux SMI/SICOM/SIBO confirmés ;
- règles métier détaillées par attribut/service/système ;
- données historiques étiquetées si un modèle ML doit être entraîné et activé ;
- secrets via coffre-fort ou variables d'environnement ;
- HTTPS/reverse proxy ;
- sauvegarde MySQL ;
- volumes et politiques de rétention des fichiers importés.
