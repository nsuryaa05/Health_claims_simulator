"""Hospital Desk: claims of their own hospital + document checklist."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from extensions import db
from models import DOCUMENT_TYPES
from routes.auth import role_required
from routes.claims import claim_list_page, load_claim
from services.claim_workflow import checklist_from_form, claims_for_user, set_documents

bp = Blueprint("hospital", __name__, url_prefix="/hospital")


@bp.route("/claims")
@role_required("hospital_desk")
def claims():
    """Handle the claims operation."""
    return claim_list_page(claims_for_user(g.user), "Hospital Claims", "hospital")

