"""
Django settings for the "SQL vs NoSQL in Django" project.

Every secret or machine-specific value is read from environment variables,
which python-dotenv loads from the .env file. The .env file is in .gitignore,
so passwords and keys never end up in the repository.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# --- Core Django -----------------------------------------------------------

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "core",            # shared API layer, home page, MongoDB connection (Member 1)
    "pokeapi_client",  # PokeAPI client with local cache (Member 4)
    "sql_api",         # PostgreSQL version of the API (Member 2)
    "mongo_api",       # MongoDB version of the API (Member 3)
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- PostgreSQL: used through Django's ORM -----------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "pokedex"),
        "USER": os.environ.get("POSTGRES_USER", "pokedex"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "pokedex"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,  # keep connections open between requests, like MongoClient does
    }
}

# --- MongoDB: used through PyMongo (see core/mongo.py) -----------------------

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "pokedex")
MONGO_TIMEOUT_MS = int(os.environ.get("MONGO_TIMEOUT_MS", "3000"))

# --- PokeAPI client ------------------------------------------------------------

POKEAPI = {
    "BASE_URL": os.environ.get("POKEAPI_BASE_URL", "https://pokeapi.co/api/v2/"),
    "CACHE_DIR": BASE_DIR / ".cache" / "pokeapi",
    "REQUEST_DELAY": float(os.environ.get("POKEAPI_REQUEST_DELAY", "0.2")),
    "TIMEOUT": 15,
    "USER_AGENT": "pokedex-sql-vs-nosql/1.0 (university group project)",
}

# --- Benchmark ------------------------------------------------------------------

BENCHMARK_RESULTS_DIR = BASE_DIR / "benchmark_results"

# --- Django REST framework ------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "UNICODE_JSON": True,
}

# --- Auth, i18n, static ------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"pokeapi_client": {"handlers": ["console"], "level": "INFO"}},
}
