"""One /dashboard URL, four different dashboards (one per role)."""
from flask import Blueprint, g, render_template

from extensions import db
from models import Claim, Policy, User
from routes.auth import login_required
from services.claim_workflow import claims_for_user, summary_totals

bp = Blueprint("dashboard", __name__)


def count(query, *conditions):
    """Handle the count operation."""
    return query.filter(*conditions).count()


@bp.route("/dashboard")
@login_required
def index():
    """Handle the index operation."""
    user = g.user
    claims = claims_for_user(user)
    recent = claims.order_by(Claim.id.desc()).limit(5).all()

    if user.role == "policyholder":
        data = {
            "policies": Policy.query.filter_by(holder_user_id=user.id).count(),
            "total": claims.count(),
            "in_review": count(claims, Claim.status == "in_review"),
            "approved": count(claims, Claim.status.in_(("approved", "paid"))),
            "rejected": count(claims, Claim.status == "rejected"),
        }
    elif user.role == "hospital_desk":
        data = {
            "total": claims.count(),
            "submitted_cashless": count(claims, Claim.claim_type == "cashless", Claim.status == "submitted"),
            "more_info": count(claims, Claim.status == "more_info"),
            "approved": count(claims, Claim.status.in_(("approved", "paid"))),
        }
    elif user.role == "claims_officer":
        mine = Claim.query.filter(Claim.officer_user_id == user.id)
        data = {
            "unassigned": Claim.query.filter(Claim.status == "submitted", Claim.officer_user_id.is_(None)).count(),
            "assigned": mine.count(),
            "in_review": count(mine, Claim.status == "in_review"),
            "more_info": count(mine, Claim.status == "more_info"),
            "approved": count(mine, Claim.status.in_(("approved", "paid"))),
            "rejected": count(mine, Claim.status == "rejected"),
        }
        recent = (Claim.query.filter(Claim.status == "submitted", Claim.officer_user_id.is_(None))
                  .order_by(Claim.id).limit(5).all())   # preview of the review queue
    else:  # administrator
        totals = summary_totals()
        data = {
            "users": User.query.count(),
            "policies": Policy.query.count(),
            "totals": totals,
            "max_count": max([c for _, c in totals["by_status"]] + [1]),
        }
    return render_template("dashboards/%s.html" % user.role, recent=recent, **data)
