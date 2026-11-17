from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model, views as auth_views
from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters

from .recovery import allow_password_attempt, queue_reset


class ResetRequestForm(forms.Form):
    email = forms.EmailField(max_length=254, label='Email address')


@method_decorator(sensitive_post_parameters('email'), name='dispatch')
@method_decorator(never_cache, name='dispatch')
class ResetRequestView(auth_views.PasswordResetView):
    form_class = ResetRequestForm

    def form_valid(self, form):
        queue_reset(form.cleaned_data['email'], self.request.META.get('REMOTE_ADDR', 'unknown'))
        # Same redirect for unknown, inactive, ambiguous, disabled and throttled requests.
        return HttpResponseRedirect(self.get_success_url())


@method_decorator(never_cache, name='dispatch')
@method_decorator(sensitive_post_parameters(), name='dispatch')
class ResetConfirmView(auth_views.PasswordResetConfirmView):
    @transaction.atomic
    def dispatch(self, request, *args, **kwargs):
        if request.method == 'POST' and not allow_password_attempt(request.META.get('REMOTE_ADDR', 'unknown')):
            return HttpResponse('Too many attempts. Please try again later.', status=429)
        return super().dispatch(request, *args, **kwargs)

    def get_user(self, uidb64):
        if not settings.ACCOUNT_RECOVERY_ENABLED:
            return None
        user = super().get_user(uidb64)
        if user is not None and self.request.method == 'POST':
            user = get_user_model().objects.select_for_update().get(pk=user.pk)
        return user if user is not None and user.is_active and user.has_usable_password() else None


@method_decorator(never_cache, name='dispatch')
@method_decorator(sensitive_post_parameters(), name='dispatch')
class PasswordChangeView(auth_views.PasswordChangeView):
    @transaction.atomic
    def dispatch(self, request, *args, **kwargs):
        if request.method == 'POST' and request.user.is_authenticated:
            if not allow_password_attempt(request.META.get('REMOTE_ADDR', 'unknown')):
                return HttpResponse('Too many attempts. Please try again later.', status=429)
            request.user = get_user_model().objects.select_for_update().get(pk=request.user.pk)
        return super().dispatch(request, *args, **kwargs)


class LoginView(auth_views.LoginView):
    template_name = 'registration/login.html'

    def post(self, request, *args, **kwargs):
        if not allow_password_attempt(request.META.get('REMOTE_ADDR', 'unknown')):
            return HttpResponse('Too many attempts. Please try again later.', status=429)
        return super().post(request, *args, **kwargs)
