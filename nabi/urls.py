from django.conf import settings
from django.contrib.auth import views as auth
from django.urls import path
from django.views.static import serve
from office import views

urlpatterns = [
    path('health',views.health),path('setup/',views.setup),
    path('login/',views.login_route),path('logout/',auth.LogoutView.as_view(next_page='/login/')),
    path('',views.dashboard),path('team/',views.team),path('tasks/',views.tasks),path('tasks/create/',views.create_task),
    path('tasks/<int:pk>/',views.task_detail),path('tasks/<int:pk>/action/',views.task_action),
    path('approvals/',views.approvals),path('approvals/<int:pk>/decision/',views.decision),
    path('deliveries/<int:pk>/edit/',views.edit_delivery),path('deliveries/<int:pk>/download/',views.download_delivery),path('deliveries/<int:pk>/art/',views.post_art),
    path('work/',views.work),path('work/action/',views.work_action),path('settings/',views.settings_view),path('settings/save/',views.settings_action),
    path('workspace/switch/',views.switch_workspace),path('office/pause/',views.pause),path('logo/<int:pk>/',views.logo),
    path('export/',views.export_data),path('backup/',views.backup_download),path('api/v1/dashboard',views.api_dashboard),
    path('static/<path:path>',serve,{'document_root':settings.STATIC_ROOT}),
]
