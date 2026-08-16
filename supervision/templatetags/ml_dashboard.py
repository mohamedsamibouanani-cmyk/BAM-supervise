from django import template

from supervision.services.campaign_forecasting import campaign_forecast


register = template.Library()


@register.inclusion_tag('supervision/includes/forecast_dashboard_card.html')
def ml_dashboard_card():
    """Compatibilité du tableau de bord : le cœur ML est désormais prévisionnel."""
    return {'forecast': campaign_forecast()}
