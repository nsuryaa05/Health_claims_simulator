"""Administrator: users & roles, hospitals, plans, simulated settlements."""
from decimal import Decimal
from datetime import date

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from extensions import db
from models import ROLES, Claim, ClaimHistory, Hospital, Member, Plan, Policy, Settlement, User
from routes.auth import role_required, validate_new_account
from services.claim_validation import parse_money, to_int
from services.claim_workflow import WorkflowError
from services.settlement import payout_details, record_settlement

bp = Blueprint("admin", __name__, url_prefix="/admin")


def check_role_assignment(role, hospital_text):
    """Role must be valid; a hospital desk user must have a hospital."""
    errors, hospital_id = [], None
    if role not in ROLES:
        errors.append("Please choose a valid role.")
    if role == "hospital_desk":
        hospital_id = to_int(hospital_text)
        if db.session.get(Hospital, hospital_id or 0) is None:
            errors.append("A Hospital Desk user must be linked to a hospital.")
    return errors, hospital_id

# ---- users & roles -------------------------------------------------------------
@bp.route("/users")
@role_required("administrator")
def users():
    """Handle the users operation."""
    return render_template("admin/users.html", users=User.query.order_by(User.id).all(),
                           hospitals=Hospital.query.order_by(Hospital.name).all(), roles=ROLES)


@bp.route("/users/create", methods=["POST"])
@role_required("administrator")
def create_user():
    """Handle the create user operation."""
    errors = validate_new_account(request.form)
    role_errors, hospital_id = check_role_assignment(request.form.get("role"), request.form.get("hospital_id"))
    errors += role_errors
    if errors:
        for message in errors:
            flash(message, "danger")
    else:
        user = User(username=request.form["username"].strip().lower(),
                    display_name=request.form["display_name"].strip(),
                    role=request.form["role"], hospital_id=hospital_id)
        user.set_password(request.form["password"])
        db.session.add(user)
        db.session.commit()
        flash("User '%s' created as %s." % (user.username, user.role), "success")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/delete", methods=["POST"])
@role_required("administrator")
def delete_user(user_id):
    """Delete a user only when no records depend on that account."""
    user = db.get_or_404(User, user_id)

    # Prevent the logged-in administrator from deleting their own account.
    if user.id == g.user.id:
        flash("You cannot delete your own account.", "danger")
        return redirect(url_for("admin.users"))

    # Keep claim/policy/audit records intact. Users with linked records cannot
    # be deleted until those records are reassigned or removed appropriately.
    has_policy = Policy.query.filter_by(holder_user_id=user.id).first() is not None
    has_created_claim = Claim.query.filter_by(created_by_user_id=user.id).first() is not None
    has_assigned_claim = Claim.query.filter_by(officer_user_id=user.id).first() is not None
    has_history = ClaimHistory.query.filter_by(actor_user_id=user.id).first() is not None
    has_settlement = Settlement.query.filter_by(recorded_by=user.id).first() is not None

    if any((has_policy, has_created_claim, has_assigned_claim, has_history, has_settlement)):
        flash("Cannot remove this user because policies, claims, or audit records are linked to the account.", "danger")
        return redirect(url_for("admin.users"))

    username = user.username
    db.session.delete(user)
    db.session.commit()
    flash("User '%s' was removed successfully." % username, "success")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/update", methods=["POST"])
@role_required("administrator")
def update_user(user_id):
    """Handle the update user operation."""
    user = db.get_or_404(User, user_id)
    if user.id == g.user.id:
        flash("You cannot change your own role.", "danger")
        return redirect(url_for("admin.users"))
    errors, hospital_id = check_role_assignment(request.form.get("role"), request.form.get("hospital_id"))
    if errors:
        for message in errors:
            flash(message, "danger")
    else:
        user.role = request.form["role"]
        user.hospital_id = hospital_id
        db.session.commit()
        flash("Role updated for '%s'." % user.username, "success")
    return redirect(url_for("admin.users"))


# ---- hospitals -----------------------------------------------------------------
@bp.route("/hospitals")
@role_required("administrator")
def hospitals():
    """Handle the hospitals operation."""
    return render_template("admin/hospitals.html", hospitals=Hospital.query.order_by(Hospital.id).all())


@bp.route("/hospitals/save", methods=["POST"])
@role_required("administrator")
def save_hospital():
    """Handle the save hospital operation."""
    hospital_id = to_int(request.form.get("id"))
    hospital = db.session.get(Hospital, hospital_id) if hospital_id else Hospital()
    if hospital is None:
        abort(404)
    name = request.form.get("name", "").strip()
    city = request.form.get("city", "").strip()
    status = request.form.get("network_status", "")
    errors = []
    if not name or not city:
        errors.append("Hospital name and city are required.")
    if status not in ("network", "non_network"):
        errors.append("Please choose a network status.")
    if name and Hospital.query.filter(Hospital.name == name, Hospital.id != (hospital.id or 0)).first():
        errors.append("A hospital with this name already exists.")
    if errors:
        for message in errors:
            flash(message, "danger")
    else:
        hospital.name, hospital.city, hospital.network_status = name, city, status
        db.session.add(hospital)
        db.session.commit()
        flash("Hospital saved.", "success")
    return redirect(url_for("admin.hospitals"))

# ---- insurance plans -----------------------------------------------------------
@bp.route("/plans")
@role_required("administrator")
def plans():
    """Handle the plans operation."""
    return render_template("admin/plans.html", plans=Plan.query.order_by(Plan.id).all())


@bp.route("/plans/save", methods=["POST"])
@role_required("administrator")
def save_plan():
    """Handle the save plan operation."""
    plan_id = to_int(request.form.get("id"))
    plan = db.session.get(Plan, plan_id) if plan_id else Plan()
    if plan is None:
        abort(404)
    name = request.form.get("name", "").strip()
    treatments = [t.strip() for t in request.form.get("covered_treatment_types", "").split(",") if t.strip()]
    limit = parse_money(request.form.get("policy_amount_limit") or request.form.get("annual_limit"))
    deductible = parse_money(request.form.get("deductible"))
    copay = parse_money(request.form.get("copay_percent"))
    errors = []
    if not name:
        errors.append("Plan name is required.")
    if not treatments:
        errors.append("Enter at least one covered treatment type (comma separated).")
    if limit is None or limit <= 0:
        errors.append("Policy amount limit must be greater than zero.")
    if deductible is None or deductible < 0 or (limit is not None and deductible > limit):
        errors.append("Deductible must be zero or more and not above the policy amount limit.")
    if copay is None or not (Decimal("0") <= copay <= Decimal("100")):
        errors.append("Co-pay must be between 0 and 100.")
    if name and Plan.query.filter(Plan.name == name, Plan.id != (plan.id or 0)).first():
        errors.append("A plan with this name already exists.")
    if errors:
        for message in errors:
            flash(message, "danger")
    else:
        plan.name, plan.covered_treatment_types = name, ",".join(treatments)
        plan.policy_amount_limit, plan.deductible, plan.copay_percent = limit, deductible, copay
        db.session.add(plan)
        db.session.commit()
        flash("Plan saved.", "success")
    return redirect(url_for("admin.plans"))


# ---- policy management ---------------------------------------------------------
@bp.route("/policies")
@role_required("administrator")
def policies():
    """List policies and provide policy creation to administrators."""
    policyholders = User.query.filter_by(role="policyholder").order_by(User.display_name).all()
    plans = Plan.query.order_by(Plan.name).all()
    policies_list = Policy.query.order_by(Policy.id.desc()).all()
    return render_template("admin/policies.html", policyholders=policyholders,
                           plans=plans, policies=policies_list)


@bp.route("/policies/create", methods=["POST"])
@role_required("administrator")
def create_policy():
    """Create a policy for a policyholder and add the primary covered member."""
    holder_id = to_int(request.form.get("holder_user_id"))
    plan_id = to_int(request.form.get("plan_id"))
    holder = db.session.get(User, holder_id or 0)
    plan = db.session.get(Plan, plan_id or 0)
    member_name = request.form.get("member_name", "").strip()
    relation = request.form.get("relation", "").strip()
    insurer_name = request.form.get("insurer_name", "").strip() or "Demo Insurance Company"
    policy_limit = parse_money(request.form.get("policy_amount_limit") or (str(plan.annual_limit) if plan else ""))
    premium = parse_money(request.form.get("premium_amount") or "0")
    waiting_period = request.form.get("waiting_period", "").strip()
    exclusions = request.form.get("exclusions", "").strip()
    cashless_details = request.form.get("cashless_details", "").strip()
    terms_conditions = request.form.get("terms_conditions", "").strip()
    try:
        start_date = date.fromisoformat(request.form.get("start_date", ""))
        end_date = date.fromisoformat(request.form.get("end_date", ""))
    except ValueError:
        start_date = end_date = None

    errors = []
    if not holder or holder.role != "policyholder":
        errors.append("Select a valid policyholder account.")
    if not plan:
        errors.append("Select an existing insurance plan.")
    if not member_name:
        errors.append("Covered member name is required.")
    if not relation:
        errors.append("Covered member relationship is required.")
    if policy_limit is None or policy_limit <= 0:
        errors.append("Policy Amount Limit must be greater than zero.")
    if premium is None or premium < 0:
        errors.append("Premium amount must be zero or greater.")
    if not start_date or not end_date or (start_date and end_date and end_date < start_date):
        errors.append("Enter valid policy dates; end date must not be before start date.")

    if errors:
        for message in errors:
            flash(message, "danger")
    else:
        policy = Policy(holder_user_id=holder.id, plan_id=plan.id,
                        start_date=start_date, end_date=end_date, status="active",
                        policy_amount_limit=policy_limit, insurer_name=insurer_name,
                        premium_amount=premium, waiting_period=waiting_period,
                        exclusions=exclusions, cashless_details=cashless_details,
                        terms_conditions=terms_conditions)
        db.session.add(policy)
        db.session.flush()
        db.session.add(Member(policy_id=policy.id, display_name=member_name, relation=relation))
        db.session.commit()
        flash("Policy %s created and linked to %s." % (policy.code, holder.display_name), "success")
    return redirect(url_for("admin.policies"))


@bp.route("/policies/<int:policy_id>/members", methods=["POST"])
@role_required("administrator")
def add_policy_member(policy_id):
    policy = db.get_or_404(Policy, policy_id)
    name = request.form.get("display_name", "").strip()
    relation = request.form.get("relation", "").strip()
    if not name or relation not in ("Self", "Spouse", "Child", "Parent", "Other"):
        flash("Enter a valid member name and relationship.", "danger")
    else:
        member = Member(policy_id=policy.id, display_name=name, relation=relation)
        db.session.add(member)
        db.session.commit()
        flash("Member added to %s." % policy.code, "success")
    return redirect(url_for("admin.policies"))


# ---- simulated settlement ------------------------------------------------------
@bp.route("/settlements")
@role_required("administrator")
def settlements():
    """Handle the settlements operation."""
    waiting = Claim.query.filter_by(status="approved").order_by(Claim.id).all()
    done = Settlement.query.order_by(Settlement.id.desc()).all()
    return render_template("admin/settlements.html", waiting=waiting, done=done)


@bp.route("/settlements/<int:claim_id>")
@role_required("administrator")
def settle(claim_id):
    """Handle the settle operation."""
    claim = db.get_or_404(Claim, claim_id)
    if claim.status == "draft":
        abort(404)
    payout = payout_details(claim) if claim.eligible_amount is not None else None
    return render_template("admin/settle.html", claim=claim, payout=payout)


@bp.route("/settlements/<int:claim_id>/record", methods=["POST"])
@role_required("administrator")
def record(claim_id):
    """Handle the record operation."""
    claim = db.get_or_404(Claim, claim_id)
    try:
        amount = record_settlement(claim, g.user)   # amount is calculated on the server
    except WorkflowError as error:
        db.session.rollback()
        flash(str(error), "danger")
    else:
        flash("Simulated settlement of Rs %s recorded." % amount, "success")
    return redirect(url_for("admin.settle", claim_id=claim_id))
