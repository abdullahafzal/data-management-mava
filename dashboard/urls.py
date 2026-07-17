from django.urls import path

from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.DashboardHomeView.as_view(), name='home'),
    path('<int:pk>/', views.WorkspaceDashboardView.as_view(), name='workspace'),
    path('<int:pk>/download/', views.WorkspaceDownloadMasterView.as_view(), name='download_master'),
    path('<int:pk>/merge/', views.WorkspaceMergeView.as_view(), name='merge'),
    path('<int:pk>/proceed/', views.WorkspaceProceedView.as_view(), name='proceed'),
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
