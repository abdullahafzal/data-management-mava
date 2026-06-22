from django.urls import path

from . import views

app_name = 'registry'

urlpatterns = [
    path('', views.RegistryDashboardView.as_view(), name='dashboard'),
    path('upload/', views.RegistryUploadView.as_view(), name='upload'),
    path('diff/<int:pk>/', views.RegistryDiffDetailView.as_view(), name='diff_detail'),
    path(
        'diff/<int:pk>/approve/',
        views.RegistryApproveBaselineView.as_view(),
        name='approve_baseline',
    ),
    path(
        'diff/<int:pk>/rerun-ai/',
        views.RegistryRerunAIView.as_view(),
        name='rerun_ai',
    ),
    path(
        'diff/<int:pk>/download/<str:change_type>/',
        views.RegistryDownloadChangesView.as_view(),
        name='download_changes',
    ),
]
