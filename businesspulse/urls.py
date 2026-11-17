from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.http import HttpResponse
from django.urls import path
from tracker import views
from tracker import recovery_views

urlpatterns = [
    path('health/', lambda request: HttpResponse('ok', content_type='text/plain'), name='health'),
    path('admin/', admin.site.urls),
    path('accounts/login/', recovery_views.LoginView.as_view(), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('accounts/password-reset/', recovery_views.ResetRequestView.as_view(), name='password_reset'),
    path('accounts/password-reset/done/', auth_views.PasswordResetDoneView.as_view(), name='password_reset_done'),
    path('accounts/reset/<uidb64>/<token>/', recovery_views.ResetConfirmView.as_view(), name='password_reset_confirm'),
    path('accounts/reset-complete/', auth_views.PasswordResetCompleteView.as_view(), name='password_reset_complete'),
    path('accounts/password-change/', recovery_views.PasswordChangeView.as_view(), name='password_change'),
    path('accounts/password-change/done/', auth_views.PasswordChangeDoneView.as_view(), name='password_change_done'),
    path('signup/', views.signup, name='signup'),
    path('', views.dashboard, name='dashboard'),
    path('upload/', views.upload_sales, name='upload_sales'),
    path('expenses/upload/', views.upload_expenses, name='upload_expenses'),
    path('imports/', views.manage_imports, name='manage_imports'),
    path('imports/<str:dataset>/<int:batch_id>/', views.import_detail, name='import_detail'),
    path('preferences/', views.preferences, name='preferences'),
    path('export/', views.export_sales, name='export_sales'),
]
