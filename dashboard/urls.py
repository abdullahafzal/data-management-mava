from django.urls import path

from . import email_views, views

app_name = 'dashboard'

urlpatterns = [
    path('', views.DashboardHomeView.as_view(), name='home'),
    path('emails/', email_views.EmailInboxView.as_view(), name='email_inbox'),
    path(
        'emails/campaign/<int:pk>/',
        email_views.EmailCampaignView.as_view(),
        name='email_campaign',
    ),
    path(
        'emails/chat/<int:pk>/',
        email_views.EmailConversationView.as_view(),
        name='email_conversation',
    ),
    path('<int:pk>/', views.WorkspaceDashboardView.as_view(), name='workspace'),
    path('<int:pk>/download/', views.WorkspaceDownloadMasterView.as_view(), name='download_master'),
    path('<int:pk>/merge/', views.WorkspaceMergeView.as_view(), name='merge'),
    path('<int:pk>/proceed/', views.WorkspaceProceedView.as_view(), name='proceed'),
    path('<int:pk>/process-next/', views.WorkspaceProcessNextView.as_view(), name='process_next'),
    path(
        '<int:pk>/actions/<int:action_pk>/undo/',
        views.WorkspaceUndoActionView.as_view(),
        name='undo_action',
    ),
    path(
        '<int:pk>/selection-ids/',
        views.WorkspaceSelectionIdsView.as_view(),
        name='selection_ids',
    ),
]
