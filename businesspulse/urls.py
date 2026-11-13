from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.http import HttpResponse
from django.urls import path
from tracker import views

urlpatterns = [
    path('health/', lambda request: HttpResponse('ok', content_type='text/plain'), name='health'),
    path('admin/', admin.site.urls),
    path('accounts/login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('signup/', views.signup, name='signup'),
    path('', views.dashboard, name='dashboard'),
    path('upload/', views.upload_sales, name='upload_sales'),
    path('preferences/', views.preferences, name='preferences'),
    path('export/', views.export_sales, name='export_sales'),
]
