from django import template

from supervision.services.campaign_forecasting import campaign_forecast


register = template.Library()


@register.inclusion_tag('supervision/includes/forecast_dashboard_card.html')
def forecast_dashboard_card():
    return {'forecast': campaign_forecast()}
