"""Claims Officer: review queue, assign, request info, approve, reject."""
from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from extensions import db
from models import Claim
from routes.auth import role_required
from routes.claims import claim_list_page, load_claim
from services.claim_validation import available_coverage, eligibility_checks, find_duplicates
from services.claim_workflow import (ACTIVE_QUEUE_STATUSES, WorkflowError, approve_claim, assign_to_officer,
                                     claims_for_user, mark_documents_verified, reject_claim,
                                     request_more_info)

bp = Blueprint("officer", __name__, url_prefix="/officer")


@bp.route("/queue")
@role_required("claims_officer")
def queue():
    """Handle the queue operation."""
    base = claims_for_user(g.user)
    if not request.args.get("status"):   # default view = claims that still need work
        base = base.filter(Claim.status.in_(ACTIVE_QUEUE_STATUSES))
    return claim_list_page(base, "Review Queue", "queue", order=Claim.id.asc())


@bp.route("/claims/<int:claim_id>")
@role_required("claims_officer")
def review(claim_id):
    """Handle the review operation."""
    claim = load_claim(claim_id)
    is_mine = claim.officer_user_id == g.user.id
    return render_template("officer/review.html", claim=claim,
                           coverage=available_coverage(claim), duplicates=find_duplicates(claim),
                           is_mine=is_mine, can_decide=is_mine and claim.status == "in_review")


def run(claim_id, action, success_message, *args):
    """Call a workflow function; show its error message if a rule is broken."""
    claim = load_claim(claim_id)
    try:
        action(claim, g.user, *args)
    except WorkflowError as error:
        db.session.rollback()
        flash(str(error), "danger")
    else:
        flash(success_message, "success")
    return redirect(url_for("officer.review", claim_id=claim_id))   # Post/Redirect/Get


@bp.route("/claims/<int:claim_id>/assign", methods=["POST"])
@role_required("claims_officer")
def assign(claim_id):
    """Handle the assign operation."""
    return run(claim_id, assign_to_officer, "Claim assigned to you. Review started.")


@bp.route("/claims/<int:claim_id>/request-info", methods=["POST"])
@role_required("claims_officer")
def request_info(claim_id):
    """Handle the request info operation."""
    return run(claim_id, request_more_info, "Additional information requested.",
               request.form.get("note", ""))


@bp.route("/claims/<int:claim_id>/approve", methods=["POST"])
@role_required("claims_officer")
def approve(claim_id):
    """Handle the approve operation."""
    return run(claim_id, approve_claim, "Claim approved.",
               request.form.get("eligible_amount", ""), request.form.get("reason", ""))


@bp.route("/claims/<int:claim_id>/reject", methods=["POST"])
@role_required("claims_officer")
def reject(claim_id):
    """Handle the reject operation."""
    return run(claim_id, reject_claim, "Claim rejected.", request.form.get("reason", ""))


@bp.route("/claims/<int:claim_id>/verify-documents", methods=["POST"])
@role_required("claims_officer")
def verify_documents(claim_id):
    """Handle the verify documents operation."""
    return run(claim_id, mark_documents_verified, "Provided documents marked as verified.")
