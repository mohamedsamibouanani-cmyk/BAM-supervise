from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class Systeme(models.Model):
    code_systeme = models.CharField(max_length=10, unique=True)
    nom_systeme = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    ordre_comparaison = models.PositiveSmallIntegerField(unique=True)
    actif = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'systeme'
        ordering = ['ordre_comparaison']

    def __str__(self):
        return self.code_systeme


class Superviseur(AbstractUser):
    nom_complet = models.CharField(max_length=150, blank=True)

    class Meta:
        db_table = 'superviseur'

    def save(self, *args, **kwargs):
        if not self.pk and Superviseur.objects.exists():
            raise ValidationError('BAM Supervise autorise un seul compte superviseur.')
        if not self.nom_complet:
            self.nom_complet = self.get_full_name() or self.username
        super().save(*args, **kwargs)


class ServiceReference(models.Model):
    code_service = models.CharField(max_length=50, unique=True)
    libelle_reference = models.CharField(max_length=255)
    categorie_service = models.CharField(max_length=50, db_index=True)
    description = models.CharField(max_length=500, blank=True)
    actif = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'service_reference'

    def __str__(self):
        return f'{self.code_service} - {self.libelle_reference}'


class AttributDefinition(models.Model):
    class Portee(models.TextChoices):
        ENVOI = 'ENVOI', 'Envoi'
        SERVICE = 'SERVICE', 'Service'

    class TypeValeur(models.TextChoices):
        TEXTE = 'TEXTE', 'Texte'
        NOMBRE = 'NOMBRE', 'Nombre'
        DATE = 'DATE', 'Date'
        BOOLEEN = 'BOOLEEN', 'Booléen'

    code_attribut = models.CharField(max_length=80, unique=True)
    libelle = models.CharField(max_length=255)
    portee = models.CharField(max_length=15, choices=Portee.choices)
    type_valeur = models.CharField(max_length=15, choices=TypeValeur.choices)
    unite = models.CharField(max_length=30, blank=True)
    sensible = models.BooleanField(default=False)
    actif = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'attribut_definition'

    def __str__(self):
        return self.code_attribut


class ServiceAttributRegle(models.Model):
    service_ref = models.ForeignKey(ServiceReference, null=True, blank=True, on_delete=models.RESTRICT)
    attribut = models.ForeignKey(AttributDefinition, on_delete=models.RESTRICT)
    systeme = models.ForeignKey(Systeme, on_delete=models.RESTRICT)
    obligatoire = models.BooleanField(default=True)
    regle_validation = models.JSONField(null=True, blank=True)
    message_erreur = models.CharField(max_length=500)
    actif = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'service_attribut_regle'
        constraints = [
            models.UniqueConstraint(fields=['service_ref', 'attribut', 'systeme'], name='uq_service_attr_systeme')
        ]


class FluxSynchronisation(models.Model):
    systeme_source = models.ForeignKey(Systeme, on_delete=models.RESTRICT, related_name='flux_sortants')
    systeme_destination = models.ForeignKey(Systeme, on_delete=models.RESTRICT, related_name='flux_entrants')
    nom_flux = models.CharField(max_length=150)
    sens_fonctionnel = models.CharField(max_length=255)
    priorite = models.PositiveSmallIntegerField(default=1)
    actif = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'flux_synchronisation'
        constraints = [
            models.UniqueConstraint(fields=['systeme_source', 'systeme_destination'], name='uq_flux_source_destination'),
            models.CheckConstraint(condition=~Q(systeme_source=models.F('systeme_destination')), name='ck_flux_systemes_distincts'),
        ]

    def __str__(self):
        return self.nom_flux


class Motif(models.Model):
    class Niveau(models.TextChoices):
        ENVOI = 'ENVOI', 'Envoi'
        SERVICE = 'SERVICE', 'Service'
        ATTRIBUT = 'ATTRIBUT', 'Attribut'

    code_motif = models.CharField(max_length=80, unique=True)
    libelle = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    niveau_applicable = models.CharField(max_length=15, choices=Niveau.choices)
    categorie = models.CharField(max_length=20)
    champ_typique = models.CharField(max_length=80, blank=True)
    actif = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'motif'

    def __str__(self):
        return self.libelle


class RegleMetier(models.Model):
    flux = models.ForeignKey(FluxSynchronisation, null=True, blank=True, on_delete=models.RESTRICT)
    attribut = models.ForeignKey(AttributDefinition, null=True, blank=True, on_delete=models.RESTRICT)
    motif_suggere = models.ForeignKey(Motif, on_delete=models.RESTRICT)
    code_regle = models.CharField(max_length=80, unique=True)
    niveau_anomalie = models.CharField(max_length=15, choices=Motif.Niveau.choices)
    type_controle = models.CharField(max_length=30)
    expression_regle = models.JSONField()
    seuil_confiance = models.DecimalField(max_digits=5, decimal_places=4, default=1)
    priorite = models.PositiveSmallIntegerField(default=100)
    actif = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'regle_metier'
        ordering = ['priorite']


class GroupeResponsable(models.Model):
    systeme = models.ForeignKey(Systeme, on_delete=models.RESTRICT)
    nom_groupe = models.CharField(max_length=150)
    email_collectif = models.EmailField(blank=True)
    description = models.CharField(max_length=500, blank=True)
    actif = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'groupe_responsable'
        constraints = [models.UniqueConstraint(fields=['systeme', 'nom_groupe'], name='uq_groupe_systeme_nom')]

    def __str__(self):
        return self.nom_groupe


class ContactGroupe(models.Model):
    groupe = models.ForeignKey(GroupeResponsable, on_delete=models.CASCADE, related_name='contacts')
    nom_complet = models.CharField(max_length=150)
    email = models.EmailField()
    fonction = models.CharField(max_length=120, blank=True)
    actif = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'contact_groupe'
        constraints = [models.UniqueConstraint(fields=['groupe', 'email'], name='uq_contact_groupe_email')]

    def __str__(self):
        return f'{self.nom_complet} <{self.email}>'


class RegleAffectation(models.Model):
    systeme_a_corriger = models.ForeignKey(Systeme, on_delete=models.RESTRICT)
    motif = models.ForeignKey(Motif, on_delete=models.RESTRICT)
    groupe = models.ForeignKey(GroupeResponsable, on_delete=models.RESTRICT)
    priorite = models.PositiveSmallIntegerField(default=1)
    actif = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'regle_affectation'
        constraints = [models.UniqueConstraint(fields=['systeme_a_corriger', 'motif'], name='uq_affectation_systeme_motif')]

    def clean(self):
        if self.groupe_id and self.systeme_a_corriger_id and self.groupe.systeme_id != self.systeme_a_corriger_id:
            raise ValidationError('Le groupe doit appartenir au système à corriger.')


class FichierImport(models.Model):
    class Statut(models.TextChoices):
        RECU = 'RECU', 'Reçu'
        VALIDE = 'VALIDE', 'Validé'
        CHARGE = 'CHARGE', 'Chargé'
        ECHEC = 'ECHEC', 'Échec'

    systeme = models.ForeignKey(Systeme, on_delete=models.RESTRICT)
    superviseur = models.ForeignKey(Superviseur, on_delete=models.RESTRICT)
    nom_fichier = models.CharField(max_length=255)
    chemin_stockage = models.CharField(max_length=500)
    checksum_sha256 = models.CharField(max_length=64)
    statut_import = models.CharField(max_length=20, choices=Statut.choices, default=Statut.RECU)
    nb_lignes_source = models.PositiveIntegerField(default=0)
    nb_lignes_retenues = models.PositiveIntegerField(default=0)
    importe_le = models.DateTimeField(auto_now_add=True)
    message_erreur = models.TextField(blank=True)

    class Meta:
        db_table = 'fichier_import'
        constraints = [models.UniqueConstraint(fields=['systeme', 'checksum_sha256'], name='uq_fichier_systeme_checksum')]


class EnvoiSnapshot(models.Model):
    fichier_import = models.ForeignKey(FichierImport, on_delete=models.CASCADE, related_name='envois')
    code_envoi = models.CharField(max_length=40, db_index=True)
    num_commande = models.CharField(max_length=40, blank=True, db_index=True)
    org_commerciale = models.CharField(max_length=10, default='2000')
    date_commande = models.DateField(null=True, blank=True)
    ligne_premiere = models.PositiveIntegerField()
    empreinte_envoi = models.CharField(max_length=64, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'envoi_snapshot'
        constraints = [models.UniqueConstraint(fields=['fichier_import', 'code_envoi'], name='uq_import_code_envoi')]


class ServiceSnapshot(models.Model):
    envoi_snapshot = models.ForeignKey(EnvoiSnapshot, on_delete=models.CASCADE, related_name='services')
    service_ref = models.ForeignKey(ServiceReference, null=True, blank=True, on_delete=models.RESTRICT)
    code_service = models.CharField(max_length=50, db_index=True)
    libelle_service = models.CharField(max_length=255, blank=True)
    numero_occurrence = models.PositiveSmallIntegerField(default=1)
    ligne_source = models.PositiveIntegerField()
    empreinte_service = models.CharField(max_length=64, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'service_snapshot'
        constraints = [models.UniqueConstraint(fields=['envoi_snapshot', 'code_service', 'numero_occurrence'], name='uq_envoi_service_occurrence')]


class ValeurAttributSnapshot(models.Model):
    envoi_snapshot = models.ForeignKey(EnvoiSnapshot, null=True, blank=True, on_delete=models.CASCADE, related_name='valeurs_attribut')
    service_snapshot = models.ForeignKey(ServiceSnapshot, null=True, blank=True, on_delete=models.CASCADE, related_name='valeurs_attribut')
    attribut = models.ForeignKey(AttributDefinition, on_delete=models.RESTRICT)
    valeur_brute = models.TextField(null=True, blank=True)
    valeur_normalisee = models.CharField(max_length=500, null=True, blank=True, db_index=True)
    valeur_numerique = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    valeur_date = models.DateField(null=True, blank=True)
    est_vide = models.BooleanField(default=False)
    format_source_conforme = models.BooleanField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'valeur_attribut_snapshot'
        constraints = [
            models.CheckConstraint(
                condition=(Q(envoi_snapshot__isnull=False, service_snapshot__isnull=True) | Q(envoi_snapshot__isnull=True, service_snapshot__isnull=False)),
                name='ck_valeur_parent_xor'
            ),
            models.UniqueConstraint(fields=['envoi_snapshot', 'attribut'], condition=Q(service_snapshot__isnull=True), name='uq_envoi_attribut'),
            models.UniqueConstraint(fields=['service_snapshot', 'attribut'], condition=Q(envoi_snapshot__isnull=True), name='uq_service_attribut'),
        ]


class CampagneSupervision(models.Model):
    class Statut(models.TextChoices):
        PREPAREE = 'PREPAREE', 'Préparée'
        EN_COURS = 'EN_COURS', 'En cours'
        TERMINEE = 'TERMINEE', 'Terminée'
        ECHEC = 'ECHEC', 'Échec'

    superviseur = models.ForeignKey(Superviseur, on_delete=models.RESTRICT)
    type_declenchement = models.CharField(max_length=15, default='MANUEL')
    org_commerciale = models.CharField(max_length=10, default='2000')
    version_moteur = models.CharField(max_length=30, default='1.0.0')
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.PREPAREE)
    demarree_le = models.DateTimeField(auto_now_add=True)
    terminee_le = models.DateTimeField(null=True, blank=True)
    nb_anomalies = models.PositiveIntegerField(default=0)
    message_erreur = models.TextField(blank=True)

    class Meta:
        db_table = 'campagne_supervision'


class CampagneImport(models.Model):
    campagne = models.ForeignKey(CampagneSupervision, on_delete=models.CASCADE, related_name='imports')
    systeme = models.ForeignKey(Systeme, on_delete=models.RESTRICT)
    fichier_import = models.ForeignKey(FichierImport, on_delete=models.RESTRICT)
    role_fichier = models.CharField(max_length=20, default='COMPARAISON')
    associe_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'campagne_import'
        constraints = [
            models.UniqueConstraint(fields=['campagne', 'systeme'], name='uq_campagne_systeme'),
            models.UniqueConstraint(fields=['campagne', 'fichier_import'], name='uq_campagne_import'),
        ]

    def clean(self):
        if self.fichier_import_id and self.systeme_id and self.fichier_import.systeme_id != self.systeme_id:
            raise ValidationError('Le fichier importé doit appartenir au système associé.')


class Anomalie(models.Model):
    class Niveau(models.TextChoices):
        ENVOI = 'ENVOI', 'Envoi'
        SERVICE = 'SERVICE', 'Service'
        ATTRIBUT = 'ATTRIBUT', 'Attribut'

    class TypeEcart(models.TextChoices):
        ABSENT = 'ABSENT', 'Absent'
        DIFFERENT = 'DIFFERENT', 'Valeur différente'

    class Statut(models.TextChoices):
        DETECTEE = 'DETECTEE', 'Détectée'
        ANALYSEE = 'ANALYSEE', 'Analysée'
        VALIDEE = 'VALIDEE', 'Validée'
        NOTIFIEE = 'NOTIFIEE', 'Notifiée'
        RESOLUE = 'RESOLUE', 'Résolue'
        PERSISTANTE = 'PERSISTANTE', 'Persistante'

    class Gravite(models.TextChoices):
        FAIBLE = 'FAIBLE', 'Faible'
        MOYENNE = 'MOYENNE', 'Moyenne'
        HAUTE = 'HAUTE', 'Haute'
        CRITIQUE = 'CRITIQUE', 'Critique'

    campagne = models.ForeignKey(CampagneSupervision, on_delete=models.CASCADE, related_name='anomalies')
    niveau = models.CharField(max_length=15, choices=Niveau.choices)
    type_ecart = models.CharField(max_length=20, choices=TypeEcart.choices)
    code_envoi = models.CharField(max_length=40, db_index=True)
    code_service = models.CharField(max_length=50, blank=True, db_index=True)
    attribut = models.ForeignKey(AttributDefinition, null=True, blank=True, on_delete=models.RESTRICT)
    systeme_ecart = models.ForeignKey(Systeme, null=True, blank=True, on_delete=models.RESTRICT)
    empreinte_anomalie = models.CharField(max_length=64)
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.DETECTEE, db_index=True)
    gravite = models.CharField(max_length=15, choices=Gravite.choices, default=Gravite.MOYENNE)
    detectee_le = models.DateTimeField(auto_now_add=True, db_index=True)
    cloturee_le = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'anomalie'
        constraints = [models.UniqueConstraint(fields=['campagne', 'empreinte_anomalie'], name='uq_campagne_empreinte_anomalie')]

    def clean(self):
        if self.type_ecart == self.TypeEcart.DIFFERENT and self.niveau != self.Niveau.ATTRIBUT:
            raise ValidationError('Une différence de valeur ne peut concerner que le niveau ATTRIBUT.')
        if self.niveau != self.Niveau.ATTRIBUT and self.attribut_id:
            raise ValidationError('Un attribut ne doit être renseigné qu’au niveau ATTRIBUT.')


class DetailComparaison(models.Model):
    anomalie = models.ForeignKey(Anomalie, on_delete=models.CASCADE, related_name='details')
    systeme = models.ForeignKey(Systeme, on_delete=models.RESTRICT)
    objet_present = models.BooleanField()
    valeur_brute = models.TextField(null=True, blank=True)
    valeur_normalisee = models.CharField(max_length=500, null=True, blank=True)
    format_source_conforme = models.BooleanField(null=True, blank=True)
    est_ecart = models.BooleanField(default=False)
    ligne_source = models.PositiveIntegerField(null=True, blank=True)
    observation = models.CharField(max_length=500, blank=True)

    class Meta:
        db_table = 'detail_comparaison'
        constraints = [models.UniqueConstraint(fields=['anomalie', 'systeme'], name='uq_anomalie_systeme_detail')]


class ModeleML(models.Model):
    nom_modele = models.CharField(max_length=100)
    algorithme = models.CharField(max_length=100)
    version_modele = models.CharField(max_length=40, unique=True)
    entraine_le = models.DateTimeField()
    nb_exemples = models.PositiveIntegerField()
    metriques = models.JSONField()
    chemin_fichier = models.CharField(max_length=500)
    checksum_sha256 = models.CharField(max_length=64, unique=True)
    actif = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'modele_ml'


class PredictionMotif(models.Model):
    class Source(models.TextChoices):
        REGLE = 'REGLE', 'Règle métier'
        APPRENTISSAGE = 'APPRENTISSAGE', 'Cas appris'
        ML = 'ML', 'Machine learning'

    anomalie = models.ForeignKey(Anomalie, on_delete=models.CASCADE, related_name='predictions')
    source_prediction = models.CharField(max_length=20, choices=Source.choices)
    modele = models.ForeignKey(ModeleML, null=True, blank=True, on_delete=models.RESTRICT)
    regle = models.ForeignKey(RegleMetier, null=True, blank=True, on_delete=models.RESTRICT)
    motif = models.ForeignKey(Motif, on_delete=models.RESTRICT)
    systeme_a_corriger_predit = models.ForeignKey(Systeme, null=True, blank=True, on_delete=models.RESTRICT)
    rang = models.PositiveSmallIntegerField(default=1)
    score_confiance = models.DecimalField(max_digits=5, decimal_places=4)
    explication = models.JSONField(null=True, blank=True)
    predite_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'prediction_motif'
        constraints = [models.UniqueConstraint(fields=['anomalie', 'source_prediction', 'rang'], name='uq_anomalie_source_rang')]

    def clean(self):
        if self.source_prediction == self.Source.ML and not self.modele_id:
            raise ValidationError('Une prédiction ML doit référencer un modèle.')
        if self.source_prediction == self.Source.REGLE and not self.regle_id:
            raise ValidationError('Une prédiction par règle doit référencer une règle métier.')


class ValidationMotif(models.Model):
    class Decision(models.TextChoices):
        ACCEPTE = 'ACCEPTE', 'Accepté'
        MODIFIE = 'MODIFIE', 'Modifié'
        INCONNU = 'INCONNU', 'Inconnu'

    anomalie = models.ForeignKey(Anomalie, on_delete=models.CASCADE, related_name='validations')
    prediction_retenue = models.ForeignKey(PredictionMotif, null=True, blank=True, on_delete=models.RESTRICT)
    motif_final = models.ForeignKey(Motif, on_delete=models.RESTRICT)
    systeme_a_corriger_final = models.ForeignKey(Systeme, on_delete=models.RESTRICT)
    superviseur = models.ForeignKey(Superviseur, on_delete=models.RESTRICT)
    decision = models.CharField(max_length=15, choices=Decision.choices)
    commentaire = models.TextField(blank=True)
    version_validation = models.PositiveSmallIntegerField(default=1)
    est_finale = models.BooleanField(default=True)
    validee_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'validation_motif'
        constraints = [models.UniqueConstraint(fields=['anomalie', 'version_validation'], name='uq_anomalie_version_validation')]


class ExempleApprentissage(models.Model):
    validation = models.OneToOneField(ValidationMotif, on_delete=models.CASCADE)
    motif_label = models.ForeignKey(Motif, on_delete=models.RESTRICT)
    systeme_a_corriger_label = models.ForeignKey(Systeme, on_delete=models.RESTRICT)
    caracteristiques = models.JSONField()
    eligible = models.BooleanField(default=True)
    raison_exclusion = models.CharField(max_length=500, blank=True)
    version_dataset = models.CharField(max_length=40, blank=True, db_index=True)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'exemple_apprentissage'


class Notification(models.Model):
    class Statut(models.TextChoices):
        A_ENVOYER = 'A_ENVOYER', 'À envoyer'
        ENVOYEE = 'ENVOYEE', 'Envoyée'
        ECHEC = 'ECHEC', 'Échec'

    validation = models.ForeignKey(ValidationMotif, on_delete=models.RESTRICT, related_name='notifications')
    groupe = models.ForeignKey(GroupeResponsable, on_delete=models.RESTRICT)
    objet = models.CharField(max_length=255)
    message = models.TextField()
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.A_ENVOYER)
    nb_tentatives = models.PositiveSmallIntegerField(default=0)
    creee_le = models.DateTimeField(auto_now_add=True)
    envoyee_le = models.DateTimeField(null=True, blank=True)
    erreur = models.TextField(blank=True)

    class Meta:
        db_table = 'notification'


class NotificationDestinataire(models.Model):
    class Statut(models.TextChoices):
        EN_ATTENTE = 'EN_ATTENTE', 'En attente'
        ENVOYE = 'ENVOYE', 'Envoyé'
        ECHEC = 'ECHEC', 'Échec'

    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name='destinataires')
    contact = models.ForeignKey(ContactGroupe, null=True, blank=True, on_delete=models.SET_NULL)
    nom_snapshot = models.CharField(max_length=150)
    email_snapshot = models.EmailField()
    statut_livraison = models.CharField(max_length=20, choices=Statut.choices, default=Statut.EN_ATTENTE)
    envoyee_le = models.DateTimeField(null=True, blank=True)
    erreur = models.CharField(max_length=500, blank=True)

    class Meta:
        db_table = 'notification_destinataire'
        constraints = [models.UniqueConstraint(fields=['notification', 'email_snapshot'], name='uq_notification_email')]


class VerificationResolution(models.Model):
    class Resultat(models.TextChoices):
        RESOLUE = 'RESOLUE', 'Résolue'
        PERSISTANTE = 'PERSISTANTE', 'Persistante'
        INDETERMINABLE = 'INDETERMINABLE', 'Indéterminable'

    anomalie = models.ForeignKey(Anomalie, on_delete=models.CASCADE, related_name='verifications')
    campagne_controle = models.ForeignKey(CampagneSupervision, on_delete=models.RESTRICT)
    resultat = models.CharField(max_length=20, choices=Resultat.choices)
    commentaire = models.CharField(max_length=500, blank=True)
    verifiee_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'verification_resolution'
        constraints = [models.UniqueConstraint(fields=['anomalie', 'campagne_controle'], name='uq_anomalie_campagne_controle')]


class HistoriqueAnomalie(models.Model):
    anomalie = models.ForeignKey(Anomalie, on_delete=models.CASCADE, related_name='historique')
    ancien_statut = models.CharField(max_length=20, blank=True)
    nouveau_statut = models.CharField(max_length=20)
    source_evenement = models.CharField(max_length=20)
    superviseur = models.ForeignKey(Superviseur, null=True, blank=True, on_delete=models.RESTRICT)
    commentaire = models.CharField(max_length=500, blank=True)
    change_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'historique_anomalie'
        ordering = ['change_le']


class JournalAudit(models.Model):
    superviseur = models.ForeignKey(Superviseur, null=True, blank=True, on_delete=models.RESTRICT)
    action = models.CharField(max_length=40, db_index=True)
    entite = models.CharField(max_length=80, db_index=True)
    id_entite = models.PositiveBigIntegerField(db_index=True)
    anciennes_valeurs = models.JSONField(null=True, blank=True)
    nouvelles_valeurs = models.JSONField(null=True, blank=True)
    adresse_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'journal_audit'
        ordering = ['-cree_le']
