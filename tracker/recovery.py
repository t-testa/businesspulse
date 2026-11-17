"""Shared abuse controls and deferred reset delivery without an external broker."""
from datetime import datetime, timedelta, timezone as dt_timezone
from urllib.parse import urlsplit
import logging
import unicodedata

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm
from django.core.mail import EmailMultiAlternatives
from django.db import connection, transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import RecoveryLimit, RecoveryRequest

logger = logging.getLogger('tracker')


def normalize_email(email):
    return unicodedata.normalize('NFKC', email.strip()).casefold()


def digest(value):
    return salted_hmac('account-recovery-limits', value, algorithm='sha256').hexdigest()


@transaction.atomic
def consume_limits(specs):
    """Same global-first order for every caller prevents lock-order deadlocks."""
    now = timezone.now()
    stamp = int(now.timestamp())
    for scope, identity, seconds, maximum in specs:
        window = stamp // seconds
        key = f'{scope}:{window}:{digest(identity)}'
        expires = datetime.fromtimestamp((window + 1) * seconds, tz=dt_timezone.utc)
        RecoveryLimit.objects.get_or_create(key=key, defaults={'expires_at': expires})
        bucket = RecoveryLimit.objects.select_for_update().get(pk=key)
        if bucket.count >= maximum:
            return False
        bucket.count += 1
        bucket.save(update_fields=['count'])
    return True


@transaction.atomic
def queue_reset(email, remote_addr):
    if not settings.ACCOUNT_RECOVERY_ENABLED:
        return
    email = normalize_email(email)
    # Counts all requests equally, including nonexistent addresses and later denials.
    allowed = consume_limits([
        ('reset-month', 'all', 30 * 86400, 2000),
        ('reset-day', 'all', 86400, 90),
        ('reset-ip', remote_addr, 3600, 10),
        ('reset-email-hour', email, 3600, 3),
        ('reset-email-day', email, 86400, 10),
    ])
    if allowed:
        RecoveryRequest.objects.create(email=email, expires_at=timezone.now() + timedelta(minutes=15))


def allow_password_attempt(remote_addr):
    return consume_limits([('password-global', 'all', 3600, 1200),
                           ('password-ip', remote_addr, 600, 30)])


class DeliveryForm(PasswordResetForm):
    def get_users(self, email):
        # Default User.email is not unique. Never reset several accounts with one request.
        matches = get_user_model().objects.filter(email__iexact=email)
        if matches.count() != 1:
            return []
        return super().get_users(email)

    def send_mail(self, subject_template_name, email_template_name, context, from_email,
                  to_email, html_email_template_name=None):
        subject = ''.join(render_to_string(subject_template_name, context).splitlines())
        body = render_to_string(email_template_name, context)
        EmailMultiAlternatives(subject, body, from_email, [to_email]).send()


def deliver_one():
    """At-most-once dispatch. Crash/timeout can lose mail; owner can request again.

    Claim commits BEFORE contacting the provider, keeping the public queue's timing
    independent of account existence or mail latency. No reset token is stored.
    """
    if not settings.ACCOUNT_RECOVERY_ENABLED:
        return False
    with transaction.atomic():
        queue = RecoveryRequest.objects.filter(expires_at__gt=timezone.now())
        queue = queue.select_for_update(skip_locked=True) if connection.vendor == 'postgresql' else queue
        job = queue.first()
        if job is None:
            return False
        email = job.email
        job.delete()
    form = DeliveryForm({'email': email})
    if form.is_valid():
        origin = urlsplit(settings.RECOVERY_ORIGIN)
        try:
            form.save(domain_override=origin.netloc, use_https=origin.scheme == 'https',
                      from_email=settings.DEFAULT_FROM_EMAIL,
                      email_template_name='registration/password_reset_email.txt',
                      subject_template_name='registration/password_reset_subject.txt')
        except Exception:
            # Do not log provider exceptions, recipients, passwords, bodies or tokens.
            logger.warning('Password recovery delivery failed; request a new link after checking provider configuration.')
    return True


def cleanup_recovery():
    RecoveryRequest.objects.filter(expires_at__lte=timezone.now()).delete()
    RecoveryLimit.objects.filter(expires_at__lte=timezone.now()).delete()
