from django.urls import path

from . import views

app_name = 'pipeline'

urlpatterns = [
    path('api/categories/suggest/', views.CategorySuggestView.as_view(), name='category_suggest'),
    path('api/categories/record/', views.CategoryRecordView.as_view(), name='category_record'),
    path('api/locations/suggest/', views.LocationSuggestView.as_view(), name='location_suggest'),
    path('api/locations/record/', views.LocationRecordView.as_view(), name='location_record'),
    path('', views.CampaignListView.as_view(), name='campaign_list'),
    path('history/', views.ImportHistoryView.as_view(), name='import_history'),
    path('campaigns/new/', views.CampaignCreateView.as_view(), name='campaign_create'),
    path('campaigns/<int:pk>/', views.CampaignDetailView.as_view(), name='campaign_detail'),
    path(
        'campaigns/<int:campaign_pk>/upload/',
        views.DataImportUploadView.as_view(),
        name='upload',
    ),
    path(
        'imports/<int:import_pk>/confirm/',
        views.DataImportConfirmView.as_view(),
        name='upload_confirm',
    ),
    path(
        'imports/<int:import_pk>/download/original/',
        views.DownloadOriginalView.as_view(),
        name='download_original',
    ),
    path(
        'imports/<int:import_pk>/download/diana/',
        views.DownloadDianaQueueView.as_view(),
        name='download_diana',
    ),
    path(
        'imports/<int:import_pk>/columns/',
        views.SelectColumnsView.as_view(),
        name='select_columns',
    ),
    path(
        'imports/<int:import_pk>/results/',
        views.AutomaticResultsView.as_view(),
        name='automatic_results',
    ),
    path(
        'imports/<int:import_pk>/',
        views.ImportDetailView.as_view(),
        name='import_detail',
    ),
    path(
        'imports/<int:import_pk>/download/cleaned/',
        views.DownloadCleanedView.as_view(),
        name='download_cleaned',
    ),
    path(
        'imports/<int:import_pk>/verification/',
        views.VerificationUploadView.as_view(),
        name='verification_upload',
    ),
    path(
        'exports/<int:export_pk>/download/',
        views.DownloadVerificationExportView.as_view(),
        name='download_verification_export',
    ),
    path(
        'imports/<int:import_pk>/download/verification-all.zip',
        views.DownloadVerificationZipView.as_view(),
        name='download_verification_zip',
    ),
]
