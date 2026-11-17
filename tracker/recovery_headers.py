"""Allow same-origin referrers only on named forms without reset credentials.

The global no-referrer policy still covers all token URLs, including errors.
Django CSRF needs origin/referrer information for ordinary form submissions.
"""
class FormReferrerPolicyMiddleware:
    form_routes = frozenset({
        'login', 'signup', 'dashboard', 'upload_sales', 'upload_expenses',
        'manage_imports', 'import_detail', 'preferences', 'password_reset',
        'password_reset_done', 'password_reset_complete', 'password_change',
        'password_change_done', 'auth_user_add', 'auth_user_change',
        'auth_user_password_change', 'auth_group_add', 'auth_group_change',
    })

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        match = request.resolver_match
        if match and (match.url_name in self.form_routes or (
                match.url_name == 'password_reset_confirm'
                and match.kwargs.get('token') == 'set-password')):
            response['Referrer-Policy'] = 'same-origin'
        return response
