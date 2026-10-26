"""The claim life-cycle. EVERY status change goes through change_status(),
so invalid transitions are impossible - even if someone sends a hand-made request."""
from decimal import Decimal

from sqlalchemy import func, or_

from extensions import db
from models import (CHECKLIST_STATUSES, DOCUMENT_TYPES, STATUSES, Claim, ClaimDocument,
                    ClaimHistory, Member, Policy, Settlement)
from services.claim_validation import eligibility_checks, parse_money

# current status -> statuses it is allowed to move to
ALLOWED_TRANSITIONS = {
    "draft": ["submitted"],
    "submitted": ["in_review"],
    "in_review": ["more_info", "approved", "rejected"],
    "more_info": ["submitted"],
    "approved": ["paid"],
    "rejected": [],   # terminal
    "paid": [],       # terminal
}
ACTIVE_QUEUE_STATUSES = ("submitted", "in_review", "more_info")


class WorkflowError(Exception):
    """A rule was broken. The message is safe to show to the user."""


def _commit():
    """Handle the  commit operation."""
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


# ==== the one place where a status changes ====================================
def change_status(claim, new_status, actor, note=""):
    """Update the status AND add the history row. The caller commits, so both
    changes are saved together in ONE database transaction."""
    if new_status not in ALLOWED_TRANSITIONS.get(claim.status, []):
        raise WorkflowError("A claim cannot move from '%s' to '%s'." % (claim.status, new_status))
    old_status = claim.status
    claim.status = new_status
    db.session.add(ClaimHistory(claim=claim, actor_user_id=actor.id,
                                from_status=old_status, to_status=new_status, note=note,
                                policy_id=claim.policy_id, member_id=claim.member_id,
                                event_type="status_change"))


# ==== who can see / edit what =================================================
def claims_for_user(user):
    """Base query: ONLY the claims this user may see (permission is in the query itself)."""
    query = (Claim.query
             .join(Member, Claim.member_id == Member.id)
             .join(Policy, Claim.policy_id == Policy.id))
    if user.role == "policyholder":
        query = query.filter(Policy.holder_user_id == user.id,
                             or_(Claim.status != "draft", Claim.created_by_user_id == user.id))
    elif user.role == "hospital_desk":
        query = query.filter(Claim.hospital_id == user.hospital_id)
    else:  # claims officer / administrator never see drafts
        query = query.filter(Claim.status != "draft")
    return query


def can_view_claim(user, claim):
    """Handle the can view claim operation."""
    return claims_for_user(user).filter(Claim.id == claim.id).first() is not None


def can_edit_claim(user, claim):
    """Handle the can edit claim operation."""
    return claim.created_by_user_id == user.id and claim.status == "draft"


def can_respond(user, claim):
    """The creator or the owning policyholder may respond to a document request."""
    owns_policy = user.role == "policyholder" and claim.policy.holder_user_id == user.id
    return (claim.created_by_user_id == user.id or owns_policy) and claim.status == "more_info"


# ==== search + filters (GET parameters) =======================================
def apply_filters(query, args):
    """Handle the apply filters operation."""
    text = args.get("q", "").strip()
    if text:
        conditions = [func.lower(Member.display_name).contains(text.lower(), autoescape=True)]
        number = text.upper().replace("CLM", "").strip()
        if number.isdigit() and len(number) < 9:
            conditions.append(Claim.id == int(number))
        query = query.filter(or_(*conditions))
    status = args.get("status", "")
    if status in STATUSES:
        query = query.filter(Claim.status == status)
    claim_type = args.get("type", "")
    if claim_type in ("cashless", "reimbursement"):
        query = query.filter(Claim.claim_type == claim_type)
    hospital_id = args.get("hospital", type=int)
    if hospital_id:
        query = query.filter(Claim.hospital_id == hospital_id)
    return query


# ==== document checklist ======================================================
def checklist_from_form(form):
    """Checked boxes -> 'provided', unchecked -> 'missing'."""
    ticked = form.getlist("documents")
    return {doc: ("provided" if doc in ticked else "missing") for doc in DOCUMENT_TYPES}


def set_documents(claim, statuses):
    """Handle the set documents operation."""
    existing = {d.document_type: d for d in claim.documents}
    for doc_type in DOCUMENT_TYPES:
        new_status = statuses.get(doc_type, "missing")
        if new_status not in CHECKLIST_STATUSES:
            new_status = "missing"
        doc = existing.get(doc_type)
        if doc is None:
            db.session.add(ClaimDocument(claim=claim, document_type=doc_type, checklist_status=new_status))
        elif not (doc.checklist_status == "verified" and new_status == "provided"):
            doc.checklist_status = new_status  # keep 'verified' if still provided


# ==== creator actions =========================================================
def create_claim(user, data, claim_type, doc_statuses, submit=False):
    """Handle the create claim operation."""
    claim = Claim(policy_id=data["policy"].id, member_id=data["member"].id,
                  hospital_id=data["hospital"].id, created_by_user_id=user.id,
                  claim_type=claim_type, treatment_type=data["treatment_type"],
                  admission_date=data["admission_date"], discharge_date=data["discharge_date"],
                  requested_amount=data["requested_amount"], description=data["description"],
                  status="draft")
    db.session.add(claim)
    db.session.add(ClaimHistory(claim=claim, actor_user_id=user.id, from_status=None,
                                to_status="draft", note="Draft created"))
    set_documents(claim, doc_statuses)
    if submit:
        change_status(claim, "submitted", user, "Claim submitted")
    _commit()
    return claim


def update_draft(claim, user, data, doc_statuses, submit=False):
    """Handle the update draft operation."""
    if not can_edit_claim(user, claim):
        raise WorkflowError("Only the creator can edit a draft claim.")
    claim.policy_id = data["policy"].id
    claim.member_id = data["member"].id
    claim.hospital_id = data["hospital"].id
    claim.treatment_type = data["treatment_type"]
    claim.admission_date = data["admission_date"]
    claim.discharge_date = data["discharge_date"]
    claim.requested_amount = data["requested_amount"]
    claim.description = data["description"]
    set_documents(claim, doc_statuses)
    if submit:
        change_status(claim, "submitted", user, "Claim submitted")
    _commit()


def submit_claim(claim, user, note="Claim submitted"):
    """Handle the submit claim operation."""
    owns_policy = user.role == "policyholder" and claim.policy.holder_user_id == user.id
    if claim.created_by_user_id != user.id and not owns_policy:
        raise WorkflowError("Only the claim creator or policyholder can submit this claim.")
    failed = [label for label, ok in eligibility_checks(claim) if not ok]
    if failed:
        raise WorkflowError("Cannot submit. These checks failed: " + "; ".join(failed))
    old_status = claim.status
    change_status(claim, "submitted", user, note)
    if old_status == "more_info":
        claim.officer_user_id = None  # goes back to the queue; an officer picks it up again
    _commit()


def respond_to_request(claim, user, response_note, doc_statuses):
    """more_info -> submitted, with the submitter's answer and updated checklist."""
    if not can_respond(user, claim):
        raise WorkflowError("Only the original submitter can respond to this request.")
    if not response_note.strip():
        raise WorkflowError("Please write a response note.")
    set_documents(claim, doc_statuses)
    submit_claim(claim, user, "Response from submitter: " + response_note.strip())


# ==== claims officer actions ==================================================
def _require_officer(officer):
    """Handle the  require officer operation."""
    if officer.role != "claims_officer":
        raise WorkflowError("Only a claims officer can do this.")


def _require_my_claim_in_review(claim, officer):
    """Handle the  require my claim in review operation."""
    _require_officer(officer)
    if claim.status != "in_review":
        raise WorkflowError("This claim is not in review.")
    if claim.officer_user_id != officer.id:
        raise WorkflowError("This claim is assigned to another officer.")


def assign_to_officer(claim, officer):
    """Handle the assign to officer operation."""
    _require_officer(officer)
    if claim.status != "submitted":
        raise WorkflowError("Only submitted claims can be assigned.")
    claim.officer_user_id = officer.id
    change_status(claim, "in_review", officer, "Assigned to %s. Review started." % officer.display_name)
    _commit()


def request_more_info(claim, officer, note):
    """Handle the request more info operation."""
    _require_my_claim_in_review(claim, officer)
    if not note.strip():
        raise WorkflowError("A note is required when requesting more information.")
    change_status(claim, "more_info", officer, note.strip())
    _commit()


def approve_claim(claim, officer, eligible_text, reason):
    """Handle the approve claim operation."""
    _require_my_claim_in_review(claim, officer)
    amount = parse_money(eligible_text)
    if amount is None or amount <= 0:
        raise WorkflowError("Eligible amount must be a number greater than zero.")
    if amount > claim.requested_amount:
        raise WorkflowError("Eligible amount cannot be more than the requested amount.")
    used = (db.session.query(func.coalesce(func.sum(Claim.eligible_amount), 0))
            .filter(Claim.policy_id == claim.policy_id,
                    Claim.status.in_(("approved", "paid"))).scalar())
    remaining = max(Decimal("0.00"), Decimal(str(claim.policy.amount_limit or claim.policy.plan.annual_limit))
                    - Decimal(str(used or 0)))
    if amount > remaining:
        raise WorkflowError("Eligible amount exceeds the remaining policy coverage of ₹%s." % format(remaining, ",.2f"))
    if not reason.strip():
        raise WorkflowError("A reason is required to approve a claim.")
    claim.eligible_amount = amount
    change_status(claim, "approved", officer, reason.strip())
    _commit()


def reject_claim(claim, officer, reason):
    """Handle the reject claim operation."""
    _require_my_claim_in_review(claim, officer)
    if not reason.strip():
        raise WorkflowError("A reason is required to reject a claim.")
    change_status(claim, "rejected", officer, reason.strip())
    _commit()


def mark_documents_verified(claim, officer):
    """Handle the mark documents verified operation."""
    _require_my_claim_in_review(claim, officer)
    for doc in claim.documents:
        if doc.checklist_status == "provided":
            doc.checklist_status = "verified"
    _commit()


# ==== totals for dashboards and reports =======================================
def summary_totals():
    """Handle the summary totals operation."""
    visible = Claim.status != "draft"
    counts = dict(db.session.query(Claim.status, func.count(Claim.id)).filter(visible)
                  .group_by(Claim.status).all())
    by_status = [(s, counts.get(s, 0)) for s in STATUSES if s != "draft"]
    zero = func.coalesce(func.sum(Claim.requested_amount), 0)
    return {
        "total_claims": sum(counts.values()),
        "by_status": by_status,
        "requested_total": db.session.query(zero).filter(visible).scalar(),
        "eligible_total": db.session.query(func.coalesce(func.sum(Claim.eligible_amount), 0))
                            .filter(Claim.eligible_amount.isnot(None)).scalar(),
        "settled_total": db.session.query(func.coalesce(func.sum(Settlement.amount), 0)).scalar(),
    }
