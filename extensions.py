"""Shared extension objects. They are created here (empty) and connected to the
Flask app inside create_app() in app.py. Keeping them in their own file avoids
circular imports between app.py, models.py and the route files."""
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()   # database (ORM)
csrf = CSRFProtect()  # protects every POST form against CSRF attacks
