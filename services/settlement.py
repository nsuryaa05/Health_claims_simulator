"""Simulated payout maths and settlement recording (classroom formula only)."""
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Settlement, now
from services.claim_workflow import WorkflowError, _commit, change_status


def calculate_payout(eligible_amount, deductible, copay_percent):
    """payout = (eligible - deductible_applied) x (1 - copay / 100)
    deductible_applied = min(eligible, deductible). Uses Decimal, rounded to 2 places."""
    eligible = Decimal(str(eligible_amount))
    deductible = Decimal(str(deductible))
    copay = Decimal(str(copay_percent))
    deductible_applied = min(eligible, deductible)
    payout = (eligible - deductible_applied) * (Decimal("1") - copay / Decimal("100"))
    return payout.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def payout_details(claim):
    """Every number shown on the settlement page."""
    plan = claim.policy.plan
    eligible = Decimal(str(claim.eligible_amount))
    deductible = Decimal(str(plan.deductible))
    return {
        "eligible": eligible,
        "deductible": deductible,
        "deductible_applied": min(eligible, deductible),
        "copay_percent": Decimal(str(plan.copay_percent)),
        "payout": calculate_payout(eligible, deductible, plan.copay_percent),
    }


def record_settlement(claim, admin):
    """Only an administrator, only for an APPROVED claim, only once.
    The amount is always re-calculated here - it is never taken from the form."""
    if admin.role != "administrator":
        raise WorkflowError("Only an administrator can record a settlement.")
    if claim.status != "approved":
        raise WorkflowError("Only approved claims can be settled.")
    if Settlement.query.filter_by(claim_id=claim.id).first() is not None:
        raise WorkflowError("This claim has already been settled.")
    amount = payout_details(claim)["payout"]
    payee = "hospital" if claim.claim_type == "cashless" else "policyholder"
    db.session.add(Settlement(claim_id=claim.id, payee_type=payee, amount=amount,
                              recorded_by=admin.id, recorded_at=now()))
    change_status(claim, "paid", admin, "Simulated settlement recorded: Rs %s to %s." % (amount, payee))
    try:
        _commit()   # status + history + settlement are saved together
    except IntegrityError:
        raise WorkflowError("This claim has already been settled.")
    return amount
