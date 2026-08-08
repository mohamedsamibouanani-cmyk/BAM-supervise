from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('campagnes/', views.campaign_list, name='campaign_list'),
    path('campagnes/nouvelle/', views.campaign_create, name='campaign_create'),
    path('anomalies/', views.anomaly_list, name='anomaly_list'),
    path('anomalies/<int:pk>/', views.anomaly_detail, name='anomaly_detail'),
    path('anomalies/<int:pk>/valider/', views.anomaly_validate, name='anomaly_validate'),
    path('notifications/', views.notification_list, name='notification_list'),
    path('groupes/', views.group_list, name='group_list'),
    path('groupes/<int:group_id>/contacts/ajouter/', views.contact_add, name='contact_add'),
]
