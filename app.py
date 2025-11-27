import os
import json
from google.oauth2 import service_account

from datetime import datetime, date
from functools import wraps

from flask import (
    Flask,
    request,
    redirect,
    url_for,
    session,
    render_template_string,
    abort,
    flash,
    Response,
    current_app,
)
from google.cloud import firestore
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

# -------------------------------------------------------------------
# Configuration et initialisation
# -------------------------------------------------------------------

# 1. Chargement des variables d'environnement
load_dotenv()

# 2. Variables de sécurité
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "odile+++")
MASTER_PASSWORD = os.getenv("MASTER_PASSWORD", "odile+++")

# 3. Initialisation de l'application Flask
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-me")

# 4. Constantes de Collection
USERS_COLLECTION = "users"
CITIES_COLLECTION = "cities"
ALLOC_CONFIGS_COLLECTION = "allocationConfigs"
TRANSACTIONS_COLLECTION = "transactions"
EXPENSE_CATEGORIES_COLLECTION = "expenseCategories"
ALLOCATIONS_COLLECTION = "allocations"

# 5. Initialisation Firestore avec credentials explicites
creds_json_env = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
creds_file_env = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

if creds_json_env:
    # Cas déploiement (Render) : la clé JSON est dans une variable d'environnement
    try:
        creds_dict = json.loads(creds_json_env)
        credentials = service_account.Credentials.from_service_account_info(creds_dict)
        db_client = firestore.Client(credentials=credentials, project=creds_dict["project_id"])
    except json.JSONDecodeError:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS_JSON contient un JSON invalide.")

elif creds_file_env and os.path.exists(creds_file_env):
    # Cas local (Codespaces) : la variable pointe vers un fichier JSON
    try:
        with open(creds_file_env, "r") as f:
            creds_dict = json.load(f)
        credentials = service_account.Credentials.from_service_account_info(creds_dict)
        db_client = firestore.Client(credentials=credentials, project=creds_dict["project_id"])
    except FileNotFoundError:
        raise RuntimeError(f"Fichier de credentials non trouvé : {creds_file_env}")

else:
    # Rien n'est configuré : on stoppe tout avec un message explicite
    raise RuntimeError(
        "Aucun identifiant Firestore trouvé. "
        "Définis soit GOOGLE_APPLICATION_CREDENTIALS vers le fichier JSON (local), "
        "soit GOOGLE_APPLICATION_CREDENTIALS_JSON avec le contenu JSON (Render)."
    )

# Attacher le client DB à l'application Flask
app.config["FIRESTORE_DB"] = db_client


# -------------------------------------------------------------------
# Utilitaires généraux
# -------------------------------------------------------------------

# Utilitaire pour obtenir le client Firestore
def get_db():
    return app.config["FIRESTORE_DB"]

def utc_now_iso() -> str:
    return datetime.utcnow().isoformat()

def get_school_year_for_date(d: date) -> str:
    """Retourne l'année scolaire au format 'AAAA-AAAA'."""
    if d.month >= 9:
        start_year = d.year
        end_year = d.year + 1
    else:
        start_year = d.year - 1
        end_year = d.year
    return f"{start_year}-{end_year}"

def get_year_month(d: date) -> str:
    """Retourne le mois/année au format 'AAAA-MM'."""
    return f"{d.year:04d}-{d.month:02d}"

# -------------------------------------------------------------------
# Villes (Firestore)
# -------------------------------------------------------------------

def init_cities():
    db = get_db()
    cities = {
        "strasbourg": {
            "name": "Strasbourg",
            "schoolYearStartMonth": 9,
            "schoolYearStartDay": 1,
            "schoolYearEndMonth": 8,
            "schoolYearEndDay": 31,
            "createdAt": utc_now_iso(),
            "updatedAt": utc_now_iso(),
        },
        "colmar": {
            "name": "Colmar",
            "schoolYearStartMonth": 9,
            "schoolYearStartDay": 1,
            "schoolYearEndMonth": 8,
            "schoolYearEndDay": 31,
            "createdAt": utc_now_iso(),
            "updatedAt": utc_now_iso(),
        },
    }
    for cid, data in cities.items():
        doc_ref = db.collection(CITIES_COLLECTION).document(cid)
        if not doc_ref.get().exists:
            doc_ref.set(data)
            print(f"Ville créée : {cid}")

# -------------------------------------------------------------------
# Utilisateurs
# -------------------------------------------------------------------

def get_user_doc_ref(user_id: str):
    db = get_db()
    return db.collection(USERS_COLLECTION).document(user_id)

def get_user_by_login(login: str):
    db = get_db()
    docs = (
        db.collection(USERS_COLLECTION)
        .where("login", "==", login)
        .limit(1)
        .stream()
    )
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        return data
    return None

def get_user_by_id(user_id: str):
    doc = get_user_doc_ref(user_id).get()
    if doc.exists:
        data = doc.to_dict()
        data["id"] = doc.id
        return data
    return None

def create_user(
    user_id: str,
    full_name: str,
    short_name: str,
    login: str,
    city_id: str,
    role: str,
    temp_password: str | None = None,
    must_change_password: bool = True,
):
    now = utc_now_iso()
    if temp_password:
        password_hash = generate_password_hash(temp_password)
    else:
        password_hash = None

    data = {
        "fullName": full_name,
        "shortName": short_name,
        "cityId": city_id,
        "role": role,
        "active": True,
        "login": login,
        "passwordHash": password_hash,
        "mustChangePassword": must_change_password,
        "passwordSetAt": None,
        "lastLoginAt": None,
        "createdAt": now,
        "updatedAt": now,
    }
    get_user_doc_ref(user_id).set(data, merge=False)
    return data

def update_user(user_id: str, **fields):
    fields["updatedAt"] = utc_now_iso()
    get_user_doc_ref(user_id).update(fields)

# -------------------------------------------------------------------
# Catégories de dépenses
# -------------------------------------------------------------------

def get_active_expense_categories():
    """Catégories actives, triées par nom."""
    db = get_db()
    docs = (
        db.collection(EXPENSE_CATEGORIES_COLLECTION)
        .where("active", "==", True)
        .order_by("name")
        .stream()
    )
    categories = []
    for doc in docs:
        d = doc.to_dict()
        d["id"] = doc.id
        categories.append(d)
    return categories

# -------------------------------------------------------------------
# Allocation mensuelle (Configs)
# -------------------------------------------------------------------

def get_allocation_config(user_id: str, school_year: str):
    db = get_db()
    docs = (
        db.collection(ALLOC_CONFIGS_COLLECTION)
        .where("userId", "==", user_id)
        .where("schoolYear", "==", school_year)
        .where("active", "==", True)
        .limit(1)
        .stream()
    )
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        return data
    return None

def upsert_allocation_config(user_id: str, city_id: str, school_year: str, monthly_amount: float):
    db = get_db()
    existing = get_allocation_config(user_id, school_year)
    now = utc_now_iso()
    if existing:
        doc_ref = db.collection(ALLOC_CONFIGS_COLLECTION).document(existing["id"])
        doc_ref.update(
            {
                "monthlyAmount": monthly_amount,
                "updatedAt": now,
            }
        )
        return existing["id"]
    else:
        doc_ref = db.collection(ALLOC_CONFIGS_COLLECTION).document()
        doc_ref.set(
            {
                "userId": user_id,
                "cityId": city_id,
                "schoolYear": school_year,
                "monthlyAmount": monthly_amount,
                "active": True,
                "createdAt": now,
                "updatedAt": now,
            }
        )
        return doc_ref.id

# -------------------------------------------------------------------
# Transactions
# -------------------------------------------------------------------

def create_transaction(
    city_id: str,
    user_id: str | None,
    d: date,
    ttype: str,
    source: str,
    amount: float,
    payment_method: str | None,
    is_advance: bool,
    advance_status: str | None,
    description: str,
    category_id: str | None = None,
    category_name: str | None = None,
):
    db = get_db()
    now = utc_now_iso()
    school_year = get_school_year_for_date(d)
    year_month = get_year_month(d)
    doc_ref = db.collection(TRANSACTIONS_COLLECTION).document()
    data = {
        "cityId": city_id,
        "userId": user_id,
        "schoolYear": school_year,
        "yearMonth": year_month,
        "date": d.isoformat(),
        "type": ttype,
        "source": source,
        "amount": amount,
        "paymentMethod": payment_method,
        "isAdvance": is_advance,
        "advanceStatus": advance_status,
        "description": description,
        "categoryId": category_id,
        "categoryName": category_name,
        "createdAt": now,
        "updatedAt": now,
    }
    doc_ref.set(data)
    data["id"] = doc_ref.id
    return data

def get_city_annual_balance(city_id: str, school_year: str) -> float:
    db = get_db()
    docs = (
        db.collection(TRANSACTIONS_COLLECTION)
        .where("cityId", "==", city_id)
        .where("schoolYear", "==", school_year)
        .stream()
    )
    total = 0.0
    for doc in docs:
        data = doc.to_dict()
        total += float(data.get("amount", 0.0))
    return total

def get_personal_monthly_balance(user_id: str, year_month: str) -> float:
    db = get_db()
    docs = (
        db.collection(TRANSACTIONS_COLLECTION)
        .where("userId", "==", user_id)
        .where("yearMonth", "==", year_month)
        .stream()
    )
    total = 0.0
    for doc in docs:
        data = doc.to_dict()
        total += float(data.get("amount", 0.0))
    return total

def ensure_allocation_transaction_for_month(user, d: date):
    db = get_db()
    school_year = get_school_year_for_date(d)
    year_month = get_year_month(d)

    config = get_allocation_config(user["id"], school_year)
    if not config:
        return

    docs = (
        db.collection(TRANSACTIONS_COLLECTION)
        .where("userId", "==", user["id"])
        .where("cityId", "==", user["cityId"])
        .where("schoolYear", "==", school_year)
        .where("yearMonth", "==", year_month)
        .where("source", "==", "allocation_mensuelle")
        .limit(1)
        .stream()
    )
    
    # Vérification d'existence optimisée
    if any(True for _ in docs):
        return

    # Si non existante, on crée la transaction
    create_transaction(
        city_id=user["cityId"],
        user_id=user["id"],
        d=d,
        ttype="income",
        source="allocation_mensuelle",
        amount=float(config["monthlyAmount"]),
        payment_method="virement",
        is_advance=False,
        advance_status=None,
        description=f"Allocation mensuelle {year_month}",
    )

# -------------------------------------------------------------------
# Auth helpers & Decorateurs
# -------------------------------------------------------------------

def login_user(user_data: dict):
    session["user_id"] = user_data["id"]
    session["role"] = user_data["role"]
    session["city_id"] = user_data["cityId"]
    session["short_name"] = user_data.get("shortName") or user_data.get("fullName")
    # Mettre à jour l'heure de dernière connexion
    update_user(user_data["id"], lastLoginAt=utc_now_iso())


def logout_user():
    session.clear()

def current_user():
    uid = session.get("user_id")
    # L'admin n'a pas de document Firestore, le gérer à part
    if uid == "admin":
        return {"id": "admin", "role": "admin", "fullName": "Administrateur"}

    if not uid:
        return None
    
    user = get_user_by_id(uid)
    # Vérification de sécurité supplémentaire
    if user and user.get("role") == "admin":
        user["isAdmin"] = True
    return user

def require_login(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id"):
            flash("Veuillez vous connecter pour accéder à cette page.", "error")
            return redirect(url_for("login"))
        
        user = current_user()
        if user and user.get("mustChangePassword"):
             return redirect(url_for("change_password_first"))
             
        return f(*args, **kwargs)
    return decorated_function

def require_admin(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = current_user()
        if not user or user.get("role") != "admin":
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

def require_chef_or_admin(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = current_user()
        if not user or user["role"] not in ("chef", "admin"):
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

# -------------------------------------------------------------------
# HTML Template
# -------------------------------------------------------------------

BASE_LAYOUT = """
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>{{ title or "Comptabilité SMMD Alsace" }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">

  <link rel="manifest" href="{{ url_for('static', filename='manifest.json') }}">
  <meta name="theme-color" content="#0d6efd">

  <link
    href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"
    rel="stylesheet"
    integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH"
    crossorigin="anonymous"
  >
    <style>
    body {
      background: #f4f6fb;
    }
    .navbar-brand {
      font-weight: 600;
    }
  </style>
</head>

<body class="bg-light">

<nav class="navbar navbar-expand-lg navbar-dark bg-primary mb-4">
  <div class="container-fluid">
    <a class="navbar-brand" href="{{ url_for('dashboard') }}">Compta SMMD</a>

    <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarMain">
      <span class="navbar-toggler-icon"></span>
    </button>

    <div class="collapse navbar-collapse" id="navbarMain">
      <ul class="navbar-nav me-auto mb-2 mb-lg-0">

        {% if session.user_id %}

          {% if session.role == 'admin' %}
            <li class="nav-item">
              <a class="nav-link" href="{{ url_for('admin_transactions') }}">Admin compta</a>
            </li>
            <li class="nav-item">
              <a class="nav-link" href="{{ url_for('admin_users') }}">Admin utilisateurs</a>
            </li>
            <li class="nav-item">
              <a class="nav-link" href="{{ url_for('admin_categories') }}">Catégories dépenses</a>
            </li>

          {% else %}
            <li class="nav-item">
              <a class="nav-link" href="{{ url_for('dashboard') }}">Tableau de bord</a>
            </li>
            <li class="nav-item">
              <a class="nav-link" href="{{ url_for('income') }}">Recettes</a>
            </li>
            <li class="nav-item">
              <a class="nav-link" href="{{ url_for('expense') }}">Dépenses</a>
            </li>
            <li class="nav-item">
              <a class="nav-link" href="{{ url_for('my_operations') }}">Mes opérations</a>
            </li>

            {% if session.role == 'chef' %}
              <li class="nav-item">
                <a class="nav-link" href="{{ url_for('chef_city_transactions') }}">Compta maison</a>
              </li>
              <li class="nav-item">
                <a class="nav-link" href="{{ url_for('chef_advances') }}">Avances</a>
              </li>
              <li class="nav-item">
                <a class="nav-link" href="{{ url_for('chef_export') }}">Export ville</a>
              </li>
            {% endif %}
          {% endif %}

          <li class="nav-item">
            <a class="nav-link" href="{{ url_for('logout') }}">Déconnexion</a>
          </li>

        {% else %}
          <li class="nav-item">
            <a class="nav-link" href="{{ url_for('login') }}">Connexion</a>
          </li>
        {% endif %}
      </ul>

      {% if session.user_id %}
        <span class="navbar-text text-white">
          Bonjour {{ session.short_name }}
        </span>
      {% endif %}
    </div>
  </div>
</nav>

<div class="container my-4">

  {% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
      {% for category, msg in messages %}
        <div class="alert alert-{{ 'danger' if category == 'error' else 'success' }} alert-dismissible fade show" role="alert">
          {{ msg }}
          <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
        </div>
      {% endfor %}
    {% endif %}
  {% endwith %}

  {{ body|safe }}

</div>

<script
  src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"
  integrity="sha384-YvpcrYf0tY3lHB60NNkmXc5s9fDVZLESaAA55NDzOxhy9GkcIdslK1eN7N6jIeHz"
  crossorigin="anonymous"
></script>

<script>
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function() {
      navigator.serviceWorker.register("/service-worker.js")
        .then(() => console.log("SW registered"))
        .catch(error => console.log("SW registration failed:", error));
    });
  }
</script>

</body>
</html>
"""

def render_page(body, title="Comptabilité SMMD Alsace"):
    return render_template_string(BASE_LAYOUT, body=body, title=title)


# -------------------------------------------------------------------
# Routes: Authentification
# -------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    # Si déjà connecté, rediriger
    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    # Si on soumet le formulaire (POST)
    if request.method == "POST":
        ident = request.form.get("username", "").strip().lower()
        pwd = request.form.get("password", "")

        # 1. Connexion admin
        if ident == "admin":
            if pwd == ADMIN_PASSWORD:
                session["user_id"] = "admin"
                session["role"] = "admin"
                session["short_name"] = "Admin"
                return redirect(url_for("admin_transactions"))
            flash("Identifiant ou mot de passe incorrect.", "error")
            return redirect(url_for("login"))

        # 2. Connexion utilisateur (user / chef)
        db = get_db()
        q = db.collection(USERS_COLLECTION).where("login", "==", ident).limit(1)
        docs = list(q.stream())
        
        if not docs:
            flash("Identifiant ou mot de passe incorrect.", "error")
            return redirect(url_for("login"))

        user_doc = docs[0]
        user = user_doc.to_dict()
        user["id"] = user_doc.id

        pwd_ok = False
        
        # Gestion du premier login avec MASTER_PASSWORD (pour config initiale)
        if "passwordHash" not in user or not user["passwordHash"]:
            if pwd == MASTER_PASSWORD:
                # Premier login réussi avec MASTER_PASSWORD, on hache et on sauvegarde
                h = generate_password_hash(pwd)
                user_doc.reference.update({"passwordHash": h, "passwordSetAt": utc_now_iso(), "mustChangePassword": True})
                pwd_ok = True
            else:
                pwd_ok = False
        else:
            # Login normal
            pwd_ok = check_password_hash(user["passwordHash"], pwd)

        if not pwd_ok:
            flash("Identifiant ou mot de passe incorrect.", "error")
            return redirect(url_for("login"))

        # Connexion réussie
        login_user(user)

        if user.get("mustChangePassword"):
            flash("Veuillez définir votre mot de passe personnel.", "success")
            return redirect(url_for("change_password_first"))

        return redirect(url_for("dashboard"))

    # Si on arrive en GET : on affiche simplement le formulaire
    body = """
    <div class="row justify-content-center">
      <div class="col-md-6 col-lg-4">
        <div class="card shadow-sm border-0">
          <div class="card-header bg-primary text-white text-center">
            <h5 class="mb-0">Connexion</h5>
          </div>
          <div class="card-body">
            <form method="post">
              <div class="mb-3">
                <label class="form-label">Identifiant</label>
                <input class="form-control" name="username" required autocomplete="username">
              </div>
              <div class="mb-3">
                <label class="form-label">Mot de passe</label>
                <input class="form-control" name="password" type="password" required autocomplete="current-password">
              </div>
              <div class="d-grid">
                <button class="btn btn-primary">Se connecter</button>
              </div>
            </form>
          </div>
          <div class="card-footer text-muted small text-center">
            Comptabilité SMMD Alsace
          </div>
        </div>
      </div>
    </div>
    """
    return render_page(body, "Connexion")


@app.route("/logout")
def logout():
    logout_user()
    flash("Déconnecté.", "success")
    return redirect(url_for("login"))

@app.route("/change-password", methods=["GET", "POST"])
@require_login
def change_password_first():
    user = current_user()
    
    # Redirection si le mot de passe n'est pas requis à changer
    if not user.get("mustChangePassword"):
        flash("Votre mot de passe est déjà défini.", "info")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        pwd1 = request.form.get("password1", "")
        pwd2 = request.form.get("password2", "")
        if len(pwd1) < 6:
            flash("Le mot de passe doit contenir au moins 6 caractères.", "error")
        elif pwd1 != pwd2:
            flash("Les mots de passe ne correspondent pas.", "error")
        else:
            new_hash = generate_password_hash(pwd1)
            update_user(
                user["id"],
                passwordHash=new_hash,
                mustChangePassword=False,
                passwordSetAt=utc_now_iso(),
            )
            flash("Mot de passe mis à jour.", "success")
            return redirect(url_for("dashboard"))

    body = """
      <div class="row justify-content-center">
        <div class="col-md-6 col-lg-4">
          <div class="card shadow-sm border-warning border-4">
            <div class="card-header bg-warning-subtle text-dark text-center">
              <h5 class="mb-0">Définir un nouveau mot de passe</h5>
            </div>
            <div class="card-body">
              <p class="text-danger">Vous devez définir un mot de passe personnel pour continuer.</p>
              <form method="post">
                <div class="mb-3">
                  <label class="form-label">Nouveau mot de passe :</label>
                  <input class="form-control" type="password" name="password1" required>
                </div>
                <div class="mb-3">
                  <label class="form-label">Confirmer le mot de passe :</label>
                  <input class="form-control" type="password" name="password2" required>
                </div>
                <div class="d-grid">
                  <button class="btn btn-warning" type="submit">Valider</button>
                </div>
              </form>
            </div>
          </div>
        </div>
      </div>
    """
    return render_page(body, "Nouveau mot de passe")

# -------------------------------------------------------------------
# Tableau de bord
# -------------------------------------------------------------------

@app.route("/")
@require_login
def dashboard():
    user = current_user()
    
    if user["role"] == "admin":
        return redirect(url_for("admin_transactions"))

    today = date.today()
    year_month = get_year_month(today)
    
    # S'assurer que l'allocation mensuelle est créée pour le mois en cours
    ensure_allocation_transaction_for_month(user, today) 

    # Solde de la ville (sur toute la ville)
    db = get_db()
    city = user["cityId"]
    q = db.collection(TRANSACTIONS_COLLECTION).where("cityId", "==", city)
    total = 0.0
    for d in q.stream():
        tr = d.to_dict()
        amount = float(tr.get("amount", 0.0))
        if tr.get("type") == "income":
            total += amount
        else: # expense
            total -= amount

    # Solde perso du mois
    q2 = (
        db.collection(TRANSACTIONS_COLLECTION)
        .where("userId", "==", user["id"])
        .where("yearMonth", "==", year_month)
    )
    perso = 0.0
    for d in q2.stream():
        tr = d.to_dict()
        amount = float(tr.get("amount", 0.0))
        if tr.get("type") == "income":
            perso += amount
        else: # expense
            perso -= amount

    body = f"""
    <h1 class="mb-4">Tableau de bord</h1>

    <div class="row g-4">
      <div class="col-md-6">
        <div class="card border-0 shadow-sm">
          <div class="card-body">
            <h5 class="card-title">Solde de la ville</h5>
            <p class="text-muted mb-1">Ville : <strong>{city.capitalize()}</strong></p>
            <p class="display-6 mb-0">{total:.2f} €</p>
          </div>
        </div>
      </div>

      <div class="col-md-6">
        <div class="card border-0 shadow-sm">
          <div class="card-body">
            <h5 class="card-title">Votre solde du mois</h5>
            <p class="text-muted mb-1">Mois : <strong>{year_month}</strong></p>
            <p class="display-6 mb-3">{perso:.2f} €</p>
            <a href="{url_for('income')}" class="btn btn-outline-success btn-sm me-2">Recettes</a>
            <a href="{url_for('expense')}" class="btn btn-outline-danger btn-sm">Dépenses</a>
          </div>
        </div>
      </div>
    </div>
    """

    return render_page(body, "Tableau de bord")


# -------------------------------------------------------------------
# Recettes
# -------------------------------------------------------------------
@app.route("/income", methods=["GET", "POST"])
@require_login
def income():
    db = get_db()
    user = current_user()

    today = date.today()
    year_month = get_year_month(today)
    school_year = get_school_year_for_date(today)

    # 1) Récupération configuration allocation existante
    config = get_allocation_config(user["id"], school_year)
    allocation_amount = float(config.get("monthlyAmount", 0.0)) if config else 0.0

    # -------------------------------------------------------------------
    # POST : Mise à jour allocation ou ajout recette ponctuelle
    # -------------------------------------------------------------------
    if request.method == "POST":
        action = request.form.get("action")

        # --- Mise à jour allocation mensuelle ---
        if action == "set_allocation":
            if user["role"] not in ("chef", "admin"):
                flash("Vous n'êtes pas autorisé à modifier l'allocation.", "error")
                return redirect(url_for("income"))
                
            try:
                # Remplacement des virgules pour permettre la saisie décimale
                new_amount = float(request.form.get("allocation_amount").replace(",", "."))
                if new_amount < 0:
                    raise ValueError("Montant négatif")
            except Exception:
                flash("Montant d’allocation invalide.", "error")
                return redirect(url_for("income"))

            # 1) Mettre à jour / créer la configuration d'allocation
            upsert_allocation_config(
                user_id=user["id"], 
                city_id=user["cityId"], 
                school_year=school_year, 
                monthly_amount=new_amount
            )

            # 2) Mise à jour / Création des transactions pour les mois restants/actuels (simplifié)
            # On considère que la mise à jour s'applique à tous les mois de l'année scolaire
            # à partir de maintenant, ainsi qu'aux transactions existantes.
            start_year, end_year = map(int, school_year.split("-"))
            months_schedule = (
                [(start_year, m) for m in range(9, 13)] + 
                [(end_year, m) for m in range(1, 9)]
            )
            
            # Mise à jour de la date au 1er du mois pour la recherche
            current_date_for_update = date(today.year, today.month, 1) 

            for y, m in months_schedule:
                month_date = date(y, m, 1)
                ym_str = f"{y:04d}-{m:02d}"

                # Ne traiter que les mois à partir du mois courant ou futur
                if month_date >= current_date_for_update:
                    tx_q = (
                        db.collection(TRANSACTIONS_COLLECTION)
                        .where("userId", "==", user["id"])
                        .where("cityId", "==", user["cityId"])
                        .where("type", "==", "income")
                        .where("source", "==", "allocation_mensuelle")
                        .where("yearMonth", "==", ym_str)
                        .limit(1)
                        .stream()
                    )
                    tx_docs = list(tx_q)

                    if tx_docs:
                        # Mettre à jour l'allocation existante
                        tx_docs[0].reference.update(
                            {
                                "amount": new_amount,
                                "date": month_date.isoformat(),
                                "updatedAt": utc_now_iso(),
                            }
                        )
                    else:
                        # Créer la transaction si elle n'existe pas
                        create_transaction(
                            city_id=user["cityId"],
                            user_id=user["id"],
                            d=month_date,
                            ttype="income",
                            source="allocation_mensuelle",
                            amount=new_amount,
                            payment_method="virement",
                            is_advance=False,
                            advance_status=None,
                            description=f"Allocation mensuelle {ym_str}",
                        )

            flash(
                "Allocation mensuelle mise à jour à partir de ce mois pour le reste de l'année scolaire.",
                "success",
            )
            return redirect(url_for("income"))

        # --- Recette ponctuelle ---
        if action == "add_extra_income":
            try:
                amount = float(request.form.get("amount").replace(",", "."))
                if amount <= 0:
                     raise ValueError("Montant doit être positif")
            except Exception:
                flash("Montant invalide.", "error")
                return redirect(url_for("income"))

            desc = request.form.get("description", "").strip() or "Recette ponctuelle"

            create_transaction(
                city_id=user["cityId"],
                user_id=user["id"],
                d=today,
                ttype="income",
                source="recette_ponctuelle",
                amount=amount, # abs(amount) non nécessaire car vérifié au-dessus
                payment_method=None,
                is_advance=False,
                advance_status=None,
                description=desc,
                category_id=None,
                category_name=None,
            )

            flash("Recette ponctuelle ajoutée.", "success")
            return redirect(url_for("income"))

    # -------------------------------------------------------------------
    # GET : Affichage des recettes du mois
    # -------------------------------------------------------------------
    q = (
        db.collection(TRANSACTIONS_COLLECTION)
        .where("cityId", "==", user["cityId"])
        .where("userId", "==", user["id"])
        .where("type", "==", "income") # type corrigé de ttype à type dans la query
        .where("yearMonth", "==", year_month)
        .order_by("date", direction=firestore.Query.DESCENDING) # Tri par date descendante
    )
    docs = q.stream()

    month_incomes = []
    total_income = 0.0
    total_alloc = 0.0

    for d in docs:
        data = d.to_dict()
        amount = float(data.get("amount", 0.0))
        is_allocation = data.get("source") == "allocation_mensuelle"
        total_income += amount
        if is_allocation:
            total_alloc += amount

        month_incomes.append(
            {
                "date": data.get("date", ""),
                "amount": amount,
                "isAllocation": is_allocation,
                "description": data.get("description", ""),
            }
        )
    
    # Inverser l'ordre des transactions pour un affichage ascendant par défaut dans le tableau
    month_incomes.reverse() 

    total_extra = total_income - total_alloc

    # -------------------------------------------------------------------
    # Interface HTML
    # -------------------------------------------------------------------
    rows_html = ""
    for tr in month_incomes:
        badge = (
            '<span class="badge bg-success-subtle text-success border border-success-subtle">Allocation</span>'
            if tr["isAllocation"]
            else '<span class="badge bg-primary-subtle text-primary border border-primary-subtle">Ponctuelle</span>'
        )
        rows_html += f"""
        <tr>
            <td>{tr["date"]}</td>
            <td>{badge}</td>
            <td>{tr["description"]}</td>
            <td class="text-end">{tr["amount"]:.2f} €</td>
        </tr>
        """

    # Début du corps HTML de la page
    body = f"""
    <h1 class="mb-4">Recettes ({year_month})</h1>

    <div class="row g-4 mb-4">
      <div class="col-md-4">
        <div class="card text-center bg-success text-white shadow-sm">
          <div class="card-body">
            <h6 class="card-title">Total Recettes du mois</h6>
            <p class="fs-4 mb-0">{total_income:.2f} €</p>
          </div>
        </div>
      </div>
      <div class="col-md-4">
        <div class="card text-center bg-success-subtle border-success shadow-sm">
          <div class="card-body">
            <h6 class="card-title text-success">Allocation mensuelle</h6>
            <p class="fs-4 mb-0">{total_alloc:.2f} €</p>
          </div>
        </div>
      </div>
      <div class="col-md-4">
        <div class="card text-center bg-primary-subtle border-primary shadow-sm">
          <div class="card-body">
            <h6 class="card-title text-primary">Recettes ponctuelles</h6>
            <p class="fs-4 mb-0">{total_extra:.2f} €</p>
          </div>
        </div>
      </div>
    </div>
    
    <h2>Configuration de l'allocation annuelle ({school_year})</h2>
    <div class="card shadow-sm mb-5">
        <div class="card-body">
            <form method="post">
                <input type="hidden" name="action" value="set_allocation">
                <div class="row g-3 align-items-end">
                    <div class="col-md-6">
                        <label class="form-label">Montant mensuel de l'allocation (€)</label>
                        <input 
                            type="text" 
                            name="allocation_amount" 
                            class="form-control" 
                            value="{allocation_amount:.2f}".replace('.', ',') 
                            required
                            {'disabled' if user["role"] not in ("chef", "admin") else ''}
                        >
                    </div>
                    <div class="col-md-6">
                        <button 
                            type="submit" 
                            class="btn btn-primary w-100"
                            {'disabled' if user["role"] not in ("chef", "admin") else ''}
                        >
                            Enregistrer l'allocation
                        </button>
                    </div>
                </div>
            </form>
            
            {% if session.role not in ("chef", "admin") %}
            <p class="small text-muted mt-2">Seul un chef de maison ou un administrateur peut modifier ce montant.</p>
            {% endif %}
            
        </div>
    </div>


    <h2>Ajouter une recette ponctuelle</h2>
    <div class="card shadow-sm mb-5">
        <div class="card-body">
            <form method="post">
                <input type="hidden" name="action" value="add_extra_income">
                <div class="row g-3">
                    <div class="col-md-4">
                        <label class="form-label">Montant reçu (€)</label>
                        <input type="text" name="amount" class="form-control" required placeholder="Ex: 50.00">
                    </div>
                    <div class="col-md-8">
                        <label class="form-label">Description</label>
                        <input type="text" name="description" class="form-control" placeholder="Ex: Don de Mme Dupont">
                    </div>
                    <div class="col-12">
                        <button type="submit" class="btn btn-success">Ajouter la recette</button>
                    </div>
                </div>
            </form>
        </div>
    </div>

    <h2>Détail des recettes du mois ({year_month})</h2>
    <div class="card shadow-sm">
        <div class="card-body p-0">
            <div class="table-responsive">
                <table class="table table-striped table-hover mb-0">
                    <thead>
                        <tr>
                            <th>Date</th>
                            <th>Type</th>
                            <th>Description</th>
                            <th class="text-end">Montant</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows_html}
                    </tbody>
                </table>
            </div>
            {% if not month_incomes %}
                <p class="text-center p-3 text-muted mb-0">Aucune recette enregistrée pour le mois de {year_month}.</p>
            {% endif %}
        </div>
    </div>
    """

    return render_page(body, "Recettes")

# -------------------------------------------------------------------
# Route d'Admin (Exemple)
# -------------------------------------------------------------------

@app.route("/admin/transactions")
@require_admin
def admin_transactions():
    body = "<h1>Admin Compta</h1><p>Vue de toutes les transactions...</p>"
    return render_page(body, "Admin Compta")

@app.route("/admin/users")
@require_admin
def admin_users():
    body = "<h1>Admin Utilisateurs</h1><p>Gestion des utilisateurs...</p>"
    return render_page(body, "Admin Utilisateurs")

@app.route("/admin/categories")
@require_admin
def admin_categories():
    body = "<h1>Admin Catégories</h1><p>Gestion des catégories de dépenses...</p>"
    # Exemple d'utilisation de la fonction utilitaire
    categories = get_active_expense_categories()
    
    table_rows = ""
    for cat in categories:
        table_rows += f"<tr><td>{cat['id']}</td><td>{cat['name']}</td></tr>"

    body = f"""
    <h1>Admin Catégories de Dépenses</h1>
    <table class="table table-striped">
        <thead><tr><th>ID</th><th>Nom</th></tr></thead>
        <tbody>{table_rows}</tbody>
    </table>
    """
    return render_page(body, "Admin Catégories")


# -------------------------------------------------------------------
# Lancement de l'application
# -------------------------------------------------------------------

if __name__ == "__main__":
    # Initialisation des données de base au démarrage si nécessaire
    with app.app_context():
        init_cities()
        
    app.run(debug=True)