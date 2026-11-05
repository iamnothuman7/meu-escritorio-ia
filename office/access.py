from datetime import timedelta
import re

from django.contrib.auth.forms import SetPasswordForm
from django.db import transaction
from django.db.models import F
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from .models import OwnerAccessCode


MAX_ATTEMPTS = 6
ACCESS_CODE_LIFETIME = timedelta(hours=24)
INVALID_ACCESS = "Não foi possível confirmar o código. Confira o e-mail e o código ou solicite um novo."


def access_code_digest(user_id, code):
    return salted_hmac("office.owner-access.v1", f"{user_id}:{code}", algorithm="sha256").hexdigest()


def _usable_codes(email):
    return OwnerAccessCode.objects.filter(
        user__email__iexact=email,
        user__is_staff=True,
        user__is_active=True,
        expires_at__gt=timezone.now(),
        attempts__lt=MAX_ATTEMPTS,
    )


def _claim_query(email, record):
    return _usable_codes(email).filter(pk=record.pk, digest=record.digest, expires_at=record.expires_at)


def _redeem(request, email):
    code = request.POST.get("code", "").strip()
    if not email or not re.fullmatch(r"[0-9]{8}", code):
        return INVALID_ACCESS
    try:
        record = _usable_codes(email).select_related("user").get()
    except (OwnerAccessCode.DoesNotExist, OwnerAccessCode.MultipleObjectsReturned):
        return INVALID_ACCESS

    if not constant_time_compare(record.digest, access_code_digest(record.user_id, code)):
        _claim_query(email, record).update(attempts=F("attempts") + 1)
        return INVALID_ACCESS

    form = SetPasswordForm(record.user, request.POST)
    if not form.is_valid():
        return " ".join(message for errors in form.errors.values() for message in errors)
    user = form.save(commit=False)

    with transaction.atomic():
        # The conditional write claims this exact code even on SQLite. Password
        # hashing runs before the write so it does not hold the database lock.
        if not _claim_query(email, record).update(attempts=F("attempts") + 1):
            return INVALID_ACCESS
        user.save(update_fields=["password"])
        OwnerAccessCode.objects.filter(pk=record.pk).delete()
    return ""


@sensitive_post_parameters("email", "code", "new_password1", "new_password2")
@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def owner_access(request):
    email = ""
    error = ""
    if request.method == "POST":
        email = request.POST.get("email", "").strip()[:254]
        error = _redeem(request, email)
        if not error:
            return redirect("/login/")
    return render(request, "office/access.html", {"email": email, "error": error})
