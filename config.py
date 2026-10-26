"""Application configuration with safe local-development defaults."""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Configuration for the Flask application.

    Set SECRET_KEY and DATABASE_URL in .env to override these local defaults.
    The fallback secret is for local demos only; use a strong secret in deployment.
    """
    SECRET_KEY = os.getenv("SECRET_KEY", "local-demo-only-change-this-secret")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///health_claims.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"


class TestConfig(Config):
    """Configuration for automated tests."""
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
