"""Extra shared pages: profile settings, activity timeline, and help center."""
from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash

from extensions import db
from models import ClaimHistory, User
from routes.auth import login_required
from services.claim_workflow import claims_for_user

bp = Blueprint("extras", __name__)


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """Display the current profile and safely update the display name or password."""
    user = g.user
    if request.method == "POST":
        try:
            display_name = request.form.get("display_name", "").strip()
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")

            if len(display_name) < 2 or len(display_name) > 100:
                raise ValueError("Display name must contain 2 to 100 characters.")

            wants_password_change = bool(new_password or confirm_password or current_password)
            if wants_password_change:
                if not user.check_password(current_password):
                    raise ValueError("Your current password is incorrect.")
                if len(new_password) < 8:
                    raise ValueError("New password must contain at least 8 characters.")
                if new_password != confirm_password:
                    raise ValueError("New passwords do not match.")
                user.set_password(new_password)

            user.display_name = display_name
            db.session.commit()
            flash("Profile updated successfully.", "success")
            return redirect(url_for("extras.profile"))
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "danger")
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Profile update database error")
            flash("Profile could not be updated. Please try again.", "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Unexpected profile update error")
            flash("An unexpected error occurred while updating your profile.", "danger")
    return render_template("extras/profile.html")


@bp.route("/activity")
@login_required
def activity():
    """Show recent history only for claims visible to the current role."""
    try:
        visible_claim_ids = [claim.id for claim in claims_for_user(g.user).all()]
        events = []
        if visible_claim_ids:
            events = (ClaimHistory.query
                      .filter(ClaimHistory.claim_id.in_(visible_claim_ids))
                      .order_by(ClaimHistory.timestamp.desc(), ClaimHistory.id.desc())
                      .limit(100).all())
        return render_template("extras/activity.html", events=events)
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Activity timeline database error")
        flash("Activity could not be loaded right now.", "danger")
        return render_template("extras/activity.html", events=[])


@bp.route("/help")
@login_required
def help_center():
    """Display simple help and workflow guidance for the signed-in role."""
    return render_template("extras/help.html")
