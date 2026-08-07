from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import *

admin.site.register(Superviseur, UserAdmin)
for model in [
    Systeme, ServiceReference, AttributDefinition, ServiceAttributRegle,
    FluxSynchronisation, Motif, RegleMetier, GroupeResponsable, ContactGroupe,
    RegleAffectation, FichierImport, EnvoiSnapshot, ServiceSnapshot,
    ValeurAttributSnapshot, CampagneSupervision, CampagneImport, Anomalie,
    DetailComparaison, ModeleML, PredictionMotif, ValidationMotif,
    ExempleApprentissage, Notification, NotificationDestinataire,
    VerificationResolution, HistoriqueAnomalie, JournalAudit,
]:
    admin.site.register(model)
