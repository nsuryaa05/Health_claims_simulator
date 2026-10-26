"""Login, sign-up, logout + the decorators that protect every other page."""
import re
from functools import wraps

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import ROLES, Hospital, User

bp = Blueprint("auth", __name__)
USERNAME_PATTERN = re.compile(r"^[a-z0-9_]{3,30}$")
ROLE_DISPLAY_NAMES = {
    "policyholder": "Policyholder",
    "hospital_desk": "Hospital Desk",
    "claims_officer": "Claims Officer",
    "administrator": "Administrator",
}


@bp.before_app_request
def load_current_user():
    """Runs before every request: puts the logged-in user (or None) into g.user."""
    user_id = session.get("user_id")
    g.user = db.session.get(User, user_id) if user_id else None


def role_required(*roles):
    """Allow a page only for the given roles. This is the REAL security check."""
    def decorator(view):
        """Apply the access-control wrapper to a view function."""
        @wraps(view)
        def wrapped(*args, **kwargs):
            """Run the wrapped view after checking access permissions."""
            if g.user is None:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("auth.login"))
            if g.user.role not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def login_required(view):
    """Handle the login required operation."""
    return role_required(*ROLES)(view)


def validate_new_account(form):
    """Shared by public sign-up and the admin 'create user' form."""
    errors = []
    name = form.get("display_name", "").strip()
    username = form.get("username", "").strip().lower()
    password = form.get("password", "")
    if len(name) < 2 or len(name) > 100:
        errors.append("Full name must be 2 to 100 characters.")
    if not USERNAME_PATTERN.match(username):
        errors.append("Username must be 3-30 characters: lowercase letters, numbers and underscore only.")
    elif User.query.filter_by(username=username).first():
        errors.append("That username is already taken.")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    if "confirm_password" in form and password != form.get("confirm_password"):
        errors.append("Passwords do not match.")
    return errors


@bp.route("/")
def home():
    """Always show the login page when the application root is opened."""
    # The root URL is the public entry point. Clear an existing browser session
    # so visiting http://127.0.0.1:5000/ always requires fresh login credentials.
    session.clear()
    g.user = None
    return redirect(url_for("auth.login"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    """Authenticate a username and password; load the assigned role from the database."""
    if g.user:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        try:
            user = User.query.filter_by(username=username).first()
            # The stored role comes from the database; login never trusts a role field.
            if user and user.check_password(password):
                session.clear()
                session["user_id"] = user.id
                flash("Welcome back, %s!" % user.display_name, "success")
                return redirect(url_for("dashboard.index"))
            flash("Invalid username or password.", "danger")
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Database error while signing in")
            flash("We could not sign you in right now. Please try again.", "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Unexpected login error")
            flash("An unexpected error occurred while signing in.", "danger")

    return render_template("auth/login.html")


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    """Create an account using the role selected in the demo sign-up form.

    This role-selection flow is intended for a classroom simulator only.
    A production insurance system should create privileged accounts through
    an administrator or an invitation process.
    """
    if g.user:
        return redirect(url_for("dashboard.index"))

    hospitals = Hospital.query.order_by(Hospital.name).all()

    if request.method == "POST":
        errors = validate_new_account(request.form)
        selected_role = request.form.get("role", "").strip()
        hospital_id = request.form.get("hospital_id", "").strip()

        if selected_role not in ROLES:
            errors.append("Please select a valid role.")

        selected_hospital = None
        if selected_role == "hospital_desk":
            if not hospital_id:
                errors.append("Please select the hospital you work for.")
            else:
                try:
                    selected_hospital = db.session.get(Hospital, int(hospital_id))
                except (TypeError, ValueError):
                    selected_hospital = None
                if selected_hospital is None:
                    errors.append("Please select a valid hospital.")

        if errors:
            for message in errors:
                flash(message, "danger")
        else:
            try:
                user = User(
                    username=request.form["username"].strip().lower(),
                    display_name=request.form["display_name"].strip(),
                    role=selected_role,
                    hospital_id=selected_hospital.id if selected_hospital else None,
                )
                user.set_password(request.form["password"])
                db.session.add(user)
                db.session.commit()

                # Sign in the newly created account so the selected role opens
                # its own dashboard immediately after successful registration.
                session.clear()
                session["user_id"] = user.id
                flash(
                    "Account created successfully. Welcome to your %s dashboard!"
                    % ROLE_DISPLAY_NAMES.get(selected_role, selected_role.replace("_", " ").title()),
                    "success",
                )
                return redirect(url_for("dashboard.index"))
            except SQLAlchemyError:
                db.session.rollback()
                current_app.logger.exception("Database error while creating an account")
                flash("We could not create your account. Please try again.", "danger")
            except Exception:
                db.session.rollback()
                current_app.logger.exception("Unexpected sign-up error")
                flash("An unexpected error occurred while creating your account.", "danger")

    return render_template("auth/signup.html", hospitals=hospitals)


@bp.route("/logout", methods=["POST"])
def logout():
    """Handle the logout operation."""
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
