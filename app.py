"""Application factory: builds the Flask app and connects everything together.
Run with:  python app.py   (or: flask run)"""
from decimal import Decimal
from flask import Flask, g, render_template
from flask_wtf.csrf import CSRFError
from config import Config
from extensions import csrf, db
from models import ROLE_LABELS

# Sidebar menu for each role: (label, endpoint, icon, other endpoints that keep it highlighted)
NAV = {
    "policyholder": [
        ("Dashboard", "dashboard.index", "bi-grid-1x2", []),
        ("My Policies", "policies.my_policies", "bi-shield-check", ["policies.detail"]),
        ("My Claims", "claims.my_claims", "bi-file-earmark-medical",
         ["claims.detail", "claims.edit_claim", "claims.respond"]),
        ("New Claim", "claims.new_claim", "bi-plus-circle", []),
        ("Activity Timeline", "extras.activity", "bi-clock-history", []),
        ("My Profile", "extras.profile", "bi-person-gear", []),
        ("Help Center", "extras.help_center", "bi-life-preserver", []),
    ],
    "hospital_desk": [
        ("Dashboard", "dashboard.index", "bi-grid-1x2", []),
        ("Hospital Claims", "hospital.claims", "bi-hospital",
         ["hospital.checklist", "claims.detail", "claims.edit_claim", "claims.respond"]),
        ("New Cashless Claim", "claims.new_claim", "bi-plus-circle", []),
        ("Activity Timeline", "extras.activity", "bi-clock-history", []),
        ("My Profile", "extras.profile", "bi-person-gear", []),
        ("Help Center", "extras.help_center", "bi-life-preserver", []),
    ],
    "claims_officer": [
        ("Dashboard", "dashboard.index", "bi-grid-1x2", []),
        ("Review Queue", "officer.queue", "bi-inboxes", ["officer.review", "claims.detail"]),
        ("Activity Timeline", "extras.activity", "bi-clock-history", []),
        ("My Profile", "extras.profile", "bi-person-gear", []),
        ("Help Center", "extras.help_center", "bi-life-preserver", []),
    ],
    "administrator": [
        ("Dashboard", "dashboard.index", "bi-grid-1x2", []),
        ("Users & Roles", "admin.users", "bi-people", []),
        ("Hospitals", "admin.hospitals", "bi-hospital", []),
        ("Insurance Plans", "admin.plans", "bi-card-checklist", []),
        ("Policies", "admin.policies", "bi-shield-plus", []),
        ("Settlements", "admin.settlements", "bi-cash-coin", ["admin.settle"]),
        ("Reports", "reports.index", "bi-bar-chart-line", []),
        ("Activity Timeline", "extras.activity", "bi-clock-history", []),
        ("My Profile", "extras.profile", "bi-person-gear", []),
        ("Help Center", "extras.help_center", "bi-life-preserver", []),
    ],
}


def inr(value):
    """Jinja filter: 500000 -> ₹5,00,000 (Indian digit grouping)."""
    try:
        number = Decimal(str(value if value is not None else 0))
    except Exception:
        number = Decimal(0)
    sign = "-" if number < 0 else ""
    whole, _, fraction = f"{abs(number):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}₹{whole}" + ("" if fraction == "00" else "." + fraction)


def create_app(config_class=Config):
    """Handle the create app operation."""
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    csrf.init_app(app)
    app.jinja_env.filters["inr"] = inr


    # Register every group of routes (blueprints)
    from routes import admin, auth, claims, dashboard, extras, hospital, officer, policies, reports
    for module in (auth, dashboard, policies, claims, hospital, officer, admin, reports, extras):
        app.register_blueprint(module.bp)

    @app.context_processor
    def inject_template_data():
        """Handle the inject template data operation."""
        user = getattr(g, "user", None)
        from models import Notification
        unread_notifications = (Notification.query.filter_by(recipient_user_id=user.id, is_read=False)
                                .order_by(Notification.created_at.desc()).limit(5).all()) if user else []
        return {"nav_items": NAV.get(user.role, []) if user else [], "ROLE_LABELS": ROLE_LABELS,
                "unread_notifications": unread_notifications}

    @app.after_request
    def security_headers(response):
        """Handle the security headers operation."""
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    # Friendly error pages
    def show_error(code, title, message):
        """Handle the show error operation."""
        return render_template("errors/error.html", code=code, title=title, message=message), code

    @app.errorhandler(403)
    def forbidden(_):
        """Handle the forbidden operation."""
        return show_error(403, "Access denied", "You do not have permission to open this page.")

    @app.errorhandler(404)
    def not_found(_):
        """Handle the not found operation."""
        return show_error(404, "Page not found", "The page you are looking for does not exist.")

    @app.errorhandler(CSRFError)
    def csrf_error(_):
        """Handle the csrf error operation."""
        return show_error(400, "Form expired", "Your form session expired. Go back, refresh the page and try again.")

    @app.errorhandler(500)
    def server_error(_):
        """Handle the server error operation."""
        db.session.rollback()
        return show_error(500, "Something went wrong", "An unexpected error occurred. Please try again.")

    return app


if __name__ == "__main__":
    application = create_app()
    with application.app_context():
        try:
            db.create_all()
            from services.schema_migration import migrate_additive_columns
            migrate_additive_columns(db.engine)
            from models import User
            from seed_data import seed_database
            if User.query.count() == 0:
                seed_database()
                application.logger.info("SQLite tables and fictional demo data initialized.")
        except Exception:
            db.session.rollback()
            application.logger.exception("Could not initialize the local database.")
            raise
    application.run(debug=False)
