"""Database tables represented by SQLAlchemy ORM models. One class maps to one table."""
from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db

# ---- Fixed lists used across the whole project -------------------------------
ROLES = ("policyholder", "hospital_desk", "claims_officer", "administrator")
ROLE_LABELS = {
    "policyholder": "Policyholder",
    "hospital_desk": "Hospital Desk",
    "claims_officer": "Claims Officer",
    "administrator": "Administrator",
}
CLAIM_TYPES = ("cashless", "reimbursement")
STATUSES = ("draft", "submitted", "in_review", "more_info", "approved", "rejected", "paid")
DOCUMENT_TYPES = ("Hospital Bill", "Discharge Summary", "Prescription", "ID Proof")
CHECKLIST_STATUSES = ("missing", "provided", "verified")


def now():
    """Handle the now operation."""
    return datetime.now().replace(microsecond=0)


# ---- 1. hospitals ------------------------------------------------------------
class Hospital(db.Model):
    """Represent the hospital data model."""
    __tablename__ = "hospitals"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    city = db.Column(db.String(80), nullable=False)
    network_status = db.Column(db.String(20), nullable=False, default="network")  # network / non_network

    @property
    def is_network(self):
        """Handle the is network operation."""
        return self.network_status == "network"


# ---- 2. users ----------------------------------------------------------------
class User(db.Model):
    """Represent the user data model."""
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(30), nullable=False, index=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=True)

    hospital = db.relationship("Hospital")

    def set_password(self, password):
        """Handle the set password operation."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Handle the check password operation."""
        return check_password_hash(self.password_hash, password)


# ---- 3. plans ----------------------------------------------------------------
class Plan(db.Model):
    """Represent the plan data model."""
    __tablename__ = "plans"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    covered_treatment_types = db.Column(db.Text, nullable=False)  # "Surgery,Cardiology,..."
    annual_limit = db.Column(db.Numeric(12, 2), nullable=False)

    @property
    def policy_amount_limit(self):
        return self.annual_limit

    @policy_amount_limit.setter
    def policy_amount_limit(self, value):
        self.annual_limit = value
    deductible = db.Column(db.Numeric(12, 2), nullable=False)
    copay_percent = db.Column(db.Numeric(5, 2), nullable=False)

    @property
    def treatments(self):
        """Handle the treatments operation."""
        return [t.strip() for t in self.covered_treatment_types.split(",") if t.strip()]

    def covers(self, treatment_type):
        """Handle the covers operation."""
        return treatment_type in self.treatments


# ---- 4. policies -------------------------------------------------------------
class Policy(db.Model):
    """Represent the policy data model."""
    __tablename__ = "policies"
    id = db.Column(db.Integer, primary_key=True)
    holder_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    plan_id = db.Column(db.Integer, db.ForeignKey("plans.id"), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    policy_amount_limit = db.Column(db.Numeric(12, 2), nullable=True)
    insurer_name = db.Column(db.String(120), nullable=False, default="Demo Insurance Company")
    premium_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    waiting_period = db.Column(db.Text, nullable=False, default="")
    exclusions = db.Column(db.Text, nullable=False, default="")
    cashless_details = db.Column(db.Text, nullable=False, default="")
    terms_conditions = db.Column(db.Text, nullable=False, default="")

    holder = db.relationship("User")
    plan = db.relationship("Plan")
    members = db.relationship("Member", back_populates="policy", order_by="Member.id")

    @property
    def code(self):
        """Handle the code operation."""
        return f"POL{self.id:03d}"

    @property
    def amount_limit(self):
        return self.policy_amount_limit if self.policy_amount_limit is not None else self.plan.policy_amount_limit

    @property
    def consumed_coverage(self):
        from sqlalchemy import func
        total = (db.session.query(func.coalesce(func.sum(Claim.eligible_amount), 0))
                 .filter(Claim.policy_id == self.id, Claim.status.in_(("approved", "paid"))).scalar())
        return total or 0

    @property
    def remaining_coverage(self):
        from decimal import Decimal
        return max(Decimal("0.00"), Decimal(str(self.amount_limit or 0)) - Decimal(str(self.consumed_coverage or 0)))

    def covers_date(self, day):
        """Handle the covers date operation."""
        return self.start_date <= day <= self.end_date  # inclusive


# ---- 5. members --------------------------------------------------------------
class Member(db.Model):
    """Represent the member data model."""
    __tablename__ = "members"
    id = db.Column(db.Integer, primary_key=True)
    policy_id = db.Column(db.Integer, db.ForeignKey("policies.id"), nullable=False, index=True)
    display_name = db.Column(db.String(100), nullable=False)
    date_of_birth = db.Column(db.Date, nullable=True)
    gender = db.Column(db.String(30), nullable=True)
    is_covered = db.Column(db.Boolean, nullable=False, default=True)
    relation = db.Column("relationship", db.String(40), nullable=False)  # column is called "relationship"

    # (id, policy_id) must be unique so that claims can point to BOTH values.
    __table_args__ = (db.UniqueConstraint("id", "policy_id", name="uq_member_id_policy"),)

    policy = db.relationship("Policy", back_populates="members")


# ---- 6. claims ---------------------------------------------------------------
class Claim(db.Model):
    """Represent the claim data model."""
    __tablename__ = "claims"
    id = db.Column(db.Integer, primary_key=True)
    policy_id = db.Column(db.Integer, db.ForeignKey("policies.id"), nullable=False, index=True)
    member_id = db.Column(db.Integer, db.ForeignKey("members.id"), nullable=False, index=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False, index=True)
    # Extra column (not in the original list): who created the claim.
    # Needed so only the creator can edit / submit / respond.
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    claim_type = db.Column(db.String(20), nullable=False, index=True)
    treatment_type = db.Column(db.String(80), nullable=False)
    admission_date = db.Column(db.Date, nullable=False)
    discharge_date = db.Column(db.Date, nullable=False)
    requested_amount = db.Column(db.Numeric(12, 2), nullable=False)
    eligible_amount = db.Column(db.Numeric(12, 2), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="draft", index=True)
    officer_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    description = db.Column(db.Text)

    # The pair (member_id, policy_id) must exist in members -> the member
    # ALWAYS belongs to the same policy as the claim (database-level rule).
    __table_args__ = (
        db.ForeignKeyConstraint(["member_id", "policy_id"], ["members.id", "members.policy_id"],
                                name="fk_claim_member_same_policy"),
    )

    policy = db.relationship("Policy", foreign_keys=[policy_id])
    member = db.relationship("Member", foreign_keys=[member_id])
    hospital = db.relationship("Hospital")
    creator = db.relationship("User", foreign_keys=[created_by_user_id])
    officer = db.relationship("User", foreign_keys=[officer_user_id])
    documents = db.relationship("ClaimDocument", back_populates="claim", order_by="ClaimDocument.id",
                                cascade="all, delete-orphan")
    history = db.relationship("ClaimHistory", back_populates="claim",
                              order_by="ClaimHistory.timestamp, ClaimHistory.id")
    files = db.relationship("ClaimFile", back_populates="claim", order_by="ClaimFile.created_at")
    settlement = db.relationship("Settlement", back_populates="claim", uselist=False)

    @property
    def code(self):
        """Handle the code operation."""
        return f"CLM{self.id:03d}"

    def last_note(self, to_status):
        """Note of the most recent history event that moved the claim to `to_status`."""
        for event in reversed(self.history):
            if event.to_status == to_status:
                return event.note
        return ""


# ---- 7. claim_documents (checklist metadata only - no real files) ------------
class ClaimDocument(db.Model):
    """Represent the claimdocument data model."""
    __tablename__ = "claim_documents"
    id = db.Column(db.Integer, primary_key=True)
    claim_id = db.Column(db.Integer, db.ForeignKey("claims.id"), nullable=False, index=True)
    document_type = db.Column(db.String(60), nullable=False)
    checklist_status = db.Column(db.String(20), nullable=False, default="missing")  # missing/provided/verified
    original_filename = db.Column(db.String(255), nullable=True)
    storage_path = db.Column(db.String(500), nullable=True)
    content_type = db.Column(db.String(120), nullable=True)
    file_size = db.Column(db.Integer, nullable=True)
    uploaded_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    uploaded_at = db.Column(db.DateTime, nullable=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    replaces_document_id = db.Column(db.Integer, db.ForeignKey("claim_documents.id"), nullable=True)
    resend_reason = db.Column(db.Text, nullable=True)
    uploader = db.relationship("User", foreign_keys=[uploaded_by_user_id])
    previous_version = db.relationship("ClaimDocument", remote_side=[id], uselist=False)

    __table_args__ = (db.UniqueConstraint("claim_id", "document_type", name="uq_claim_document"),)
    claim = db.relationship("Claim", back_populates="documents")


# ---- 8. claim_history (audit trail) ------------------------------------------
class ClaimHistory(db.Model):
    """Represent the claimhistory data model."""
    __tablename__ = "claim_history"
    id = db.Column(db.Integer, primary_key=True)
    claim_id = db.Column(db.Integer, db.ForeignKey("claims.id"), nullable=False, index=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    from_status = db.Column(db.String(20), nullable=True)  # empty for the very first event
    to_status = db.Column(db.String(20), nullable=False)
    note = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, nullable=False, default=now)
    policy_id = db.Column(db.Integer, nullable=True, index=True)
    member_id = db.Column(db.Integer, nullable=True, index=True)
    event_type = db.Column(db.String(60), nullable=False, default="status_change")

    claim = db.relationship("Claim", back_populates="history")
    actor = db.relationship("User")


# ---- 9. settlements ----------------------------------------------------------
class Settlement(db.Model):
    """Represent the settlement data model."""
    __tablename__ = "settlements"
    id = db.Column(db.Integer, primary_key=True)
    # unique=True -> a claim can never be settled twice
    claim_id = db.Column(db.Integer, db.ForeignKey("claims.id"), nullable=False, unique=True)
    payee_type = db.Column(db.String(20), nullable=False)  # hospital / policyholder
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    recorded_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    recorded_at = db.Column(db.DateTime, nullable=False, default=now)

    claim = db.relationship("Claim", back_populates="settlement")
    recorder = db.relationship("User")


# ---- 10. document resend requests --------------------------------------------
class DocumentResendRequest(db.Model):
    __tablename__ = "document_resend_requests"
    id = db.Column(db.Integer, primary_key=True)
    claim_id = db.Column(db.Integer, db.ForeignKey("claims.id"), nullable=False, index=True)
    document_id = db.Column(db.Integer, db.ForeignKey("claim_documents.id"), nullable=False)
    requested_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(30), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, nullable=False, default=now)
    completed_at = db.Column(db.DateTime, nullable=True)
    claim = db.relationship("Claim")
    document = db.relationship("ClaimDocument")
    requester = db.relationship("User")


# ---- 11. in-app notifications -----------------------------------------------
class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    recipient_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    claim_id = db.Column(db.Integer, db.ForeignKey("claims.id"), nullable=True, index=True)
    resend_request_id = db.Column(db.Integer, db.ForeignKey("document_resend_requests.id"), nullable=True)
    message = db.Column(db.Text, nullable=False)
    notification_type = db.Column(db.String(40), nullable=False, default="general")
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=now)
    recipient = db.relationship("User")
    claim = db.relationship("Claim")
    resend_request = db.relationship("DocumentResendRequest")


# ---- persisted uploaded files / versions -------------------------------------
class ClaimFile(db.Model):
    __tablename__ = "claim_files"
    id = db.Column(db.Integer, primary_key=True)
    claim_id = db.Column(db.Integer, db.ForeignKey("claims.id"), nullable=False, index=True)
    document_id = db.Column(db.Integer, db.ForeignKey("claim_documents.id"), nullable=False, index=True)
    policy_id = db.Column(db.Integer, db.ForeignKey("policies.id"), nullable=False, index=True)
    member_id = db.Column(db.Integer, db.ForeignKey("members.id"), nullable=False, index=True)
    uploaded_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    storage_path = db.Column(db.String(500), nullable=False)
    content_type = db.Column(db.String(120), nullable=False)
    file_size = db.Column(db.Integer, nullable=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=now)
    document = db.relationship("ClaimDocument")
    claim = db.relationship("Claim", back_populates="files")
    policy = db.relationship("Policy")
    member = db.relationship("Member")
    uploader = db.relationship("User")
