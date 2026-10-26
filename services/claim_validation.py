"""Server-side checks for claim data. Nothing here changes the database."""
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import func

from extensions import db
from models import CLAIM_TYPES, Claim, Hospital, Member, Policy


# ---- small helpers -----------------------------------------------------------
def to_int(text):
    """Handle the to int operation."""
    try:
        return int(str(text).strip())
    except (ValueError, TypeError):
        return None


def parse_date(text):
    """Handle the parse date operation."""
    try:
        return datetime.strptime(str(text).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def parse_money(text):
    """Text -> Decimal with 2 places, or None if it is not a valid number."""
    try:
        value = Decimal(str(text).strip())
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite():
        return None
    return value.quantize(Decimal("0.01"))


# ---- main validation ---------------------------------------------------------
def validate_claim(user, form, claim_type):
    """Check the claim form. Returns (errors, data).
    errors = list of messages; data = cleaned values (only use when errors is empty)."""
    errors = []

    # Claim type must match the role (policyholder = reimbursement, hospital = cashless)
    expected = "reimbursement" if user.role == "policyholder" else "cashless"
    if claim_type not in CLAIM_TYPES or claim_type != expected:
        errors.append("Your role can only create %s claims." % expected)

    # 1 + 3. Policy exists, belongs to the user (policyholder) and is active
    policy = db.session.get(Policy, to_int(form.get("policy_id")) or 0)
    if policy is None:
        errors.append("Please select a valid insurance policy.")
    elif user.role == "policyholder" and policy.holder_user_id != user.id:
        errors.append("This policy does not belong to you.")
        policy = None
    elif policy.status != "active":
        errors.append("The selected policy is not active.")

    # 2. Member belongs to the selected policy
    member = db.session.get(Member, to_int(form.get("member_id")) or 0)
    if member is None:
        errors.append("Please select a covered member.")
    elif policy is not None and member.policy_id != policy.id:
        errors.append("The selected member does not belong to the selected policy.")
    elif member is not None and not member.is_covered:
        errors.append("The selected member is not currently covered.")

    # 4 + 5. Dates
    admission = parse_date(form.get("admission_date"))
    discharge = parse_date(form.get("discharge_date"))
    if admission is None:
        errors.append("Admission date is required (format YYYY-MM-DD).")
    if discharge is None:
        errors.append("Discharge date is required (format YYYY-MM-DD).")
    if admission and discharge and discharge < admission:
        errors.append("Discharge date cannot be earlier than the admission date.")
    if admission and policy is not None and not policy.covers_date(admission):
        errors.append("Admission date must be within the policy period (%s to %s)."
                      % (policy.start_date, policy.end_date))

    # 6. Treatment covered by the plan
    treatment = (form.get("treatment_type") or "").strip()
    if not treatment:
        errors.append("Treatment type is required.")
    elif policy is not None and not policy.plan.covers(treatment):
        errors.append("'%s' is not covered by the %s." % (treatment, policy.plan.name))

    # 7. Amount greater than zero
    amount = parse_money(form.get("requested_amount"))
    if amount is None:
        errors.append("Requested amount must be a valid number.")
    elif amount <= 0:
        errors.append("Requested amount must be greater than zero.")
    elif amount > Decimal("99999999.99"):
        errors.append("Requested amount is too large.")
    elif policy is not None:
        used = (db.session.query(func.coalesce(func.sum(Claim.eligible_amount), 0))
                .filter(Claim.policy_id == policy.id, Claim.status.in_(("approved", "paid"))).scalar())
        remaining = max(Decimal("0.00"), Decimal(str(policy.amount_limit or policy.plan.annual_limit)) - Decimal(str(used or 0)))
        if amount > remaining:
            errors.append("Requested amount exceeds the remaining policy coverage of ₹%s." % format(remaining, ",.2f"))

    # Hospital rules
    hospital = db.session.get(Hospital, to_int(form.get("hospital_id")) or 0)
    if hospital is None:
        errors.append("Please select a hospital.")
    elif claim_type == "cashless":
        if not hospital.is_network:
            errors.append("Cashless claims are only allowed at network hospitals.")
        if user.role != "hospital_desk" or user.hospital_id != hospital.id:
            errors.append("You can only create cashless claims for your own hospital.")

    description = (form.get("description") or "").strip()
    if len(description) > 1000:
        errors.append("Description must be 1000 characters or fewer.")

    data = {
        "policy": policy, "member": member, "hospital": hospital,
        "treatment_type": treatment, "admission_date": admission,
        "discharge_date": discharge, "requested_amount": amount,
        "description": description,
    }
    return errors, data


# ---- duplicate warning (never blocks anything) -------------------------------
def find_duplicates(claim):
    """Other claims for the same member, hospital and admission date."""
    return (Claim.query
            .filter(Claim.member_id == claim.member_id,
                    Claim.hospital_id == claim.hospital_id,
                    Claim.admission_date == claim.admission_date,
                    Claim.id != claim.id,
                    Claim.status != "draft")
            .all())


# ---- checks shown on the review page and re-run before submit ----------------
def eligibility_checks(claim):
    """List of (label, passed) used by the officer and by submit_claim()."""
    policy = claim.policy
    checks = [
        ("Policy is active", policy.status == "active"),
        ("Admission date is inside the policy period", policy.covers_date(claim.admission_date)),
        ("Treatment is covered by the plan", policy.plan.covers(claim.treatment_type)),
        ("Member belongs to the policy", claim.member.policy_id == policy.id),
        ("Discharge date is not before admission", claim.discharge_date >= claim.admission_date),
    ]
    if claim.claim_type == "cashless":
        checks.append(("Hospital is in the network (cashless)", claim.hospital.is_network))
    return checks


def available_coverage(claim):
    """Annual limit minus the eligible amounts already approved/paid on this policy."""
    used = (db.session.query(func.coalesce(func.sum(Claim.eligible_amount), 0))
            .filter(Claim.policy_id == claim.policy_id, Claim.id != claim.id,
                    Claim.status.in_(("approved", "paid")))
            .scalar())
    return max(Decimal("0.00"), Decimal(str(claim.policy.amount_limit or 0)) - Decimal(str(used)))
