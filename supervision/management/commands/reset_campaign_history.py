from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from supervision.models import (
    Anomalie,
    CampagneImport,
    CampagneSupervision,
    DetailComparaison,
    EnvoiSnapshot,
    ExempleApprentissage,
    FichierImport,
    HistoriqueAnomalie,
    JournalAudit,
    ModeleML,
    Notification,
    NotificationDestinataire,
    PredictionMotif,
    ServiceSnapshot,
    ValidationMotif,
    ValeurAttributSnapshot,
    VerificationResolution,
)


CONFIRMATION = 'SUPPRIMER'

CAMPAIGN_AUDIT_ENTITIES = (
    'campagne_supervision',
    'validation_motif',
    'notification',
)

EMPTY_HISTORY_MODELS = (
    CampagneSupervision,
    CampagneImport,
    FichierImport,
    EnvoiSnapshot,
    ServiceSnapshot,
    ValeurAttributSnapshot,
    Anomalie,
    DetailComparaison,
    PredictionMotif,
    ValidationMotif,
    ExempleApprentissage,
    Notification,
    NotificationDestinataire,
    VerificationResolution,
    HistoriqueAnomalie,
    ModeleML,
)


def _safe_media_file(path_value):
    """Return a file below MEDIA_ROOT, never an arbitrary recorded path."""
    if not path_value:
        return None
    media_root = Path(settings.MEDIA_ROOT).resolve()
    candidate = Path(path_value).resolve()
    try:
        candidate.relative_to(media_root)
    except ValueError:
        return None
    return candidate


def _reset_history_sequences():
    tables = [model._meta.db_table for model in EMPTY_HISTORY_MODELS]
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        if connection.vendor == 'mysql':
            for table in tables:
                cursor.execute(f'ALTER TABLE {quote(table)} AUTO_INCREMENT = 1')
            return
        if connection.vendor == 'sqlite':
            placeholders = ', '.join(['%s'] * len(tables))
            cursor.execute(
                f'DELETE FROM sqlite_sequence WHERE name IN ({placeholders})',
                tables,
            )
            return

        # Fallback for another backend supported by Django.
        from django.core.management.color import no_style

        for statement in connection.ops.sequence_reset_sql(no_style(), EMPTY_HISTORY_MODELS):
            cursor.execute(statement)


class Command(BaseCommand):
    help = (
        'Supprime irréversiblement toutes les données de campagnes, leurs imports, '
        'anomalies, notifications et données apprises, sans toucher aux référentiels '
        'ni au compte superviseur.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm',
            required=True,
            help=f'Confirmation obligatoire : --confirm {CONFIRMATION}',
        )

    def handle(self, *args, **options):
        if options['confirm'] != CONFIRMATION:
            raise CommandError(
                f'Confirmation refusée. Utilisez exactement --confirm {CONFIRMATION}.'
            )

        import_files = list(
            FichierImport.objects.exclude(chemin_stockage='')
            .values_list('chemin_stockage', flat=True)
        )
        model_files = list(
            ModeleML.objects.exclude(chemin_fichier='')
            .values_list('chemin_fichier', flat=True)
        )
        summary = {
            'campagnes': CampagneSupervision.objects.count(),
            'imports': FichierImport.objects.count(),
            'anomalies': Anomalie.objects.count(),
            'notifications': Notification.objects.count(),
            'exemples': ExempleApprentissage.objects.count(),
            'modeles': ModeleML.objects.count(),
        }

        # RESTRICT protects historical evidence during normal operation. The reset
        # command therefore removes the protected dependants explicitly first.
        with transaction.atomic():
            Notification.objects.all().delete()
            VerificationResolution.objects.all().delete()
            CampagneSupervision.objects.all().delete()
            FichierImport.objects.all().delete()
            ModeleML.objects.all().delete()
            JournalAudit.objects.filter(entite__in=CAMPAIGN_AUDIT_ENTITIES).delete()

        # Make the next campaign and its execution rows start at identifier 1.
        _reset_history_sequences()

        deleted_files = 0
        skipped_files = 0
        for path_value in dict.fromkeys(import_files + model_files):
            path = _safe_media_file(path_value)
            if path is None:
                skipped_files += 1
                continue
            if path.is_file():
                path.unlink()
                deleted_files += 1

        # Remove only empty directories below the application media root.
        media_root = Path(settings.MEDIA_ROOT).resolve()
        if media_root.exists():
            directories = sorted(
                (path for path in media_root.rglob('*') if path.is_dir()),
                key=lambda path: len(path.parts),
                reverse=True,
            )
            for directory in directories:
                try:
                    directory.rmdir()
                except OSError:
                    pass

        self.stdout.write(self.style.SUCCESS(
            'Historique des campagnes supprimé : '
            f"{summary['campagnes']} campagne(s), {summary['imports']} import(s), "
            f"{summary['anomalies']} anomalie(s), {summary['notifications']} notification(s), "
            f"{summary['exemples']} exemple(s) appris et {summary['modeles']} modèle(s). "
            f'{deleted_files} fichier(s) supprimé(s).'
        ))
        if skipped_files:
            self.stdout.write(self.style.WARNING(
                f'{skipped_files} chemin(s) situé(s) hors de MEDIA_ROOT ont été ignorés par sécurité.'
            ))
