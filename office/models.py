from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models import Sum
from django.utils import timezone


AI_MODES = [("offline", "Sem IA"), ("openai", "OpenAI API"), ("claude", "Claude API"), ("chatgpt", "Plano ChatGPT")]
TASK_STATUSES = [(key, label) for key, label in [
    ("draft", "Rascunho"), ("queued", "Na fila"), ("running", "Trabalhando"),
    ("review", "Aguardando aprovação"), ("completed", "Concluída"), ("blocked", "Bloqueada"),
    ("paused", "Pausada"), ("failed", "Falhou"), ("cancelled", "Cancelada")]]


class Workspace(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="workspaces")
    name = models.CharField(max_length=120)
    segment = models.CharField(max_length=160, blank=True)
    description = models.TextField(blank=True)
    brand_color = models.CharField(max_length=7, default="#7C3AED")
    logo = models.FileField(upload_to="brands/", blank=True)
    paused = models.BooleanField(default=False)
    ai_mode = models.CharField(max_length=16, choices=AI_MODES, default="offline")
    ai_model = models.CharField(max_length=160, blank=True)
    policy = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class ScopedModel(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)

    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        for name in ("agent", "task", "delivery"):
            related_id = getattr(self, f"{name}_id", None)
            if related_id:
                related = getattr(self, name)
                if related.workspace_id != self.workspace_id:
                    raise ValidationError({name: "O registro pertence a outra empresa."})

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


class Agent(ScopedModel):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="agents")
    slug = models.SlugField(max_length=80)
    name = models.CharField(max_length=80)
    role = models.CharField(max_length=120)
    instructions = models.TextField(blank=True)
    color = models.CharField(max_length=7, default="#6C5CE7")
    avatar = models.PositiveSmallIntegerField(default=0)
    enabled = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "slug"], name="unique_agent_workspace_slug")]

    def save(self, *args, **kwargs):
        if self.pk:
            previous = Agent.objects.filter(pk=self.pk).first()
            if previous and any(getattr(previous, key) != getattr(self, key) for key in ("instructions", "role", "name")):
                self.version = previous.version + 1
                if kwargs.get("update_fields"):
                    kwargs["update_fields"] = set(kwargs["update_fields"]) | {"version"}
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Task(ScopedModel):
    agent = models.ForeignKey(Agent, on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks")
    title = models.CharField(max_length=200)
    objective = models.TextField()
    inputs = models.TextField(blank=True)
    kind = models.CharField(max_length=32, default="general")
    status = models.CharField(max_length=20, choices=TASK_STATUSES, default="draft", db_index=True)
    priority = models.CharField(max_length=10, choices=[("low", "Baixa"), ("normal", "Normal"), ("high", "Alta")], default="normal")
    due_date = models.DateField(null=True, blank=True)
    mode = models.CharField(max_length=16, choices=AI_MODES, default="offline")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["workspace", "status"])]

    def __str__(self):
        return self.title


class Run(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="runs")
    status = models.CharField(max_length=20, default="running")
    provider = models.CharField(max_length=20)
    agent_version = models.PositiveIntegerField(default=1)
    instructions_snapshot = models.TextField(blank=True)
    input_snapshot = models.JSONField(default=dict)
    output = models.TextField(blank=True)
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    reservation_usd = models.DecimalField(max_digits=14, decimal_places=6, default=0)
    usage = models.JSONField(default=dict)


class Delivery(ScopedModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="deliveries")
    title = models.CharField(max_length=200)
    body = models.TextField()
    kind = models.CharField(max_length=32, default="draft")
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        changed = False
        if self.pk:
            old = Delivery.objects.filter(pk=self.pk).first()
            changed = bool(old and any(getattr(old, key) != getattr(self, key) for key in ("title", "body", "kind")))
            if changed:
                self.version = old.version + 1
                if kwargs.get("update_fields"):
                    kwargs["update_fields"] = set(kwargs["update_fields"]) | {"version", "updated_at"}
        result = super().save(*args, **kwargs)
        if changed:
            self.approvals.filter(status__in=["pending", "approved"]).update(status="expired")
        return result


class Approval(ScopedModel):
    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name="approvals")
    payload_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=[("pending", "Pendente"), ("approved", "Aprovada"), ("rejected", "Rejeitada"), ("expired", "Expirada")], default="pending")
    expires_at = models.DateTimeField()
    decided_at = models.DateTimeField(null=True, blank=True)


class DeliveryImage(models.Model):
    delivery = models.OneToOneField(Delivery, on_delete=models.CASCADE, related_name="generated_image")
    mode = models.CharField(max_length=12, default="brand", choices=[("brand", "Arte da marca"), ("openai", "OpenAI API")])
    status = models.CharField(max_length=12, default="queued", db_index=True, choices=[
        ("queued", "Na fila"), ("running", "Gerando imagem"),
        ("ready", "Imagem pronta"), ("failed", "Imagem indisponível"),
    ])
    requested_version = models.PositiveIntegerField()
    requested_hash = models.CharField(max_length=64)
    key_fingerprint = models.CharField(max_length=64, blank=True)
    automatic = models.BooleanField(default=False)
    sha256 = models.CharField(max_length=64, blank=True)
    data = models.BinaryField(null=True, editable=False)
    error = models.CharField(max_length=300, blank=True)
    requested_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    attempted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)


class InstagramPublication(ScopedModel):
    delivery = models.OneToOneField(Delivery, on_delete=models.CASCADE, related_name="instagram_publication")
    approval = models.ForeignKey(Approval, on_delete=models.CASCADE)
    approved_hash = models.CharField(max_length=64)
    image_sha256 = models.CharField(max_length=64)
    account_id = models.CharField(max_length=32)
    account_username = models.CharField(max_length=100)
    caption = models.TextField()
    status = models.CharField(max_length=16, default="queued", choices=[
        ("queued", "Na fila"), ("uploading", "Preparando no Instagram"),
        ("waiting", "Aguardando processamento do Instagram"), ("checking", "Verificando imagem"),
        ("publishing", "Publicando"), ("published", "Publicado"), ("failed", "Falhou"),
        ("uncertain", "Resultado não confirmado"), ("cancelled", "Cancelado"),
    ])
    container_id = models.CharField(max_length=32, blank=True)
    media_id = models.CharField(max_length=32, blank=True)
    error = models.CharField(max_length=300, blank=True)
    next_check_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "next_check_at"], name="ig_publication_due_idx")]

    def clean(self):
        super().clean()
        if self.approval_id and (self.approval.delivery_id != self.delivery_id or self.approval.workspace_id != self.workspace_id):
            raise ValidationError({"approval": "A aprovação pertence a outra entrega ou empresa."})


class Event(ScopedModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, null=True, blank=True, related_name="events")
    kind = models.CharField(max_length=60)
    message = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["workspace", "created_at"])]


class Routine(ScopedModel):
    agent = models.ForeignKey(Agent, on_delete=models.SET_NULL, null=True, blank=True)
    name = models.CharField(max_length=160)
    objective = models.TextField()
    kind = models.CharField(max_length=32, default="general")
    interval_minutes = models.PositiveIntegerField(default=1440, validators=[MinValueValidator(15), MaxValueValidator(525600)])
    enabled = models.BooleanField(default=False)
    next_run = models.DateTimeField(null=True, blank=True, db_index=True)
    last_run = models.DateTimeField(null=True, blank=True)


class RoutineOccurrence(models.Model):
    routine = models.ForeignKey(Routine, on_delete=models.CASCADE, related_name="occurrences")
    scheduled_for = models.DateTimeField()
    task = models.ForeignKey(Task, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["routine", "scheduled_for"], name="unique_routine_occurrence")]

    def clean(self):
        if self.task_id and self.task.workspace_id != self.routine.workspace_id:
            raise ValidationError("Ocorrência e tarefa devem pertencer à mesma empresa.")

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


class Lead(ScopedModel):
    company = models.CharField(max_length=200)
    segment = models.CharField(max_length=120, blank=True)
    city = models.CharField(max_length=120, blank=True)
    website = models.URLField(blank=True)
    contact = models.CharField(max_length=200, blank=True)
    source_url = models.URLField(blank=True)
    notes = models.TextField(blank=True)
    stage = models.CharField(max_length=32, default="new")
    do_not_contact = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class Project(ScopedModel):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=32, default="active")
    due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class FinancialEntry(ScopedModel):
    description = models.CharField(max_length=200)
    kind = models.CharField(max_length=12, choices=[("income", "A receber"), ("expense", "A pagar")])
    amount_cents = models.PositiveBigIntegerField(validators=[MinValueValidator(1)])
    due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()
        if self.amount_cents is not None and self.amount_cents <= 0:
            raise ValidationError("Informe um valor positivo em centavos.")
        if self.kind not in ("income", "expense"):
            raise ValidationError("Tipo financeiro inválido.")
        if self.pk and self.amount_cents < self.settled_cents:
            raise ValidationError("O valor da obrigação não pode ficar abaixo do total já liquidado.")

    @property
    def settled_cents(self):
        return self.settlements.aggregate(total=Sum("amount_cents"))["total"] or 0

    @property
    def remaining_cents(self):
        return self.amount_cents - self.settled_cents

    @property
    def status(self):
        if self.remaining_cents == 0:
            return "Liquidado"
        if self.settled_cents:
            return "Parcial"
        if self.due_date and self.due_date < timezone.localdate():
            return "Vencido"
        return "Aberto"


class Settlement(models.Model):
    entry = models.ForeignKey(FinancialEntry, on_delete=models.CASCADE, related_name="settlements")
    amount_cents = models.BigIntegerField()
    paid_on = models.DateField(default=timezone.localdate)
    reason = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if not self.amount_cents:
            raise ValidationError("O movimento não pode ser zero.")
        if self.amount_cents < 0 and not self.reason.strip():
            raise ValidationError("Informe o motivo do estorno.")
        if self.pk:
            old = Settlement.objects.filter(pk=self.pk).first()
            if old and any(getattr(old, key) != getattr(self, key) for key in ("amount_cents", "entry_id", "paid_on", "reason")):
                raise ValidationError("Movimentos são imutáveis. Registre um estorno.")
        total = self.entry.settlements.exclude(pk=self.pk).aggregate(total=Sum("amount_cents"))["total"] or 0
        if not 0 <= total + self.amount_cents <= self.entry.amount_cents:
            raise ValidationError("O movimento ultrapassa o saldo da obrigação.")

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


class SystemState(models.Model):
    paused = models.BooleanField(default=False)
    worker_heartbeat = models.DateTimeField(null=True, blank=True)


class WorkerLease(models.Model):
    owner = models.CharField(max_length=120)
    heartbeat = models.DateTimeField(default=timezone.now)
