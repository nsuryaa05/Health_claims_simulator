"""Creates the tables and loads DEMO DATA (all fictional).

NOTE: no CSV files were supplied, so this file contains clearly labelled demo
records that match the classroom size: 15 users, 5 hospitals, 3 plans,
8 policies, 11 members and 13 claims. When you get the official CSV files,
put them in data/csv_files/ and replace the lists below with CSV reading.

Run:   python seed_data.py            (creates tables + data, only if empty)
       python seed_data.py --reset    (drops everything and starts fresh)
Every demo user has the password:  Demo@123
"""
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal

from werkzeug.security import generate_password_hash

from extensions import db
from models import (Claim, ClaimDocument, ClaimHistory, Hospital, Member, Plan, Policy, Settlement, User)
from services.settlement import calculate_payout

DEMO_PASSWORD = "Demo@123"

# (id, name, city, network_status)
HOSPITALS = [
    (1, "Apollo Hospital", "Hyderabad", "network"),
    (2, "Sunshine Hospital", "Bengaluru", "network"),
    (3, "Care Clinic", "Chennai", "network"),
    (4, "City Hospital", "Mumbai", "network"),
    (5, "Lakeside Medical Centre", "Pune", "non_network"),
]
# (id, name, covered treatments, annual_limit, deductible, copay_percent)
PLANS = [
    (1, "Individual Plan", "General Medicine,Surgery,Orthopedics", 300000, 1500, 15),
    (2, "Family Floater Plan", "General Medicine,Cardiology,Surgery,Maternity,Pediatrics", 500000, 2000, 10),
    (3, "Premium Plan", "General Medicine,Cardiology,Surgery,Orthopedics,Oncology,Neurology,Maternity,Pediatrics",
     1000000, 2500, 5),
]
# (id, username, display_name, role, hospital_id)
USERS = [
    (1, "admin", "Admin User", "administrator", None),
    (2, "officer1", "Dr. Sharma", "claims_officer", None),
    (3, "officer2", "Neha Gupta", "claims_officer", None),
    (4, "officer3", "Arjun Das", "claims_officer", None),
    (5, "hospital1", "Apollo Desk", "hospital_desk", 1),
    (6, "hospital2", "Sunshine Desk", "hospital_desk", 2),
    (7, "hospital3", "Care Clinic Desk", "hospital_desk", 3),
    (8, "hospital4", "City Hospital Desk", "hospital_desk", 4),
    (9, "ravi", "Ravi Kumar", "policyholder", None),
    (10, "sneha", "Sneha Reddy", "policyholder", None),
    (11, "anil", "Anil Sharma", "policyholder", None),
    (12, "priya", "Priya Singh", "policyholder", None),
    (13, "karthik", "Karthik Rao", "policyholder", None),
    (14, "meera", "Meera Iyer", "policyholder", None),
    (15, "rakesh", "Rakesh Patel", "policyholder", None),
]
# (id, holder_user_id, plan_id, start_date, end_date, status)
POLICIES = [
    (1, 9, 2, date(2026, 1, 1), date(2026, 12, 31), "active"),
    (2, 9, 1, date(2026, 3, 1), date(2027, 2, 28), "active"),
    (3, 10, 2, date(2026, 1, 1), date(2026, 12, 31), "active"),
    (4, 11, 1, date(2026, 2, 1), date(2027, 1, 31), "active"),
    (5, 12, 3, date(2026, 1, 1), date(2026, 12, 31), "active"),
    (6, 13, 1, date(2026, 4, 1), date(2027, 3, 31), "active"),
    (7, 14, 2, date(2026, 5, 1), date(2027, 4, 30), "active"),
    (8, 15, 3, date(2025, 6, 1), date(2026, 5, 31), "expired"),
]
# (id, policy_id, display_name, relationship)
MEMBERS = [
    (1, 1, "Ravi Kumar", "Self"), (2, 1, "Anita Kumar", "Spouse"), (3, 2, "Ravi Kumar", "Self"),
    (4, 3, "Sneha Reddy", "Self"), (5, 3, "Vikram Reddy", "Spouse"), (6, 4, "Anil Sharma", "Self"),
    (7, 5, "Priya Singh", "Self"), (8, 5, "Rohan Singh", "Child"), (9, 6, "Karthik Rao", "Self"),
    (10, 7, "Meera Iyer", "Self"), (11, 8, "Rakesh Patel", "Self"),
]
# (id, policy, member, hospital, type, treatment, admission, discharge, requested, eligible,
#  status, officer, created_by, description)
CLAIMS = [
    (1, 2, 3, 1, "reimbursement", "General Medicine", date(2026, 6, 10), date(2026, 6, 12), 6000, None, "in_review", 2, 9, "Viral fever treatment"),
    (2, 3, 4, 2, "cashless", "Cardiology", date(2026, 6, 2), date(2026, 6, 6), 45000, None, "submitted", None, 6, "Heart treatment"),
    (3, 4, 6, 3, "reimbursement", "Surgery", date(2026, 5, 15), date(2026, 5, 20), 12000, 12000, "approved", 2, 11, "Minor surgery"),
    (4, 5, 7, 4, "cashless", "Neurology", date(2026, 7, 10), date(2026, 7, 14), 30000, None, "more_info", 3, 8, "Neurology observation"),
    (5, 6, 9, 1, "reimbursement", "Orthopedics", date(2026, 5, 1), date(2026, 5, 3), 8000, None, "rejected", 2, 13, "Knee pain consultation"),
    (6, 8, 11, 3, "cashless", "Orthopedics", date(2025, 12, 10), date(2025, 12, 15), 60000, 60000, "paid", 3, 7, "Hip surgery"),
    (7, 7, 10, 3, "reimbursement", "Maternity", date(2026, 8, 20), date(2026, 8, 25), 15000, None, "submitted", None, 14, "Delivery expenses"),
    (8, 1, 2, 1, "reimbursement", "General Medicine", date(2026, 9, 1), date(2026, 9, 3), 9500, None, "submitted", None, 9, "Dengue treatment"),
    (9, 3, 5, 2, "cashless", "Surgery", date(2026, 9, 12), date(2026, 9, 15), 52000, None, "in_review", 3, 6, "Appendix surgery"),
    (10, 5, 7, 1, "reimbursement", "Oncology", date(2026, 3, 10), date(2026, 3, 12), 20000, 20000, "approved", 4, 12, "Chemotherapy session"),
    (11, 4, 6, 5, "reimbursement", "General Medicine", date(2026, 10, 2), date(2026, 10, 3), 3500, None, "draft", None, 11, "Saved draft"),
    # CLM012 looks like CLM002 on purpose: it shows the duplicate WARNING (not a rejection).
    (12, 3, 4, 2, "cashless", "Cardiology", date(2026, 6, 2), date(2026, 6, 6), 45000, None, "submitted", None, 6, "Heart treatment (possible duplicate)"),
    (13, 6, 9, 3, "reimbursement", "Surgery", date(2026, 9, 20), date(2026, 9, 22), 25000, 22000, "approved", 2, 13, "Hernia surgery"),
]

PATHS = {
    "draft": ["draft"],
    "submitted": ["draft", "submitted"],
    "in_review": ["draft", "submitted", "in_review"],
    "more_info": ["draft", "submitted", "in_review", "more_info"],
    "approved": ["draft", "submitted", "in_review", "approved"],
    "rejected": ["draft", "submitted", "in_review", "rejected"],
    "paid": ["draft", "submitted", "in_review", "approved", "paid"],
}
NOTES = {
    "draft": "Draft created", "submitted": "Claim submitted",
    "in_review": "Assigned to officer. Review started.",
    "more_info": "Discharge summary and prescription are missing. Please provide them.",
    "approved": "Treatment is covered and documents verified.",
    "rejected": "Treatment not covered under the policy terms (demo reason).",
    "paid": "Simulated settlement recorded.",
}
DOCS = ("Hospital Bill", "Discharge Summary", "Prescription", "ID Proof")


def doc_status(claim_status, doc_type):
    """Handle the doc status operation."""
    if claim_status in ("approved", "paid"):
        return "verified"
    if claim_status == "more_info":
        return "missing" if doc_type in ("Discharge Summary", "Prescription") else "provided"
    if claim_status == "draft":
        return "provided" if doc_type in ("Hospital Bill", "ID Proof") else "missing"
    if claim_status == "rejected":
        return "provided" if doc_type != "Prescription" else "missing"
    return "provided"


def seed_database():
    """Insert records in dependency order: hospitals, plans, users, policies,
    members, claims, claim documents, claim history, settlements."""
    password_hash = generate_password_hash(DEMO_PASSWORD)   # hashed locally, never stored as plain text

    for h in HOSPITALS:
        db.session.add(Hospital(id=h[0], name=h[1], city=h[2], network_status=h[3]))
    for p in PLANS:
        db.session.add(Plan(id=p[0], name=p[1], covered_treatment_types=p[2], annual_limit=p[3],
                            deductible=p[4], copay_percent=p[5]))
    db.session.flush()
    for u in USERS:
        db.session.add(User(id=u[0], username=u[1], password_hash=password_hash,
                            display_name=u[2], role=u[3], hospital_id=u[4]))
    db.session.flush()
    for p in POLICIES:
        db.session.add(Policy(id=p[0], holder_user_id=p[1], plan_id=p[2], start_date=p[3],
                              end_date=p[4], status=p[5]))
    db.session.flush()
    for m in MEMBERS:
        db.session.add(Member(id=m[0], policy_id=m[1], display_name=m[2], relation=m[3]))
    db.session.flush()
    for c in CLAIMS:
        db.session.add(Claim(id=c[0], policy_id=c[1], member_id=c[2], hospital_id=c[3], claim_type=c[4],
                             treatment_type=c[5], admission_date=c[6], discharge_date=c[7],
                             requested_amount=c[8], eligible_amount=c[9], status=c[10],
                             officer_user_id=c[11], created_by_user_id=c[12], description=c[13]))
    db.session.flush()

    base_time = datetime(2026, 9, 1, 9, 0, 0)
    for c in CLAIMS:
        claim_id, status, officer_id, creator_id = c[0], c[10], c[11], c[12]
        for doc_type in DOCS:
            db.session.add(ClaimDocument(claim_id=claim_id, document_type=doc_type,
                                         checklist_status=doc_status(status, doc_type)))
        previous = None
        for step, to_status in enumerate(PATHS[status]):
            if to_status in ("draft", "submitted"):
                actor = creator_id
            elif to_status == "paid":
                actor = 1
            else:
                actor = officer_id
            db.session.add(ClaimHistory(claim_id=claim_id, actor_user_id=actor, from_status=previous,
                                        to_status=to_status, note=NOTES[to_status],
                                        timestamp=base_time + timedelta(days=claim_id, hours=step * 5)))
            previous = to_status

    # CLM006 is already paid -> it needs its settlement row (same formula as the app)
    paid_claim = next(c for c in CLAIMS if c[0] == 6)
    plan = next(p for p in PLANS if p[0] == 3)
    amount = calculate_payout(paid_claim[9], plan[4], plan[5])
    db.session.add(Settlement(claim_id=6, payee_type="hospital", amount=amount, recorded_by=1,
                              recorded_at=base_time + timedelta(days=12)))
    db.session.commit()


def main():
    """Handle the main operation."""
    from app import create_app
    app = create_app()
    with app.app_context():
        if "--reset" in sys.argv:
            db.drop_all()
        db.create_all()
        from services.schema_migration import migrate_additive_columns
        migrate_additive_columns(db.engine)
        if User.query.count() > 0:
            print("Database already has data. Use --reset to start fresh.")
            return
        seed_database()
        print("Demo data loaded: %d users, %d hospitals, %d plans, %d policies, %d members, %d claims."
              % (len(USERS), len(HOSPITALS), len(PLANS), len(POLICIES), len(MEMBERS), len(CLAIMS)))
        print("Log in with any username (admin, officer1, hospital1, ravi ...) and password: " + DEMO_PASSWORD)


if __name__ == "__main__":
    main()
