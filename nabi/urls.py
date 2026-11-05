from django.conf import settings
from django.contrib.auth import views as auth
from django.urls import path
from django.views.static import serve
from django.views.generic import RedirectView
from office import views
from office.image_views import delivery_image, request_image
from office.instagram import media as instagram_media
from office.platform_admin import platform_admin

urlpatterns = [
    path('health',views.health),path('setup/',views.setup),
    path('access/', RedirectView.as_view(url='/login/')),
    path('administracao/', platform_admin, name='platform_admin'),
    path('login/',views.login_route),path('logout/',auth.LogoutView.as_view(next_page='/')),
    path('',views.landing),path('app/',views.dashboard),
    path('manifest.webmanifest',views.webmanifest),path('sw.js',views.service_worker),path('offline/',views.offline_page),
    path('favicon.ico',views.favicon),
    path('team/',views.team),path('tasks/',views.tasks),path('tasks/create/',views.create_task),
    path('tasks/<int:pk>/',views.task_detail),path('tasks/<int:pk>/action/',views.task_action),
    path('approvals/',views.approvals),path('approvals/<int:pk>/decision/',views.decision),
    path('deliveries/<int:pk>/edit/',views.edit_delivery),path('deliveries/<int:pk>/download/',views.download_delivery),path('deliveries/<int:pk>/art/',views.post_art),
    path('deliveries/<int:pk>/image/request/',request_image,name='request_image'),
    path('deliveries/<int:pk>/image/',delivery_image,name='delivery_image'),
    path('instagram/media/<int:pk>/<str:signature>/',instagram_media,name='instagram_media'),
    path('work/',views.work),path('work/action/',views.work_action),path('settings/',views.settings_view),path('settings/save/',views.settings_action),
    path('workspace/switch/',views.switch_workspace),path('office/pause/',views.pause),path('logo/<int:pk>/',views.logo),
    path('export/',views.export_data),path('backup/',views.backup_download),path('api/v1/dashboard',views.api_dashboard),
    path('static/<path:path>',serve,{'document_root':settings.STATIC_ROOT}),
]
