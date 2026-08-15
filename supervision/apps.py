from django.apps import AppConfig


class SupervisionConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'supervision'

    def ready(self):
        # Enregistre le déclenchement du réentraînement après création d'un
        # exemple d'apprentissage validé par le superviseur.
        from . import signals  # noqa: F401
