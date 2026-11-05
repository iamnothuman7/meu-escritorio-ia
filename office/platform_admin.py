"""Account administration, separate from the operational workspace."""

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from . import services


class CreateOperationalUserForm(forms.Form):
    # The standard Django login form limits its identifier to 150 characters.
    email = forms.EmailField(label="E-mail", max_length=150)
    first_name = forms.CharField(label="Nome (opcional)", max_length=150, required=False)
    password = forms.CharField(
        label="Senha", min_length=10, max_length=256, strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Use pelo menos 10 caracteres.",
    )
    password_confirmation = forms.CharField(
        label="Confirme a senha", max_length=256, strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def clean_email(self):
        user_model = get_user_model()
        email = user_model.normalize_username(self.cleaned_data["email"]).lower()
        email = self.fields["email"].clean(email)
        if user_model.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).exists():
            raise ValidationError("Já existe uma conta com este e-mail.")
        return email

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get("email")
        password = cleaned.get("password")
        confirmation = cleaned.get("password_confirmation")
        if password is not None and confirmation is not None and password != confirmation:
            self.add_error("password_confirmation", "As senhas não coincidem.")
        if email and password:
            candidate = get_user_model()(username=email, email=email, first_name=cleaned.get("first_name", ""))
            try:
                validate_password(password, candidate)
            except ValidationError as error:
                self.add_error("password", error)
        return cleaned

    def save(self):
        email = self.cleaned_data["email"]
        return get_user_model().objects.create_user(
            username=email,
            email=email,
            first_name=self.cleaned_data["first_name"],
            password=self.cleaned_data["password"],
            is_staff=False,
            is_superuser=False,
        )


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
@csrf_protect
@sensitive_post_parameters("password", "password_confirmation")
def platform_admin(request):
    user = request.user
    if not (user.is_active and user.is_staff and user.is_superuser):
        return render(request, '403.html', status=403)

    form = CreateOperationalUserForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                account = form.save()
                services.bootstrap_workspace(account)
        except IntegrityError:
            # The normalized login identifier is unique even if two submissions race.
            if not get_user_model().objects.filter(username=form.cleaned_data["email"]).exists():
                raise
            form.add_error("email", "Já existe uma conta com este e-mail.")
        else:
            messages.success(request, "Conta operacional criada.", extra_tags="platform-account")
            return redirect("platform_admin")

    users = (get_user_model().objects.filter(is_superuser=False)
             .order_by("email", "username", "pk")
             .values("email", "username", "first_name", "is_active"))
    accounts = Paginator(users, 30).get_page(request.GET.get("page"))
    return render(request, "office/platform_admin.html", {"form": form, "accounts": accounts})
