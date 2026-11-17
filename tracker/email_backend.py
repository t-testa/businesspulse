"""Minimal HTTPS adapter: fixed origin, bounded timeout, no redirects or secret logging."""
import json
import urllib.error
import urllib.request

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend


class DeliveryError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise DeliveryError('Email delivery failed')


class ResendBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        count = 0
        for message in email_messages:
            request = urllib.request.Request('https://api.resend.com/emails', method='POST',
                headers={'Authorization': 'Bearer ' + settings.RESEND_API_KEY,
                         'Content-Type': 'application/json', 'User-Agent': 'BusinessPulse'},
                data=json.dumps({'from': message.from_email, 'to': message.to,
                                 'subject': message.subject, 'text': message.body}).encode())
            try:
                with urllib.request.build_opener(NoRedirect).open(request, timeout=settings.EMAIL_TIMEOUT) as response:
                    if response.status != 200:
                        raise DeliveryError('Email delivery failed')
                count += 1
            except (OSError, urllib.error.URLError, DeliveryError):
                if not self.fail_silently:
                    raise DeliveryError('Email delivery failed') from None
        return count
