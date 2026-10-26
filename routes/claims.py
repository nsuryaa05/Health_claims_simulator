"""Create, edit, view, submit and respond to claims."""
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, url_for

from extensions import db
from pathlib import Path
from werkzeug.utils import secure_filename
from models import ClaimFile, ClaimDocument, ClaimHistory, Notification, DocumentResendRequest
from models import CLAIM_TYPES, DOCUMENT_TYPES, STATUSES, Claim, Hospital, Member, Plan, Policy
from routes.auth import login_required, role_required
from services.claim_validation import available_coverage, eligibility_checks, find_duplicates, validate_claim
from services.claim_workflow import (WorkflowError, can_edit_claim, can_respond, can_view_claim,
                                     apply_filters, checklist_from_form, claims_for_user, create_claim,
                                     respond_to_request, submit_claim, update_draft)
from services.settlement import payout_details

bp = Blueprint("claims", __name__, url_prefix="/claims")

@bp.route("/policy-lookup/<string:policy_code>", methods=["GET"])
@role_required("policyholder", "hospital_desk")
def policy_lookup(policy_code):
    """Validate a typed policy code against the database and return covered members."""
    code = policy_code.strip().upper()
    if not code.startswith("POL") or not code[3:].isdigit():
        return {"found": False, "message": "Policy not found. Please check the Policy ID."}, 404
    policy = db.session.get(Policy, int(code[3:]))
    if (policy is None or policy.status != "active" or
            (g.user.role == "policyholder" and policy.holder_user_id != g.user.id)):
        return {"found": False, "message": "Policy not found. Please check the Policy ID."}, 404
    members = [{"id": m.id, "name": m.display_name, "relation": m.relation}
               for m in policy.members if m.is_covered]
    treatments = policy.plan.treatments if policy.plan else []
    return {"found": True, "policy_id": policy.id, "policy_code": policy.code,
            "plan": policy.plan.name, "members": members, "treatments": treatments,
            "message": "Policy found. You can continue with the claim."}
PER_PAGE = 8
ALLOWED_UPLOADS = {".pdf", ".png", ".jpg", ".jpeg"}

def save_uploaded_documents(claim, user, require_all=False):
    """Persist uploaded evidence and link it to the claim, policy, and member."""
    upload_root = Path(current_app.instance_path) / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    missing = []
    for doc_type in DOCUMENT_TYPES:
        file = request.files.get("file_" + str(DOCUMENT_TYPES.index(doc_type)))
        if not file or not file.filename:
            if require_all: missing.append(doc_type)
            continue
        ext = Path(file.filename).suffix.lower()
        if ext not in ALLOWED_UPLOADS:
            raise WorkflowError("Unsupported file type for %s. Upload PDF, PNG, JPG, or JPEG." % doc_type)
        data = file.read()
        if not data:
            raise WorkflowError("The uploaded file for %s is empty." % doc_type)
        if len(data) > 8 * 1024 * 1024:
            raise WorkflowError("Each document must be 8 MB or smaller.")
        file.stream.seek(0)
        doc = next((d for d in claim.documents if d.document_type == doc_type), None)
        if doc is None:
            doc = ClaimDocument(claim=claim, document_type=doc_type, checklist_status="provided")
            db.session.add(doc); db.session.flush()
        old_files = ClaimFile.query.filter_by(claim_id=claim.id, document_id=doc.id).all()
        version = max([x.version for x in old_files], default=0) + 1
        safe_name = secure_filename(file.filename) or ("document" + ext)
        stored_name = "claim_%s_doc_%s_v%s_%s" % (claim.id, doc.id, version, safe_name)
        target = upload_root / stored_name
        target.write_bytes(data)
        db.session.add(ClaimFile(claim_id=claim.id, document_id=doc.id, policy_id=claim.policy_id,
                                 member_id=claim.member_id, uploaded_by_user_id=user.id,
                                 original_filename=safe_name, storage_path=str(target),
                                 content_type=file.mimetype or "application/octet-stream",
                                 file_size=len(data), version=version))
        db.session.add(ClaimHistory(claim_id=claim.id, actor_user_id=user.id,
                                    from_status=claim.status, to_status=claim.status,
                                    note="Uploaded %s (version %s)" % (doc_type, version),
                                    policy_id=claim.policy_id, member_id=claim.member_id,
                                    event_type="document_uploaded"))
        doc.checklist_status = "provided"
    if missing:
        raise WorkflowError("Please upload all required documents: " + ", ".join(missing) + ".")
    db.session.flush()



# ---- helpers (also imported by hospital.py and officer.py) -------------------
def load_claim(claim_id):
    """Get a claim or stop with 404 / 403. Permission comes from the database query."""
    claim = db.get_or_404(Claim, claim_id)
    if not can_view_claim(g.user, claim):
        abort(403)
    return claim


def claim_list_page(base_query, title, mode, order=None):
    """Shared list page: search + filters + pagination (all kept in the URL)."""
    query = apply_filters(base_query, request.args)
    query = query.order_by(order if order is not None else Claim.id.desc())
    page = request.args.get("page", 1, type=int)
    pagination = query.paginate(page=page, per_page=PER_PAGE, error_out=False)
    return render_template("claims/list.html", title=title, mode=mode, pagination=pagination,
                           claims=pagination.items, statuses=STATUSES, claim_types=CLAIM_TYPES,
                           hospitals=Hospital.query.order_by(Hospital.name).all())


def values_from_claim(claim):
    """Handle the values from claim operation."""
    return {
        "policy_id": str(claim.policy_id), "member_id": str(claim.member_id),
        "hospital_id": str(claim.hospital_id), "treatment_type": claim.treatment_type,
        "admission_date": claim.admission_date.isoformat(), "discharge_date": claim.discharge_date.isoformat(),
        "requested_amount": str(claim.requested_amount), "description": claim.description or "",
        "documents": [d.document_type for d in claim.documents if d.checklist_status != "missing"],
    }


def values_from_form(form):
    """Handle the values from form operation."""
    keys = ("policy_id", "member_id", "hospital_id", "treatment_type", "admission_date",
            "discharge_date", "requested_amount", "description")
    values = {key: form.get(key, "") for key in keys}
    values["documents"] = form.getlist("documents")
    return values


def render_claim_form(claim, claim_type, values):
    """Handle the render claim form operation."""
    user = g.user
    if user.role == "policyholder":
        policies = Policy.query.filter_by(holder_user_id=user.id, status="active").all()
    else:
        policies = Policy.query.filter_by(status="active").all()
    ids = [p.id for p in policies]
    members = Member.query.filter(Member.policy_id.in_(ids)).all() if ids else []
    if claim_type == "cashless":
        hospitals = [user.hospital] if user.hospital else []   # own hospital only
    else:
        hospitals = Hospital.query.order_by(Hospital.name).all()
    treatments = sorted({t for plan in Plan.query.all() for t in plan.treatments})
    return render_template("claims/form.html", claim=claim, claim_type=claim_type, values=values,
                           policies=policies, members=members, hospitals=hospitals,
                           treatments=treatments, document_types=DOCUMENT_TYPES)


def warn_if_duplicate(claim):
    """Handle the warn if duplicate operation."""
    if find_duplicates(claim):
        flash("Warning: a similar claim already exists. The claims officer will check it.", "warning")


# ---- pages -------------------------------------------------------------------
@bp.route("/")
@role_required("policyholder")
def my_claims():
    """Handle the my claims operation."""
    return claim_list_page(claims_for_user(g.user), "My Claims", "mine")


@bp.route("/new", methods=["GET", "POST"])
@role_required("policyholder", "hospital_desk")
def new_claim():
    """Handle the new claim operation."""
    claim_type = "reimbursement" if g.user.role == "policyholder" else "cashless"
    values = {}
    if request.method == "POST":
        # The UI accepts a Policy ID (for example POL001); validation uses the DB primary key.
        form_data = request.form.copy()
        typed_code = (form_data.get("policy_code") or "").strip().upper()
        if typed_code:
            if typed_code.startswith("POL") and typed_code[3:].isdigit():
                found_policy = db.session.get(Policy, int(typed_code[3:]))
                if found_policy and found_policy.status == "active" and (g.user.role != "policyholder" or found_policy.holder_user_id == g.user.id):
                    form_data["policy_id"] = str(found_policy.id)
                else:
                    form_data["policy_id"] = ""
            else:
                form_data["policy_id"] = ""
        errors, data = validate_claim(g.user, form_data, claim_type)
        submit = request.form.get("action") == "submit"
        if submit:
            missing_uploads = [DOCUMENT_TYPES[i] for i in range(len(DOCUMENT_TYPES))
                               if not request.files.get("file_" + str(i)) or not request.files.get("file_" + str(i)).filename]
            if missing_uploads:
                errors.append("Please upload all required documents: " + ", ".join(missing_uploads) + ".")
        if not errors:
            try:
                claim = create_claim(g.user, data, claim_type, checklist_from_form(request.form), False)
                save_uploaded_documents(claim, g.user, require_all=submit)
                if submit:
                    submit_claim(claim, g.user)
            except WorkflowError as error:
                db.session.rollback()
                errors.append(str(error))
            else:
                flash("Claim %s." % ("submitted" if submit else "saved as a draft"), "success")
                warn_if_duplicate(claim)
                return redirect(url_for("claims.detail", claim_id=claim.id))   # Post/Redirect/Get
        for message in errors:
            flash(message, "danger")
        values = values_from_form(request.form)
        values["policy_code"] = request.form.get("policy_code", "")
    return render_claim_form(None, claim_type, values)


@bp.route("/<int:claim_id>/edit", methods=["GET", "POST"])
@role_required("policyholder", "hospital_desk")
def edit_claim(claim_id):
    """Handle the edit claim operation."""
    claim = load_claim(claim_id)
    if not can_edit_claim(g.user, claim):
        abort(403)
    if request.method == "POST":
        form_data = request.form.copy()
        typed_code = (form_data.get("policy_code") or "").strip().upper()
        if typed_code.startswith("POL") and typed_code[3:].isdigit():
            found_policy = db.session.get(Policy, int(typed_code[3:]))
            if found_policy and found_policy.status == "active" and (g.user.role != "policyholder" or found_policy.holder_user_id == g.user.id):
                form_data["policy_id"] = str(found_policy.id)
            else:
                form_data["policy_id"] = ""
        else:
            form_data["policy_id"] = ""
        errors, data = validate_claim(g.user, form_data, claim.claim_type)
        submit = request.form.get("action") == "submit"
        if submit:
            missing_uploads = [DOCUMENT_TYPES[i] for i in range(len(DOCUMENT_TYPES))
                               if not request.files.get("file_" + str(i)) or not request.files.get("file_" + str(i)).filename
                               if not ClaimFile.query.filter_by(claim_id=claim.id, document_id=next((d.id for d in claim.documents if d.document_type == DOCUMENT_TYPES[i]), -1)).first()]
            if missing_uploads:
                errors.append("Please upload all required documents: " + ", ".join(missing_uploads) + ".")
        if not errors:
            try:
                update_draft(claim, g.user, data, checklist_from_form(request.form), False)
                save_uploaded_documents(claim, g.user, require_all=submit)
                if submit:
                    submit_claim(claim, g.user)
            except WorkflowError as error:
                db.session.rollback()
                errors.append(str(error))
            else:
                flash("Claim %s." % ("submitted" if submit else "draft updated"), "success")
                warn_if_duplicate(claim)
                return redirect(url_for("claims.detail", claim_id=claim.id))
        for message in errors:
            flash(message, "danger")
        values = values_from_form(request.form)
        values["policy_code"] = request.form.get("policy_code", "")
    else:
        values = values_from_claim(claim)
        values["policy_code"] = claim.policy.code
    return render_claim_form(claim, claim.claim_type, values)


@bp.route("/<int:claim_id>")
@login_required
def detail(claim_id):
    """Handle the detail operation."""
    claim = load_claim(claim_id)
    is_staff = g.user.role in ("claims_officer", "administrator")
    return render_template(
        "claims/detail.html", claim=claim, checks=eligibility_checks(claim),
        coverage=available_coverage(claim),
        duplicates=find_duplicates(claim) if is_staff else [],
        can_edit=can_edit_claim(g.user, claim), can_respond=can_respond(g.user, claim),
        payout=payout_details(claim) if claim.eligible_amount is not None else None)


@bp.route("/<int:claim_id>/documents/<int:document_id>/resend", methods=["POST"])
@role_required("claims_officer")
def request_document_resend(claim_id, document_id):
    claim = load_claim(claim_id)
    if claim.status != "in_review" or claim.officer_user_id != g.user.id:
        abort(403)
    doc = db.get_or_404(ClaimDocument, document_id)
    if doc.claim_id != claim.id:
        abort(404)
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("Please enter a reason for requesting a replacement.", "danger")
    else:
        resend = DocumentResendRequest(claim_id=claim.id, document_id=doc.id,
                                       requested_by_user_id=g.user.id, reason=reason)
        db.session.add(resend); db.session.flush()
        doc.checklist_status = "missing"
        doc.resend_reason = reason
        db.session.add(Notification(recipient_user_id=claim.policy.holder_user_id, claim_id=claim.id,
                                    resend_request_id=resend.id, notification_type="document_resend",
                                    message="Your claim %s requires a corrected %s. Reason: %s" %
                                    (claim.code, doc.document_type, reason)))
        db.session.add(ClaimHistory(claim_id=claim.id, actor_user_id=g.user.id,
                                    from_status=claim.status, to_status=claim.status,
                                    note="Resend requested for %s: %s" % (doc.document_type, reason),
                                    policy_id=claim.policy_id, member_id=claim.member_id,
                                    event_type="document_resend_requested"))
        from services.claim_workflow import change_status
        change_status(claim, "more_info", g.user, "Document resend requested: " + reason)
        db.session.commit()
        flash("Replacement document requested. The policyholder has been notified.", "success")
    return redirect(url_for("officer.review", claim_id=claim.id))


@bp.route("/<int:claim_id>/documents/<int:document_id>/upload", methods=["POST"])
@role_required("policyholder", "hospital_desk")
def upload_replacement(claim_id, document_id):
    claim = load_claim(claim_id)
    owns_policy = g.user.role == "policyholder" and claim.policy.holder_user_id == g.user.id
    if claim.status != "more_info" or (claim.created_by_user_id != g.user.id and not owns_policy):
        abort(403)
    doc = db.get_or_404(ClaimDocument, document_id)
    if doc.claim_id != claim.id:
        abort(404)
    upload = request.files.get("replacement_file")
    if not upload or not upload.filename:
        flash("Please select a replacement file.", "danger")
        return redirect(url_for("claims.detail", claim_id=claim.id))
    ext = Path(upload.filename).suffix.lower()
    data = upload.read()
    if ext not in ALLOWED_UPLOADS or not data or len(data) > 8 * 1024 * 1024:
        flash("Upload a non-empty PDF, PNG, JPG, or JPEG file up to 8 MB.", "danger")
        return redirect(url_for("claims.detail", claim_id=claim.id))
    upload_root = Path(current_app.instance_path) / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    previous = ClaimFile.query.filter_by(claim_id=claim.id, document_id=doc.id).order_by(ClaimFile.version.desc()).first()
    version = (previous.version + 1) if previous else 1
    safe_name = secure_filename(upload.filename) or ("replacement" + ext)
    target = upload_root / ("claim_%s_doc_%s_v%s_%s" % (claim.id, doc.id, version, safe_name))
    target.write_bytes(data)
    db.session.add(ClaimFile(claim_id=claim.id, document_id=doc.id, policy_id=claim.policy_id,
                             member_id=claim.member_id, uploaded_by_user_id=g.user.id,
                             original_filename=safe_name, storage_path=str(target),
                             content_type=upload.mimetype or "application/octet-stream",
                             file_size=len(data), version=version))
    db.session.add(ClaimHistory(claim_id=claim.id, actor_user_id=g.user.id,
                                from_status=claim.status, to_status=claim.status,
                                note="Replacement document uploaded: %s (version %s)" % (doc.document_type, version),
                                policy_id=claim.policy_id, member_id=claim.member_id,
                                event_type="document_replaced"))
    doc.checklist_status = "provided"
    doc.resend_reason = None
    for n in Notification.query.filter_by(claim_id=claim.id, recipient_user_id=g.user.id, is_read=False).all():
        n.is_read = True
    db.session.commit()
    flash("Replacement document uploaded successfully.", "success")
    return redirect(url_for("claims.detail", claim_id=claim.id))


@bp.route("/notifications/<int:notification_id>/open")
@login_required
def open_notification(notification_id):
    notification = db.get_or_404(Notification, notification_id)
    if notification.recipient_user_id != g.user.id:
        abort(403)
    notification.is_read = True
    db.session.commit()
    if notification.claim_id:
        return redirect(url_for("claims.detail", claim_id=notification.claim_id))
    return redirect(url_for("dashboard.index"))


@bp.route("/files/<int:file_id>")
@login_required
def view_file(file_id):
    stored = db.get_or_404(ClaimFile, file_id)
    claim = db.get_or_404(Claim, stored.claim_id)
    if not can_view_claim(g.user, claim):
        abort(403)
    path = Path(stored.storage_path)
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype=stored.content_type, as_attachment=False, download_name=stored.original_filename)


@bp.route("/<int:claim_id>/submit", methods=["POST"])
@role_required("policyholder", "hospital_desk")
def submit(claim_id):
    """Handle the submit operation."""
    claim = load_claim(claim_id)
    try:
        if claim.status != "draft":
            raise WorkflowError("Only draft claims can be submitted here.")
        missing = [d.document_type for d in claim.documents
                   if not ClaimFile.query.filter_by(claim_id=claim.id, document_id=d.id).first()]
        if missing:
            raise WorkflowError("Please upload all required documents before submitting: " + ", ".join(missing) + ".")
        submit_claim(claim, g.user)
    except WorkflowError as error:
        db.session.rollback()
        flash(str(error), "danger")
    else:
        flash("Claim submitted successfully.", "success")
        warn_if_duplicate(claim)
    return redirect(url_for("claims.detail", claim_id=claim_id))


@bp.route("/<int:claim_id>/respond", methods=["GET", "POST"])
@role_required("policyholder", "hospital_desk")
def respond(claim_id):
    """Handle the respond operation."""
    claim = load_claim(claim_id)
    if not can_respond(g.user, claim):
        abort(403)
    if request.method == "POST":
        try:
            respond_to_request(claim, g.user, request.form.get("response_note", ""),
                               checklist_from_form(request.form))
        except WorkflowError as error:
            db.session.rollback()
            flash(str(error), "danger")
        else:
            flash("Your response was sent and the claim was resubmitted.", "success")
            return redirect(url_for("claims.detail", claim_id=claim_id))
    return render_template("claims/respond.html", claim=claim, document_types=DOCUMENT_TYPES,
                           request_note=claim.last_note("more_info"))
