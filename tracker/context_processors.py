from django.conf import settings

def account_options(request):
    return {'public_signup_enabled': settings.PUBLIC_SIGNUP_ENABLED}
