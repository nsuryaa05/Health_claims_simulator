"""Policyholder: their own policies and covered members."""
from flask import Blueprint, abort, g, render_template

from extensions import db
from models import Claim, Policy
from routes.auth import role_required

bp = Blueprint("policies", __name__, url_prefix="/policies")


@bp.route("/")
@role_required("policyholder")
def my_policies():
    """Handle the my policies operation."""
    policies = Policy.query.filter_by(holder_user_id=g.user.id).order_by(Policy.id).all()
    return render_template("policies/list.html", policies=policies)


@bp.route("/<int:policy_id>")
@role_required("policyholder")
def detail(policy_id):
    """Handle the detail operation."""
    policy = db.get_or_404(Policy, policy_id)
    if policy.holder_user_id != g.user.id:
        abort(403)   # not your policy
    claim_count = Claim.query.filter_by(policy_id=policy.id).count()
    return render_template("policies/detail.html", policy=policy, claim_count=claim_count)
