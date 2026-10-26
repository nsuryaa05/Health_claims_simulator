"""Administrator: reports, charts and CSV export."""
import csv
import io

from flask import Blueprint, Response, g, render_template
from sqlalchemy import func

from extensions import db
from models import Claim, Hospital
from routes.auth import role_required
from services.claim_workflow import claims_for_user, summary_totals

bp = Blueprint("reports", __name__, url_prefix="/reports")

STATUS_COLORS = {"submitted": "#2563eb", "in_review": "#7c3aed", "more_info": "#f59e0b",
                 "approved": "#16a34a", "rejected": "#dc2626", "paid": "#0d9488"}


def donut_gradient(by_status):
    """Builds the CSS conic-gradient string for the status doughnut chart."""
    total = sum(count for _, count in by_status)
    if total == 0:
        return "#e2e8f0 0% 100%"
    parts, start = [], 0.0
    for status, count in by_status:
        if count:
            end = start + count * 100.0 / total
            parts.append("%s %.2f%% %.2f%%" % (STATUS_COLORS[status], start, end))
            start = end
    return ", ".join(parts)


@bp.route("/")
@role_required("administrator")
def index():
    """Handle the index operation."""
    totals = summary_totals()
    visible = Claim.status != "draft"
    by_type = (db.session.query(Claim.claim_type, func.count(Claim.id))
               .filter(visible).group_by(Claim.claim_type).all())
    by_hospital = (db.session.query(Hospital.name, func.count(Claim.id))
                   .join(Claim, Claim.hospital_id == Hospital.id).filter(visible)
                   .group_by(Hospital.name).order_by(Hospital.name).all())
    biggest = max([c for _, c in by_type] + [c for _, c in by_hospital] + [1])
    return render_template("reports/index.html", totals=totals, by_type=by_type, by_hospital=by_hospital,
                           gradient=donut_gradient(totals["by_status"]), colors=STATUS_COLORS, biggest=biggest)


def safe_cell(value):
    """Stops spreadsheet formula injection when the CSV is opened in Excel."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


@bp.route("/export.csv")
@role_required("administrator")
def export_csv():
    """Handle the export csv operation."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Claim ID", "Member", "Hospital", "Type", "Treatment", "Status",
                     "Requested Amount", "Eligible Amount", "Settlement Amount"])
    for claim in claims_for_user(g.user).order_by(Claim.id).all():
        writer.writerow([safe_cell(v) for v in (
            claim.code, claim.member.display_name, claim.hospital.name, claim.claim_type,
            claim.treatment_type, claim.status, claim.requested_amount, claim.eligible_amount,
            claim.settlement.amount if claim.settlement else "")])
    return Response(buffer.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=claims_report.csv"})
