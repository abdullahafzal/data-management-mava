from django.db import models


class LeadWorkspace(models.Model):
    """Named master lead DB (campaign). First upload = match base."""

    name = models.CharField(max_length=255)
    master_file = models.FileField(upload_to='lead_db/master/%Y/%m/', blank=True)
    # Ordered column headers from the merged file (table uses these + Source + Status).
    columns = models.JSONField(default=list, blank=True)
    # Cached filter dropdowns: [{"column": "Facility City", "values": ["Bronx", ...]}, ...]
    filter_fields = models.JSONField(default=list, blank=True)
    row_count = models.PositiveIntegerField(default=0)
    enriched_count = models.PositiveIntegerField(default=0)
    pending_count = models.PositiveIntegerField(default=0)
    proceeded_count = models.PositiveIntegerField(default=0)
    in_campaign_count = models.PositiveIntegerField(default=0)
    last_merge_report = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.name

    @property
    def table_columns(self) -> list[str]:
        """File columns shown in the grid (Source / Status are separate)."""
        return [c for c in (self.columns or []) if c]


class LeadSourceFile(models.Model):
    class SourceKind(models.TextChoices):
        DMV = 'dmv', 'DMV'
        OUTSCRAPER = 'outscraper', 'Outscraper'
        BEENVERIFIED = 'beenverified', 'BeenVerified'
        OTHER = 'other', 'Other'

    workspace = models.ForeignKey(
        LeadWorkspace, on_delete=models.CASCADE, related_name='source_files'
    )
    file = models.FileField(upload_to='lead_db/sources/%Y/%m/')
    original_filename = models.CharField(max_length=255)
    source_kind = models.CharField(
        max_length=32, choices=SourceKind.choices, default=SourceKind.OTHER
    )
    sort_order = models.PositiveIntegerField(default=0)
    row_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f'{self.get_source_kind_display()} — {self.original_filename}'


class LeadRecord(models.Model):
    class Status(models.TextChoices):
        RESEARCHED = 'researched', 'Researched'
        NEEDS_OWNER = 'needs_owner', 'Needs owner'
        IN_CAMPAIGN = 'in_campaign', 'In campaign'
        DUPLICATE = 'duplicate', 'Duplicate'

    class ProcessStatus(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PROCEEDED = 'proceeded', 'Proceeded'

    # Verification + campaign gates (Source is separate). Meeting:
    # MillionVerifier, Email Verifier, Phone Verifier, then Smartlead / SimpleTexting.
    DESTINATION_FIELDS = (
        ('millionverifier', 'status_millionverifier', 'MillionVerifier'),
        ('email_verifier', 'status_email_verifier', 'Email Verifier'),
        ('xverify', 'status_xverify', 'Phone Verifier'),  # XVerify / phone
        ('smartlead', 'status_smartlead', 'Smartlead'),
        ('simpletexting', 'status_simpletexting', 'SimpleTexting'),
    )

    workspace = models.ForeignKey(
        LeadWorkspace, on_delete=models.CASCADE, related_name='records'
    )
    public_id = models.CharField(max_length=64, db_index=True)
    # Full row keyed by original file column names.
    data = models.JSONField(default=dict, blank=True)
    # Lowercased concatenation of all cell values for free-text search.
    search_text = models.TextField(blank=True)
    sources = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=32, choices=Status.choices, default=Status.NEEDS_OWNER, db_index=True
    )
    # Overall: pending if any destination is pending, else proceeded.
    process_status = models.CharField(
        max_length=32,
        choices=ProcessStatus.choices,
        default=ProcessStatus.PENDING,
        db_index=True,
    )
    status_millionverifier = models.CharField(
        max_length=32, choices=ProcessStatus.choices,
        default=ProcessStatus.PENDING, db_index=True,
    )
    status_email_verifier = models.CharField(
        max_length=32, choices=ProcessStatus.choices,
        default=ProcessStatus.PENDING, db_index=True,
    )
    status_smartlead = models.CharField(
        max_length=32, choices=ProcessStatus.choices,
        default=ProcessStatus.PENDING, db_index=True,
    )
    status_xverify = models.CharField(
        max_length=32, choices=ProcessStatus.choices,
        default=ProcessStatus.PENDING, db_index=True,
    )
    status_simpletexting = models.CharField(
        max_length=32, choices=ProcessStatus.choices,
        default=ProcessStatus.PENDING, db_index=True,
    )
    # Per-email / per-phone verification results (survives merge when address matches).
    # {"emails": {"a@b.com": {"millionverifier": "good", "checked_at": "..."}},
    #  "phones": {"9146825000": {"xverify": "valid", "checked_at": "..."}}}
    verification_data = models.JSONField(default=dict, blank=True)
    is_enriched = models.BooleanField(default=False, db_index=True)
    match_key_used = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['id']
        indexes = [
            models.Index(fields=['workspace', 'status']),
            models.Index(fields=['workspace', 'process_status']),
            models.Index(fields=['workspace', 'is_enriched']),
        ]

    def __str__(self):
        return self.public_id

    def cell(self, column: str) -> str:
        val = (self.data or {}).get(column, '')
        if val is None:
            return ''
        return str(val).strip()

    def destination_statuses(self) -> dict[str, str]:
        return {
            key: getattr(self, field)
            for key, field, _label in self.DESTINATION_FIELDS
        }

    def sync_overall_process_status(self) -> str:
        statuses = [
            getattr(self, field)
            for _key, field, _label in self.DESTINATION_FIELDS
        ]
        overall = (
            self.ProcessStatus.PROCEEDED
            if statuses and all(s == self.ProcessStatus.PROCEEDED for s in statuses)
            else self.ProcessStatus.PENDING
        )
        self.process_status = overall
        return overall


class LeadWorkspaceAction(models.Model):
    """Audit log of campaign actions (Proceed, Undo, merges) with undo support."""

    class ActionType(models.TextChoices):
        PROCEED = 'proceed', 'Proceed'
        UNDO_PROCEED = 'undo_proceed', 'Undo proceed'
        MERGE = 'merge', 'Merge upload'

    workspace = models.ForeignKey(
        LeadWorkspace, on_delete=models.CASCADE, related_name='actions'
    )
    action_type = models.CharField(max_length=32, choices=ActionType.choices, db_index=True)
    summary = models.CharField(max_length=512, blank=True)
    record_count = models.PositiveIntegerField(default=0)
    # Rows affected — public_ids survive rematch better than PKs.
    record_ids = models.JSONField(default=list, blank=True)
    public_ids = models.JSONField(default=list, blank=True)
    meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    undone_at = models.DateTimeField(null=True, blank=True)
    reverses = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reversed_by',
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_action_type_display()} — {self.record_count} ({self.workspace_id})'

    @property
    def can_undo(self) -> bool:
        return (
            self.action_type == self.ActionType.PROCEED
            and self.undone_at is None
            and self.record_count > 0
        )


# ---------------------------------------------------------------------------
# Smartlead email history (webhook → dashboard). Phase 1: store only.
# Phase 2 (later): async sync of messages to GHL Conversations API.
# ---------------------------------------------------------------------------


class SmartleadCampaign(models.Model):
    smartlead_campaign_id = models.CharField(max_length=64, unique=True, db_index=True)
    name = models.CharField(max_length=512, blank=True)
    status = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.name or self.smartlead_campaign_id


class SmartleadLead(models.Model):
    """One prospect email in a Smartlead campaign. Created if webhook email is new."""

    campaign = models.ForeignKey(
        SmartleadCampaign, on_delete=models.CASCADE, related_name='leads'
    )
    smartlead_lead_id = models.CharField(max_length=64, blank=True, db_index=True)
    smartlead_lead_map_id = models.CharField(max_length=64, blank=True, db_index=True)
    email = models.EmailField(db_index=True)
    first_name = models.CharField(max_length=255, blank=True)
    last_name = models.CharField(max_length=255, blank=True)
    # Optional link into master lead DB when the same email already exists.
    lead_record = models.ForeignKey(
        LeadRecord,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='smartlead_leads',
    )
    ghl_contact_id = models.CharField(max_length=128, blank=True, db_index=True)
    status = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(
                fields=['campaign', 'email'],
                name='uq_smartlead_lead_campaign_email',
            ),
        ]
        indexes = [
            models.Index(fields=['campaign', 'smartlead_lead_id']),
        ]

    def __str__(self):
        return self.email


class SmartleadMessage(models.Model):
    class EventType(models.TextChoices):
        EMAIL_SENT = 'EMAIL_SENT', 'Email sent'
        EMAIL_REPLY = 'EMAIL_REPLY', 'Email reply'

    class Direction(models.TextChoices):
        OUTBOUND = 'outbound', 'Outbound'
        INBOUND = 'inbound', 'Inbound'

    class GhlSyncStatus(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SYNCED = 'synced', 'Synced'
        FAILED = 'failed', 'Failed'
        SKIPPED = 'skipped', 'Skipped'

    lead = models.ForeignKey(
        SmartleadLead, on_delete=models.CASCADE, related_name='messages'
    )
    campaign = models.ForeignKey(
        SmartleadCampaign, on_delete=models.CASCADE, related_name='messages'
    )
    # Idempotency key — unique so Smartlead retries never duplicate rows.
    smartlead_message_id = models.CharField(max_length=512, unique=True, db_index=True)
    event_type = models.CharField(max_length=32, choices=EventType.choices, db_index=True)
    direction = models.CharField(max_length=16, choices=Direction.choices, db_index=True)
    from_email = models.CharField(max_length=512, blank=True)
    to_email = models.CharField(max_length=512, blank=True)
    subject = models.CharField(max_length=1024, blank=True)
    body_text = models.TextField(blank=True)
    body_html = models.TextField(blank=True)
    sequence_number = models.PositiveIntegerField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True, db_index=True)
    received_at = models.DateTimeField(null=True, blank=True, db_index=True)
    in_reply_to = models.CharField(max_length=512, blank=True)
    email_references = models.TextField(blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    # Phase 2 fields (unused until GHL sync is built).
    ghl_sync_status = models.CharField(
        max_length=16,
        choices=GhlSyncStatus.choices,
        default=GhlSyncStatus.SKIPPED,
        db_index=True,
    )
    ghl_sync_attempts = models.PositiveIntegerField(default=0)
    ghl_last_sync_error = models.TextField(blank=True)
    ghl_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sent_at', 'received_at', 'id']
        indexes = [
            models.Index(fields=['lead', 'event_type']),
            models.Index(fields=['campaign', 'event_type']),
        ]

    def __str__(self):
        return f'{self.event_type} — {self.smartlead_message_id[:40]}'


class SmartleadWebhookLog(models.Model):
    class Status(models.TextChoices):
        RECEIVED = 'received', 'Received'
        PROCESSED = 'processed', 'Processed'
        DUPLICATE = 'duplicate', 'Duplicate'
        IGNORED = 'ignored', 'Ignored'
        ERROR = 'error', 'Error'

    event_type = models.CharField(max_length=64, blank=True, db_index=True)
    smartlead_message_id = models.CharField(max_length=512, blank=True, db_index=True)
    campaign_id = models.CharField(max_length=64, blank=True)
    lead_id = models.CharField(max_length=64, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.RECEIVED, db_index=True
    )
    error = models.TextField(blank=True)
    message = models.ForeignKey(
        SmartleadMessage,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='webhook_logs',
    )
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-received_at']

    def __str__(self):
        return f'{self.event_type or "?"} — {self.status}'
