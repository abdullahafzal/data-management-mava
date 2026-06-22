from django.urls import path

from . import views

app_name = 'automation'

urlpatterns = [
    path('', views.AutomationDashboardView.as_view(), name='dashboard'),
    path('spy-dialer/', views.SpyDialerRunView.as_view(), name='spy_dialer_run'),
    path('spy-dialer/<int:pk>/configure/', views.SpyDialerConfigureView.as_view(), name='spy_dialer_configure'),
    path('icm/', views.IcmRunView.as_view(), name='icm_run'),
    path('icm/<int:pk>/configure/', views.IcmConfigureView.as_view(), name='icm_configure'),
    path('runs/<int:pk>/', views.AutomationRunDetailView.as_view(), name='run_detail'),
    path('runs/<int:pk>/status/', views.AutomationRunStatusView.as_view(), name='run_status'),
    path(
        'runs/<int:pk>/download/',
        views.AutomationDownloadOutputView.as_view(),
        name='download_output',
    ),
    path('runs/<int:pk>/<str:action>/', views.AutomationRunControlView.as_view(), name='run_control'),
]
