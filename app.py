import os
import csv
import io
import json
from google.oauth2 import service_account

from datetime import datetime, date

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
)
from google.cloud import firestore
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "odile+++")
MASTER_PASSWORD = os.getenv("MASTER_PASSWORD", "odile+++")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-me")
# -------------------------------------------------------------------
# Configuration générale
# -------------------------------------------------------------------

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-me")

# -------------------------------------------------------------------
# Initialisation Firestore avec credentials explicites
# -------------------------------------------------------------------
creds_json_env = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
creds_file_env = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

if creds_json_env:
    # Cas déploiement (Render) : la clé JSON est dans une variable d'environnement
    creds_dict = json.loads(creds_json_env)
    credentials = service_account.Credentials.from_service_account_info(creds_dict)
    db = firestore.Client(credentials=credentials, project=creds_dict["project_id"])

elif creds_file_env and os.path.exists(creds_file_env):
    # Cas local (Codespaces) : la variable pointe vers un fichier JSON
    with open(creds_file_env, "r") as f:
        creds_dict = json.load(f)
    credentials = service_account.Credentials.from_service_account_info(creds_dict)
    db = firestore.Client(credentials=credentials, project=creds_dict["project_id"])

else:
    # Rien n'est configuré : on stoppe tout avec un message explicite
    raise RuntimeError(
        "Aucun identifiant Firestore trouvé. "
        "Définis soit GOOGLE_APPLICATION_CREDENTIALS vers le fichier JSON (local), "
        "soit GOOGLE_APPLICATION_CREDENTIALS_JSON avec le contenu JSON (Render)."
)

USERS_COLLECTION = "users"
CITIES_COLLECTION = "cities"
ALLOC_CONFIGS_COLLECTION = "allocationConfigs"
TRANSACTIONS_COLLECTION = "transactions"
EXPENSE_CATEGORIES_COLLECTION = "expenseCategories"
ALLOCATIONS_COLLECTION = "allocations"

# -------------------------------------------------------------------
# Utilitaires généraux
# -------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.utcnow().isoformat()

def get_school_year_for_date(d: date) -> str:
    if d.month >= 9:
        start_year = d.year
        end_year = d.year + 1
    else:
        start_year = d.year - 1
        end_year = d.year
    return f"{start_year}-{end_year}"

def get_year_month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"

# -------------------------------------------------------------------
# Villes (Firestore)
# -------------------------------------------------------------------

def init_cities():
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
    return db.collection(USERS_COLLECTION).document(user_id)

def get_user_by_login(login: str):
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
# Allocation mensuelle
# -------------------------------------------------------------------

def get_allocation_config(user_id: str, school_year: str):
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
    already = False
    for _ in docs:
        already = True
        break

    if not already:
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
# Auth helpers
# -------------------------------------------------------------------

def login_user(user_data: dict):
    session["user_id"] = user_data["id"]
    session["role"] = user_data["role"]
    session["city_id"] = user_data["cityId"]
    session["short_name"] = user_data.get("shortName") or user_data.get("fullName")

def logout_user():
    session.clear()

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return get_user_by_id(uid)
def get_school_year(d: date) -> str:
    """
    Retourne l'année scolaire au format 'AAAA-AAAA'.
    Exemple : si d = 2025-11, retourne '2025-2026'.
    """
    year = d.year
    if d.month >= 9:
        return f"{year}-{year+1}"
    else:
        return f"{year-1}-{year}"

def require_login():
    if not session.get("user_id"):
        return redirect(url_for("login"))

def require_admin():
    user = current_user()
    if not user or user["role"] != "admin":
        abort(403)

def require_chef_or_admin():
    user = current_user()
    if not user or user["role"] not in ("chef", "admin"):
        abort(403)

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

  <!-- PWA -->
  <link rel="manifest" href="{{ url_for('static', filename='manifest.json') }}">
  <meta name="theme-color" content="#0d6efd">

  <!-- Bootstrap CSS -->
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

<!-- Bootstrap JS -->
<script
  src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"
  integrity="sha384-YvpcrYf0tY3lHB60NNkmXc5s9fDVZLESaAA55NDzOxhy9GkcIdslK1eN7N6jIeHz"
  crossorigin="anonymous"
></script>

<!-- Service Worker -->
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
    # Si on soumet le formulaire (POST)
    if request.method == "POST":
        ident = request.form.get("username", "").strip().lower()
        pwd = request.form.get("password", "")

        # Connexion admin
        if ident == "admin":
            if pwd == ADMIN_PASSWORD:
                session["user_id"] = "admin"
                session["role"] = "admin"
                session["short_name"] = "Admin"
                return redirect(url_for("admin_transactions"))
            flash("Identifiant ou mot de passe incorrect.", "error")
            return redirect(url_for("login"))

        # Connexion utilisateur (user / chef)
        q = db.collection(USERS_COLLECTION).where("login", "==", ident).limit(1)
        docs = list(q.stream())
        if not docs:
            flash("Identifiant incorrect.", "error")
            return redirect(url_for("login"))

        user_doc = docs[0]
        user = user_doc.to_dict()

        # Premier login sans mot de passe défini
        if "passwordHash" not in user:
            if pwd == MASTER_PASSWORD:
                h = generate_password_hash(MASTER_PASSWORD)
                user_doc.reference.update({"passwordHash": h})
                pwd_ok = True
            else:
                pwd_ok = False
        else:
            pwd_ok = check_password_hash(user["passwordHash"], pwd)

        if not pwd_ok:
            flash("Identifiant ou mot de passe incorrect.", "error")
            return redirect(url_for("login"))

        # Connexion réussie
        session["user_id"] = user_doc.id
        session["role"] = user.get("role", "user")
        session["short_name"] = user.get("shortName", user.get("fullName", ident))

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
def change_password_first():
    user = current_user()
    if not user:
        return redirect(url_for("login"))

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
      <h1>Définir un nouveau mot de passe</h1>
      <p>Vous devez définir un mot de passe personnel pour continuer.</p>
      <form method="post">
        <div>
          <label>Nouveau mot de passe :</label><br>
          <input type="password" name="password1" required>
        </div>
        <div>
          <label>Confirmer le mot de passe :</label><br>
          <input type="password" name="password2" required>
        </div>
        <button type="submit">Valider</button>
      </form>
    """
    return render_page(body, "Nouveau mot de passe")

# -------------------------------------------------------------------
# Tableau de bord
# -------------------------------------------------------------------

@app.route("/")
def dashboard():
    if not session.get("user_id") or session.get("role") == "admin":
        return redirect(url_for("login"))

    user = current_user()
    today = date.today()
    year_month = get_year_month(today)
    school_year = get_school_year_for_date(today)

    # Solde de la ville
    city = user["cityId"]
    q = db.collection(TRANSACTIONS_COLLECTION).where("cityId", "==", city)
    total = 0
    for d in q.stream():
        tr = d.to_dict()
        if tr["type"] == "income":
            total += tr["amount"]
        else:
            total -= tr["amount"]

    # Solde perso du mois
    q2 = (
        db.collection(TRANSACTIONS_COLLECTION)
        .where("userId", "==", user["id"])
        .where("yearMonth", "==", year_month)
    )
    perso = 0
    for d in q2.stream():
        tr = d.to_dict()
        if tr["type"] == "income":
            perso += tr["amount"]
        else:
            perso -= tr["amount"]

    body = f"""
    <h1 class="mb-4">Tableau de bord</h1>

    <div class="row g-4">
      <div class="col-md-6">
        <div class="card border-0 shadow-sm">
          <div class="card-body">
            <h5 class="card-title">Solde de la ville</h5>
            <p class="text-muted mb-1">Ville : <strong>{city}</strong></p>
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
def income():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    user = current_user()
    if not user:
        return redirect(url_for("login"))

    today = date.today()
    year_month = today.strftime("%Y-%m")
    school_year = get_school_year(today)

    # 1) Récupération allocation existante (doc de référence)
    alloc_q = (
        db.collection(ALLOCATIONS_COLLECTION)
        .where("userId", "==", user["id"])
        .where("schoolYear", "==", school_year)
        .limit(1)
        .stream()
    )
    alloc_docs = list(alloc_q)
    allocation_amount = 0.0

    if alloc_docs:
        allocation_amount = float(alloc_docs[0].to_dict().get("amount", 0.0))

    # -------------------------------------------------------------------
    # POST : Mise à jour allocation ou ajout recette ponctuelle
    # -------------------------------------------------------------------
    if request.method == "POST":
        action = request.form.get("action")

        # --- Mise à jour allocation ---
        if action == "set_allocation":
            try:
                new_amount = float(
                    request.form.get("allocation_amount").replace(",", ".")
                )
            except Exception:
                flash("Montant d’allocation invalide.", "error")
                return redirect(url_for("income"))

            has_existing = bool(alloc_docs)

            # Mise à jour (création si première fois)
            if has_existing:
                alloc_docs[0].reference.update(
                    {
                        "amount": new_amount,
                        "updatedAt": datetime.utcnow().isoformat(),
                    }
                )
            else:
                db.collection(ALLOCATIONS_COLLECTION).add(
                    {
                        "userId": user["id"],
                        "cityId": user["cityId"],
                        "amount": new_amount,
                        "schoolYear": school_year,
                        "createdAt": datetime.utcnow().isoformat(),
                    }
                )

            # --- Synchroniser avec les transactions mensuelles ---
            # Construction de la liste des mois de l'année scolaire
            # ex. "2024-2025" -> (2024,9..12) + (2025,1..8)
            start_year, end_year = map(int, school_year.split("-"))
            months_schedule = (
                [(start_year, m) for m in range(9, 13)]
                + [(end_year, m) for m in range(1, 9)]
            )

            current_ym_int = today.year * 100 + today.month

            for y, m in months_schedule:
                ym_int = y * 100 + m
                ym_str = f"{y:04d}-{m:02d}"
                month_date = date(y, m, 1)

                # Si c'est la première allocation -> on crée pour toute l'année scolaire
                # Si c'est une modification -> on met à jour à partir du mois en cours
                if (not has_existing) or (ym_int >= current_ym_int):
                    tx_q = (
                        db.collection(TRANSACTIONS_COLLECTION)
                        .where("userId", "==", user["id"])
                        .where("cityId", "==", user["cityId"])
                        .where("ttype", "==", "income")
                        .where("source", "==", "allocation_mensuelle")
                        .where("yearMonth", "==", ym_str)
                        .limit(1)
                        .stream()
                    )
                    tx_docs = list(tx_q)

                    if tx_docs:
                        # Mettre à jour le montant (et la date au 1er du mois)
                        tx_docs[0].reference.update(
                            {
                                "amount": new_amount,
                                "date": month_date.isoformat(),
                                "schoolYear": school_year,
                                "updatedAt": datetime.utcnow().isoformat(),
                            }
                        )
                    else:
                        # Créer une transaction d'allocation pour ce mois
                        create_transaction(
                            city_id=user["cityId"],
                            user_id=user["id"],
                            d=month_date,
                            ttype="income",
                            source="allocation_mensuelle",
                            amount=new_amount,
                            payment_method=None,
                            is_advance=False,
                            advance_status=None,
                            description="Allocation mensuelle",
                            category_id=None,
                            category_name=None,
                        )

            flash("Allocation mensuelle mise à jour.", "success")
            return redirect(url_for("income"))

        # --- Recette ponctuelle ---
        if action == "add_extra_income":
            try:
                amount = float(request.form.get("amount").replace(",", "."))
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
                amount=abs(amount),
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
    # Récupération des recettes du mois (pour l'affichage)
    # -------------------------------------------------------------------
    q = (
        db.collection(TRANSACTIONS_COLLECTION)
        .where("cityId", "==", user["cityId"])
        .where("userId", "==", user["id"])
        .where("ttype", "==", "income")
        .where("yearMonth", "==", year_month)
        .order_by("date")
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

    body = f"""
    <h1 class="mb-4">Recettes</h1>

    <div class="row g-4">
      <!-- Allocation mensuelle -->
      <div class="col-lg-6">
        <div class="card shadow-sm border-0">
          <div class="card-header bg-success text-white">
            <h5 class="mb-0">Allocation mensuelle</h5>
          </div>
          <div class="card-body">

            <p class="text-muted">
              Année scolaire : <strong>{school_year}</strong><br>
              Mois en cours : <strong>{year_month}</strong>
            </p>

            <form method="post">
              <input type="hidden" name="action" value="set_allocation">

              <div class="mb-3">
                <label class="form-label">Montant mensuel actuel</label>
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  class="form-control"
                  name="allocation_amount"
                  value="{allocation_amount:.2f}"
                  required
                >
                <div class="form-text">
                  La modification s’applique au mois en cours et à tous les mois suivants de l’année scolaire.
                </div>
              </div>

              <div class="d-grid">
                <button class="btn btn-success">Mettre à jour</button>
              </div>
            </form>

          </div>
        </div>
      </div>

      <!-- Recette ponctuelle -->
      <div class="col-lg-6">
        <div class="card shadow-sm border-0">
          <div class="card-header bg-primary text-white">
            <h5 class="mb-0">Recette ponctuelle</h5>
          </div>
          <div class="card-body">
            <form method="post">
              <input type="hidden" name="action" value="add_extra_income">

              <div class="mb-3">
                <label class="form-label">Montant</label>
                <input type="number" step="0.01" min="0" name="amount" class="form-control" required>
              </div>

              <div class="mb-3">
                <label class="form-label">Description</label>
                <input type="text" name="description" class="form-control" placeholder="Ex : don, remboursement...">
              </div>

              <div class="d-grid">
                <button class="btn btn-primary">Ajouter la recette</button>
              </div>
            </form>
          </div>
        </div>
      </div>
    </div>

    <hr class="my-4">

    <div class="row g-4 mb-3">
      <div class="col-lg-5">
        <div class="card shadow-sm border-0">
          <div class="card-body">
            <h5 class="card-title mb-3">Résumé du mois {year_month}</h5>
            <p class="mb-1">Total des recettes : <strong>{total_income:.2f} €</strong></p>
            <p class="mb-1">Dont allocations : <strong>{total_alloc:.2f} €</strong></p>
            <p class="mb-0">Recettes ponctuelles : <strong>{total_extra:.2f} €</strong></p>
          </div>
        </div>
      </div>
    </div>

    <h2 class="h5 mb-3">Détail des recettes du mois {year_month}</h2>

    <div class="table-responsive">
      <table class="table table-sm align-middle">
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
    """

    return render_page(body, "Recettes")




# -------------------------------------------------------------------
# Dépenses (avec catégories)
# -------------------------------------------------------------------

@app.route("/expense", methods=["GET", "POST"])
def expense():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    user = current_user()
    if not user:
        return redirect(url_for("login"))

    today = date.today()
    categories = get_active_expense_categories()

    if request.method == "POST":
        form_type = request.form.get("form_type")
        desc = request.form.get("description", "").strip() or "Dépense"
        category_id = request.form.get("category_id") or None
        category_name = None

        if category_id:
            cat_doc = db.collection(EXPENSE_CATEGORIES_COLLECTION).document(category_id).get()
            if cat_doc.exists:
                category_name = cat_doc.to_dict().get("name")

        try:
            amount = float(request.form.get("amount").replace(",", "."))
        except Exception:
            flash("Montant invalide.", "error")
            return redirect(url_for("expense"))

        if amount <= 0:
            flash("Le montant doit être positif.", "error")
            return redirect(url_for("expense"))

        # Toujours stocké comme montant négatif (dépense)
        amount = -abs(amount)

        if form_type == "cb_ville":
            create_transaction(
                city_id=user["cityId"],
                user_id=user["id"],
                d=today,
                ttype="expense",
                source="depense_carte_ville",
                amount=amount,
                payment_method="carte_ville",
                is_advance=False,
                advance_status=None,
                description=desc,
                category_id=category_id,
                category_name=category_name,
            )
            flash("Dépense CB maison enregistrée.", "success")

        elif form_type == "avance":
            payment_method = request.form.get("payment_method")
            create_transaction(
                city_id=user["cityId"],
                user_id=user["id"],
                d=today,
                ttype="expense",
                source="avance_frais_personnelle",
                amount=amount,
                payment_method=payment_method,
                is_advance=True,
                advance_status="en_attente",
                description=desc,
                category_id=category_id,
                category_name=category_name,
            )
            flash("Avance de frais enregistrée.", "success")

        return redirect(url_for("expense"))

    # Options de catégories pour le HTML
    category_options = ""
    for c in categories:
        category_options += f'<option value="{c["id"]}">{c["name"]}</option>'

    body = f"""
    <h1 class="mb-4">Dépenses</h1>

    <div class="row g-4">
      <!-- Dépense CB maison -->
      <div class="col-lg-6">
        <div class="card shadow-sm border-0">
          <div class="card-header bg-danger text-white">
            <h5 class="mb-0">Dépense CB de la maison</h5>
          </div>
          <div class="card-body">
            <form method="post">
              <input type="hidden" name="form_type" value="cb_ville">

              <div class="mb-3">
                <label class="form-label">Montant (€)</label>
                <input type="text" name="amount" class="form-control" required>
              </div>

              <div class="mb-3">
                <label class="form-label">Catégorie</label>
                <select name="category_id" class="form-select" required>
                  <option value="">-- choisir --</option>
                  {category_options}
                </select>
              </div>

              <div class="mb-3">
                <label class="form-label">Description</label>
                <input type="text" name="description" class="form-control" placeholder="Ex : courses, plein, etc.">
              </div>

              <div class="d-grid">
                <button type="submit" class="btn btn-danger">Enregistrer la dépense</button>
              </div>
            </form>
          </div>
        </div>
      </div>

      <!-- Avance de frais -->
      <div class="col-lg-6">
        <div class="card shadow-sm border-0">
          <div class="card-header bg-warning text-dark">
            <h5 class="mb-0">Avance de frais</h5>
          </div>
          <div class="card-body">
            <form method="post">
              <input type="hidden" name="form_type" value="avance">

              <div class="mb-3">
                <label class="form-label">Montant (€)</label>
                <input type="text" name="amount" class="form-control" required>
              </div>

              <div class="mb-3">
                <label class="form-label">Catégorie</label>
                <select name="category_id" class="form-select" required>
                  <option value="">-- choisir --</option>
                  {category_options}
                </select>
              </div>

              <div class="mb-3">
                <label class="form-label">Moyen de paiement</label>
                <select name="payment_method" class="form-select">
                  <option value="cb_perso">CB personnelle</option>
                  <option value="cheque">Chèque</option>
                  <option value="especes">Espèces</option>
                </select>
              </div>

              <div class="mb-3">
                <label class="form-label">Description</label>
                <input type="text" name="description" class="form-control" placeholder="Ex : avance sur courses">
              </div>

              <div class="d-grid">
                <button type="submit" class="btn btn-warning">Enregistrer l'avance</button>
              </div>
            </form>
          </div>
        </div>
      </div>
    </div>

    <p class="mt-3 text-muted small">
      Les catégories de dépenses sont définies par l'administrateur.
    </p>
    """
    return render_page(body, "Dépenses")


# -------------------------------------------------------------------
# Mes opérations (historique personnel) + annulation dernière opération
# -------------------------------------------------------------------

@app.route("/my-operations")
def my_operations():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    user = current_user()
    if not user:
        return redirect(url_for("login"))

    today = date.today()

    year_param = request.args.get("year")
    month_param = request.args.get("month")

    try:
        if year_param and month_param:
            year = int(year_param)
            month = int(month_param)
            selected_date = date(year, month, 1)
        else:
            selected_date = today
    except ValueError:
        selected_date = today

    year_month = get_year_month(selected_date)
    school_year = get_school_year_for_date(selected_date)

    tx_query = (
        db.collection(TRANSACTIONS_COLLECTION)
        .where("userId", "==", user["id"])
        .where("yearMonth", "==", year_month)
        .order_by("date")
    )

    try:
        tx_docs = list(tx_query.stream())
    except Exception:
        flash(
            "Firestore demande peut-être un index pour cette requête. Consulte la console Firebase si besoin.",
            "error",
        )
        tx_docs = []

    rows_html = ""
    total = 0.0
    last_cancellable_tx_id = None

    for doc in tx_docs:
        t = doc.to_dict()
        tx_id = doc.id
        amount = float(t.get("amount", 0.0))
        total += amount

        ttype = t.get("type")
        if ttype == "income":
            type_label = "Recette"
        elif ttype == "expense":
            type_label = "Dépense"
        else:
            type_label = ttype or ""

        source = t.get("source") or ""
        if source == "allocation_mensuelle":
            source_label = "Allocation mensuelle"
        elif source == "recette_ponctuelle":
            source_label = "Recette ponctuelle"
        elif source == "depense_carte_ville":
            source_label = "Dépense CB maison"
        elif source == "avance_frais_personnelle":
            source_label = "Avance de frais"
        else:
            source_label = source

        # On autorise l'annulation pour toutes les opérations sauf allocation
        if source != "allocation_mensuelle":
            last_cancellable_tx_id = tx_id

        category_name = t.get("categoryName") or ""
        payment_method = t.get("paymentMethod") or ""
        desc = t.get("description") or ""
        advance_status = t.get("advanceStatus") or ""

        rows_html += f"""
          <tr>
            <td>{t.get('date')}</td>
            <td>{type_label}</td>
            <td>{source_label}</td>
            <td>{category_name}</td>
            <td>{payment_method}</td>
            <td class="text-end">{amount:.2f} €</td>
            <td>{desc}</td>
            <td>{advance_status}</td>
          </tr>
        """

    selected_year = selected_date.year
    selected_month = selected_date.month

    cancel_block = ""
    if last_cancellable_tx_id:
        cancel_url = url_for("cancel_last_operation")
        cancel_block = f"""
          <form method="post" action="{cancel_url}" class="mt-3">
            <input type="hidden" name="year" value="{selected_year}">
            <input type="hidden" name="month" value="{selected_month}">
            <button
              type="submit"
              class="btn btn-outline-danger btn-sm"
              onclick="return confirm('Annuler définitivement ma dernière opération ?');"
            >
              Annuler ma dernière opération
            </button>
          </form>
        """

    if not rows_html:
        rows_html = "<tr><td colspan='8' class='text-center text-muted'>Aucune opération pour ce mois.</td></tr>"

    body = f"""
    <h1 class="mb-4">Mes opérations</h1>

    <div class="row g-4 mb-3">
      <div class="col-lg-6">
        <div class="card shadow-sm border-0">
          <div class="card-body">
            <h5 class="card-title mb-2">Période affichée</h5>
            <p class="mb-1">Année scolaire : <strong>{school_year}</strong></p>
            <p class="mb-0">Mois : <strong>{year_month}</strong></p>
          </div>
        </div>
      </div>

      <div class="col-lg-6">
        <div class="card shadow-sm border-0">
          <div class="card-body">
            <form method="get" class="row g-2 align-items-end">
              <div class="col-4">
                <label class="form-label mb-1">Année</label>
                <input type="number" name="year" value="{selected_year}" min="2000" max="2100" class="form-control" required>
              </div>
              <div class="col-4">
                <label class="form-label mb-1">Mois</label>
                <input type="number" name="month" value="{selected_month}" min="1" max="12" class="form-control" required>
              </div>
              <div class="col-4 d-grid">
                <button type="submit" class="btn btn-primary">Afficher</button>
              </div>
            </form>
            {cancel_block}
          </div>
        </div>
      </div>
    </div>

    <div class="card shadow-sm border-0 mb-3">
      <div class="card-body">
        <h5 class="card-title mb-2">Total du mois</h5>
        <p class="mb-0">
          <strong>{total:.2f} €</strong>
        </p>
        <p class="text-muted mb-0">
          Les recettes apparaissent en positif, les dépenses en négatif.
        </p>
      </div>
    </div>

    <div class="card shadow-sm border-0">
      <div class="card-header bg-light">
        <h5 class="mb-0">Opérations pour {year_month}</h5>
      </div>
      <div class="card-body p-0">
        <div class="table-responsive">
          <table class="table table-sm mb-0 align-middle">
            <thead>
              <tr>
                <th>Date</th>
                <th>Type</th>
                <th>Source</th>
                <th>Catégorie</th>
                <th>Moyen de paiement</th>
                <th class="text-end">Montant (€)</th>
                <th>Description</th>
                <th>Statut avance</th>
              </tr>
            </thead>
            <tbody>
              {rows_html}
            </tbody>
          </table>
        </div>
      </div>
    </div>
    """
    return render_page(body, "Mes opérations")


@app.route("/my-operations/cancel-last", methods=["POST"])
def cancel_last_operation():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    user = current_user()
    if not user:
        return redirect(url_for("login"))

    year_str = request.form.get("year")
    month_str = request.form.get("month")

    try:
        year = int(year_str)
        month = int(month_str)
        selected_date = date(year, month, 1)
    except Exception:
        flash("Paramètres de date invalides.", "error")
        return redirect(url_for("my_operations"))

    year_month = get_year_month(selected_date)

    tx_query = (
        db.collection(TRANSACTIONS_COLLECTION)
        .where("userId", "==", user["id"])
        .where("yearMonth", "==", year_month)
        .order_by("date")
    )

    try:
        tx_docs = list(tx_query.stream())
    except Exception:
        flash(
            "Firestore demande peut-être un index pour cette requête. Consulte la console Firebase si besoin.",
            "error",
        )
        return redirect(url_for("my_operations", year=year, month=month))

    last_cancellable_doc = None
    for doc in tx_docs:
        t = doc.to_dict()
        source = t.get("source") or ""
        if source != "allocation_mensuelle":
            last_cancellable_doc = doc

    if not last_cancellable_doc:
        flash("Aucune opération annulable pour ce mois (hors allocation mensuelle).", "error")
        return redirect(url_for("my_operations", year=year, month=month))

    tx_id = last_cancellable_doc.id
    last_data = last_cancellable_doc.to_dict()
    amount = float(last_data.get("amount", 0.0))
    desc = last_data.get("description") or ""
    source = last_data.get("source") or ""

    db.collection(TRANSACTIONS_COLLECTION).document(tx_id).delete()

    flash(
        f"Dernière opération annulée (source={source}, montant={amount:.2f} €, description='{desc}').",
        "success",
    )
    return redirect(url_for("my_operations", year=year, month=month))


# -------------------------------------------------------------------
# Interface chef : Avances
# -------------------------------------------------------------------

@app.route("/chef/advances")
def chef_advances():
    require_login()
    require_chef_or_admin()
    user = current_user()

    docs = (
        db.collection(TRANSACTIONS_COLLECTION)
        .where("cityId", "==", user["cityId"])
        .where("isAdvance", "==", True)
        .order_by("date")
        .stream()
    )

    rows = ""
    for doc in docs:
        t = doc.to_dict()
        tx_id = doc.id
        u = get_user_by_id(t.get("userId"))
        uname = u["fullName"] if u else "?"
        amount = float(t.get("amount", 0.0))
        amount_abs = abs(amount)
        pay = t.get("paymentMethod") or ""
        desc = t.get("description") or ""
        status = t.get("advanceStatus") or ""

        if status == "en_attente":
            status_badge = '<span class="badge bg-warning text-dark">En attente</span>'
        elif status == "rembourse":
            status_badge = '<span class="badge bg-success">Remboursée</span>'
        else:
            status_badge = status

        action = ""
        if status != "rembourse":
            action = f'<a href="{url_for("chef_mark_reimbursed", tx_id=tx_id)}" class="btn btn-sm btn-outline-success">Marquer remboursée</a>'

        rows += f"""
          <tr>
            <td>{t.get('date')}</td>
            <td>{uname}</td>
            <td class="text-end">{amount_abs:.2f} €</td>
            <td>{pay}</td>
            <td>{desc}</td>
            <td>{status_badge}</td>
            <td class="text-end">{action}</td>
          </tr>
        """

    no_rows_html = "<tr><td colspan='7' class='text-center text-muted'>Aucune avance de frais pour cette ville.</td></tr>"
    tbody_rows = rows or no_rows_html

    body = f"""
    <h1 class="mb-4">Avances de frais – {user['cityId'].capitalize()}</h1>

    <div class="card shadow-sm border-0">
      <div class="card-body">
        <p class="mb-0 text-muted">
          Liste de toutes les avances de frais des utilisateurs de la maison.<br>
          Vous pouvez marquer une avance comme remboursée lorsqu'elle a été régularisée.
        </p>
      </div>
    </div>

    <div class="card shadow-sm border-0 mt-3">
      <div class="card-header bg-light">
        <h5 class="mb-0">Avances enregistrées</h5>
      </div>
      <div class="card-body p-0">
        <div class="table-responsive">
          <table class="table table-sm mb-0 align-middle">
            <thead>
              <tr>
                <th>Date</th>
                <th>Utilisateur</th>
                <th class="text-end">Montant</th>
                <th>Moyen</th>
                <th>Description</th>
                <th>Statut</th>
                <th class="text-end">Action</th>
              </tr>
            </thead>
            <tbody>
              {tbody_rows}
            </tbody>
          </table>
        </div>
      </div>
    </div>
    """
    return render_page(body, "Avances de frais")

# -------------------------------------------------------------------
# Chef / Admin : Export CSV (ville simple – année scolaire courante)
# -------------------------------------------------------------------

@app.route("/chef/export")
def chef_export():
    require_login()
    require_chef_or_admin()
    user = current_user()

    today = date.today()
    school_year = get_school_year_for_date(today)

    docs = (
        db.collection(TRANSACTIONS_COLLECTION)
        .where("cityId", "==", user["cityId"])
        .where("schoolYear", "==", school_year)
        .stream()
    )

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        ["id", "date", "yearMonth", "type", "source", "amount",
         "paymentMethod", "categoryName", "description", "userFullName",
         "isAdvance", "advanceStatus"]
    )

    for doc in docs:
        t = doc.to_dict()
        tx_id = doc.id
        u = get_user_by_id(t.get("userId"))
        uname = u["fullName"] if u else ""
        writer.writerow([
            tx_id,
            t.get("date"),
            t.get("yearMonth"),
            t.get("type"),
            t.get("source"),
            t.get("amount"),
            t.get("paymentMethod"),
            t.get("categoryName"),
            t.get("description"),
            uname,
            t.get("isAdvance"),
            t.get("advanceStatus"),
        ])

    csv_content = output.getvalue()
    output.close()

    filename = f"compta_{user['cityId']}_{school_year}.csv"

    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

# -------------------------------------------------------------------
# Chef : Compta maison (vue filtrée + export)
# -------------------------------------------------------------------

@app.route("/chef/compta", methods=["GET"])
def chef_city_transactions():
    require_login()
    require_chef_or_admin()
    user = current_user()
    today = date.today()

    mode = request.args.get("mode", "month")
    year_str = request.args.get("year")
    month_str = request.args.get("month")

    if year_str:
        try:
            year = int(year_str)
        except ValueError:
            year = today.year
    else:
        year = today.year

    if month_str:
        try:
            month = int(month_str)
        except ValueError:
            month = today.month
    else:
        month = today.month

    selected_date = date(year, max(1, min(12, month)), 1)
    year_month = get_year_month(selected_date)
    school_year = get_school_year_for_date(selected_date)

    q = db.collection(TRANSACTIONS_COLLECTION).where("cityId", "==", user["cityId"])

    if mode == "schoolyear":
        q = q.where("schoolYear", "==", school_year)
        subtitle = f"Année scolaire {school_year}"
    else:
        q = q.where("yearMonth", "==", year_month)
        subtitle = f"Mois {year_month}"

    q = q.order_by("date")

    try:
        docs = list(q.stream())
    except Exception:
        flash("Firestore demande peut-être un index pour cette requête (chef_city_transactions).", "error")
        docs = []

    rows = ""
    total = 0.0
    count = 0

    for doc in docs:
        t = doc.to_dict()
        amount = float(t.get("amount", 0.0))
        total += amount
        count += 1

        u = get_user_by_id(t.get("userId")) if t.get("userId") else None
        uname = u["fullName"] if u else ""

        ttype = t.get("type") or ""
        source = t.get("source") or ""
        pay = t.get("paymentMethod") or ""
        desc = t.get("description") or ""
        adv_status = t.get("advanceStatus") or ""
        cat_name = t.get("categoryName") or ""
        date_str = t.get("date") or ""

        rows += f"""
          <tr>
            <td>{date_str}</td>
            <td>{ttype}</td>
            <td>{source}</td>
            <td>{cat_name}</td>
            <td>{pay}</td>
            <td class="text-end">{amount:.2f} €</td>
            <td>{uname}</td>
            <td>{adv_status}</td>
            <td>{desc}</td>
          </tr>
        """

    no_rows_html = "<tr><td colspan='9' class='text-center text-muted'>Aucune opération pour ce filtre.</td></tr>"
    tbody_rows = rows or no_rows_html

    export_url = url_for(
        "chef_city_transactions_export",
        mode=mode,
        year=year,
        month=month,
    )

    body = f"""
    <h1 class="mb-4">Compta maison – {user['cityId'].capitalize()}</h1>

    <div class="row g-4 mb-3">
      <div class="col-lg-7">
        <div class="card shadow-sm border-0">
          <div class="card-body">
            <h5 class="card-title mb-3">Filtre</h5>
            <form method="get" class="row g-2 align-items-end">
              <div class="col-12 mb-2">
                <div class="form-check form-check-inline">
                  <input class="form-check-input" type="radio" name="mode" value="month" {'checked' if mode == 'month' else ''}>
                  <label class="form-check-label">Mois</label>
                </div>
                <div class="form-check form-check-inline">
                  <input class="form-check-input" type="radio" name="mode" value="schoolyear" {'checked' if mode == 'schoolyear' else ''}>
                  <label class="form-check-label">Année scolaire</label>
                </div>
              </div>
              <div class="col-4">
                <label class="form-label mb-1">Année</label>
                <input type="number" name="year" value="{year}" min="2000" max="2100" class="form-control" required>
              </div>
              <div class="col-4">
                <label class="form-label mb-1">Mois</label>
                <input type="number" name="month" value="{month}" min="1" max="12" class="form-control">
              </div>
              <div class="col-4 d-grid">
                <button type="submit" class="btn btn-primary">Afficher</button>
              </div>
            </form>
          </div>
        </div>
      </div>

      <div class="col-lg-5">
        <div class="card shadow-sm border-0">
          <div class="card-body">
            <h5 class="card-title mb-2">Résumé</h5>
            <p class="mb-1"><strong>Filtre :</strong> {subtitle}</p>
            <p class="mb-1"><strong>Ville :</strong> {user['cityId'].capitalize()}</p>
            <p class="mb-1"><strong>Nombre d'opérations :</strong> {count}</p>
            <p class="mb-0"><strong>Total :</strong> {total:.2f} €</p>
          </div>
        </div>
      </div>
    </div>

    <div class="mb-3">
      <a href="{export_url}" class="btn btn-outline-secondary btn-sm">
        📥 Exporter en CSV (mêmes filtres)
      </a>
    </div>

    <div class="card shadow-sm border-0">
      <div class="card-header bg-light">
        <h5 class="mb-0">Opérations – {subtitle}</h5>
      </div>
      <div class="card-body p-0">
        <div class="table-responsive">
          <table class="table table-sm mb-0 align-middle">
            <thead>
              <tr>
                <th>Date</th>
                <th>Type</th>
                <th>Source</th>
                <th>Catégorie</th>
                <th>Moyen</th>
                <th class="text-end">Montant (€)</th>
                <th>Utilisateur</th>
                <th>Statut avance</th>
                <th>Description</th>
              </tr>
            </thead>
            <tbody>
              {tbody_rows}
            </tbody>
          </table>
        </div>
      </div>
    </div>
    """
    return render_page(body, "Compta maison")


# -------------------------------------------------------------------
# Admin : Compta (voir / annuler / exporter opérations)
# -------------------------------------------------------------------

@app.route("/admin/transactions", methods=["GET"])
def admin_transactions():
    require_login()
    require_admin()
    today = date.today()

    city = request.args.get("city", "all")
    mode = request.args.get("mode", "month")
    year_str = request.args.get("year")
    month_str = request.args.get("month")

    if year_str:
        try:
            year = int(year_str)
        except ValueError:
            year = today.year
    else:
        year = today.year

    if month_str:
        try:
            month = int(month_str)
        except ValueError:
            month = today.month
    else:
        month = today.month

    selected_date = date(year, max(1, min(12, month)), 1)
    year_month = get_year_month(selected_date)
    school_year = get_school_year_for_date(selected_date)

    q = db.collection(TRANSACTIONS_COLLECTION)

    if city != "all":
        q = q.where("cityId", "==", city)

    if mode == "schoolyear":
        q = q.where("schoolYear", "==", school_year)
        subtitle = f"Année scolaire {school_year}"
    else:
        q = q.where("yearMonth", "==", year_month)
        subtitle = f"Mois {year_month}"

    q = q.order_by("date")

    try:
        docs = list(q.stream())
    except Exception:
        flash(
            "Firestore demande peut-être un index pour cette requête (admin_transactions). Consulte la console Firebase si besoin.",
            "error",
        )
        docs = []

    rows = ""
    total = 0.0
    count = 0

    for doc in docs:
        t = doc.to_dict()
        tx_id = doc.id
        amount = float(t.get("amount", 0.0))
        total += amount
        count += 1

        u = get_user_by_id(t.get("userId")) if t.get("userId") else None
        uname = u["fullName"] if u else ""

        city_label = t.get("cityId") or ""
        ttype = t.get("type") or ""
        source = t.get("source") or ""
        pay = t.get("paymentMethod") or ""
        desc = t.get("description") or ""
        adv_status = t.get("advanceStatus") or ""
        cat_name = t.get("categoryName") or ""
        date_str = t.get("date") or ""

        delete_url = url_for(
            "admin_delete_transaction",
            tx_id=tx_id,
            city=city,
            mode=mode,
            year=year,
            month=month,
        )

        rows += f"""
          <tr>
            <td>{tx_id}</td>
            <td>{city_label}</td>
            <td>{date_str}</td>
            <td>{ttype}</td>
            <td>{source}</td>
            <td>{cat_name}</td>
            <td>{pay}</td>
            <td class="text-end">{amount:.2f} €</td>
            <td>{uname}</td>
            <td>{adv_status}</td>
            <td>{desc}</td>
            <td class="text-end">
              <a href="{delete_url}"
                 class="btn btn-sm btn-outline-danger"
                 onclick="return confirm('Supprimer définitivement cette opération ?');">
                Annuler
              </a>
            </td>
          </tr>
        """

    no_rows_html = "<tr><td colspan='12' class='text-center text-muted'>Aucune opération pour ce filtre.</td></tr>"
    tbody_rows = rows or no_rows_html

    export_url = url_for(
        "admin_transactions_export",
        city=city,
        mode=mode,
        year=year,
        month=month,
    )

    ville_label = "toutes" if city == "all" else city

    body = f"""
    <h1 class="mb-4">Admin compta – toutes opérations</h1>

    <div class="row g-4 mb-3">
      <div class="col-lg-7">
        <div class="card shadow-sm border-0">
          <div class="card-body">
            <h5 class="card-title mb-3">Filtres</h5>
            <form method="get" class="row g-2 align-items-end">
              <div class="col-12">
                <label class="form-label mb-1">Ville</label>
                <select name="city" class="form-select">
                  <option value="all" {'selected' if city == 'all' else ''}>Toutes</option>
                  <option value="strasbourg" {'selected' if city == 'strasbourg' else ''}>Strasbourg</option>
                  <option value="colmar" {'selected' if city == 'colmar' else ''}>Colmar</option>
                </select>
              </div>

              <div class="col-12 mt-2">
                <span class="form-label d-block mb-1">Mode</span>
                <div class="form-check form-check-inline">
                  <input class="form-check-input" type="radio" name="mode" value="month" {'checked' if mode == 'month' else ''}>
                  <label class="form-check-label">Mois</label>
                </div>
                <div class="form-check form-check-inline">
                  <input class="form-check-input" type="radio" name="mode" value="schoolyear" {'checked' if mode == 'schoolyear' else ''}>
                  <label class="form-check-label">Année scolaire</label>
                </div>
              </div>

              <div class="col-4">
                <label class="form-label mb-1">Année</label>
                <input type="number" name="year" value="{year}" min="2000" max="2100" class="form-control" required>
              </div>
              <div class="col-4">
                <label class="form-label mb-1">Mois</label>
                <input type="number" name="month" value="{month}" min="1" max="12" class="form-control">
              </div>
              <div class="col-4 d-grid">
                <button type="submit" class="btn btn-primary">Afficher</button>
              </div>
            </form>
          </div>
        </div>
      </div>

      <div class="col-lg-5">
        <div class="card shadow-sm border-0">
          <div class="card-body">
            <h5 class="card-title mb-2">Résumé</h5>
            <p class="mb-1"><strong>Filtre :</strong> {subtitle}</p>
            <p class="mb-1"><strong>Ville :</strong> {ville_label}</p>
            <p class="mb-1"><strong>Nombre d'opérations :</strong> {count}</p>
            <p class="mb-0"><strong>Total :</strong> {total:.2f} €</p>
          </div>
        </div>
      </div>
    </div>

    <div class="mb-3">
      <a href="{export_url}" class="btn btn-outline-secondary btn-sm">
        📥 Exporter en CSV (mêmes filtres)
      </a>
    </div>

    <div class="card shadow-sm border-0">
      <div class="card-header bg-light">
        <h5 class="mb-0">Opérations – {subtitle}</h5>
      </div>
      <div class="card-body p-0">
        <div class="table-responsive">
          <table class="table table-sm mb-0 align-middle">
            <thead>
              <tr>
                <th>ID</th>
                <th>Ville</th>
                <th>Date</th>
                <th>Type</th>
                <th>Source</th>
                <th>Catégorie</th>
                <th>Moyen</th>
                <th class="text-end">Montant (€)</th>
                <th>Utilisateur</th>
                <th>Statut avance</th>
                <th>Description</th>
                <th class="text-end">Action</th>
              </tr>
            </thead>
            <tbody>
              {tbody_rows}
            </tbody>
          </table>
        </div>
      </div>
    </div>
    """
    return render_page(body, "Admin compta")

# -------------------------------------------------------------------
# Admin : Gestion catégories de dépenses
# -------------------------------------------------------------------

@app.route("/admin/categories", methods=["GET", "POST"])
def admin_categories():
    require_login()
    require_admin()

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Le nom de la catégorie est obligatoire.", "error")
        else:
            now = utc_now_iso()
            doc_ref = db.collection(EXPENSE_CATEGORIES_COLLECTION).document()
            doc_ref.set(
                {
                    "name": name,
                    "active": True,
                    "createdAt": now,
                    "updatedAt": now,
                }
            )
            flash(f"Catégorie '{name}' créée.", "success")
        return redirect(url_for("admin_categories"))

    docs = (
        db.collection(EXPENSE_CATEGORIES_COLLECTION)
        .order_by("name")
        .stream()
    )

    rows = ""
    for doc in docs:
        c = doc.to_dict()
        cid = doc.id
        rows += f"""
          <tr>
            <td>{cid}</td>
            <td>{c.get('name')}</td>
            <td>{"Oui" if c.get("active") else "Non"}</td>
            <td>
              {"-" if not c.get("active") else f'<a href="{url_for("admin_category_disable", cat_id=cid)}">Désactiver</a>'}
            </td>
          </tr>
        """

    body = f"""
      <h1>Catégories de dépenses</h1>
      <form method="post">
        <label>Nom de la nouvelle catégorie :</label><br>
        <input type="text" name="name" required><br>
        <button type="submit">Ajouter</button>
      </form>

      <h2>Liste des catégories</h2>
      <table>
        <tr>
          <th>ID</th>
          <th>Nom</th>
          <th>Active</th>
          <th>Action</th>
        </tr>
        {rows if rows else "<tr><td colspan='4'>Aucune catégorie encore créée.</td></tr>"}
      </table>
    """
    return render_page(body, "Catégories dépenses")

@app.route("/admin/categories/<cat_id>/disable")
def admin_category_disable(cat_id):
    require_login()
    require_admin()

    ref = db.collection(EXPENSE_CATEGORIES_COLLECTION).document(cat_id)
    doc = ref.get()
    if not doc.exists:
        flash("Catégorie introuvable.", "error")
    else:
        ref.update(
            {
                "active": False,
                "updatedAt": utc_now_iso(),
            }
        )
        flash("Catégorie désactivée.", "success")

    return redirect(url_for("admin_categories"))

# -------------------------------------------------------------------
# Admin : Gestion utilisateurs
# -------------------------------------------------------------------

@app.route("/admin/users")
def admin_users():
    require_login()
    require_admin()

    docs = db.collection(USERS_COLLECTION).order_by("cityId").order_by("fullName").stream()

    rows = ""
    for doc in docs:
        u = doc.to_dict()
        uid = doc.id
        rows += f"""
          <tr>
            <td>{uid}</td>
            <td>{u.get('login')}</td>
            <td>{u.get('fullName')}</td>
            <td>{u.get('cityId')}</td>
            <td>{u.get('role')}</td>
            <td>{"Oui" if u.get('active') else "Non"}</td>
            <td>{u.get('passwordSetAt') or "Non défini"}</td>
            <td>{u.get('lastLoginAt') or "-"}</td>
            <td>{"Oui" if u.get('mustChangePassword') else "Non"}</td>
            <td>
              <a href="{url_for('admin_reset_password', user_id=uid)}">Réinitialiser mot de passe</a>
            </td>
          </tr>
        """

    body = f"""
      <h1>Administration – Utilisateurs</h1>
      <p><a href="{url_for('admin_create_user')}">Créer un utilisateur</a></p>
      <table>
        <tr>
          <th>ID</th>
          <th>Login</th>
          <th>Nom</th>
          <th>Ville</th>
          <th>Rôle</th>
          <th>Actif</th>
          <th>Mot de passe défini</th>
          <th>Dernière connexion</th>
          <th>Doit changer MDP</th>
          <th>Actions</th>
        </tr>
        {rows}
      </table>
    """
    return render_page(body, "Admin utilisateurs")

@app.route("/admin/users/create", methods=["GET", "POST"])
def admin_create_user():
    require_login()
    require_admin()

    if request.method == "POST":
        user_id = request.form.get("user_id").strip()
        full_name = request.form.get("full_name").strip()
        short_name = request.form.get("short_name").strip()
        login_name = request.form.get("login").strip()
        city_id = request.form.get("city_id")
        role = request.form.get("role")
        temp_pwd = request.form.get("temp_password").strip()

        if not user_id or not full_name or not login_name:
            flash("ID, Nom complet et Login obligatoires.", "error")
        else:
            existing = get_user_by_id(user_id)
            if existing:
                flash("ID déjà utilisé.", "error")
            else:
                create_user(
                    user_id=user_id,
                    full_name=full_name,
                    short_name=short_name or full_name,
                    login=login_name,
                    city_id=city_id,
                    role=role,
                    temp_password=temp_pwd or None,
                    must_change_password=True,
                )
                flash(f"Utilisateur {full_name} créé.", "success")
                return redirect(url_for("admin_users"))

    body = """
      <h1>Créer utilisateur</h1>
      <form method="post">
        <label>ID interne :</label><br>
        <input type="text" name="user_id" required><br>

        <label>Nom complet :</label><br>
        <input type="text" name="full_name" required><br>

        <label>Nom court :</label><br>
        <input type="text" name="short_name"><br>

        <label>Login :</label><br>
        <input type="text" name="login" required><br>

        <label>Ville :</label><br>
        <select name="city_id">
          <option value="strasbourg">Strasbourg</option>
          <option value="colmar">Colmar</option>
        </select><br>

        <label>Rôle :</label><br>
        <select name="role">
          <option value="user">Utilisateur</option>
          <option value="chef">Chef de maison</option>
          <option value="admin">Administrateur</option>
        </select><br>

        <label>Mot de passe temporaire :</label><br>
        <input type="text" name="temp_password"><br>

        <button type="submit">Créer</button>
      </form>
    """
    return render_page(body, "Créer utilisateur")

@app.route("/admin/users/<user_id>/reset-password", methods=["GET", "POST"])
def admin_reset_password(user_id):
    require_login()
    require_admin()

    u = get_user_by_id(user_id)
    if not u:
        abort(404)

    if request.method == "POST":
        temp_pwd = request.form.get("temp_password", "").strip()
        if not temp_pwd:
            flash("Veuillez saisir un mot de passe temporaire.", "error")
        else:
            new_hash = generate_password_hash(temp_pwd)
            update_user(
                user_id,
                passwordHash=new_hash,
                mustChangePassword=True,
                passwordSetAt=None,
            )
            flash("Mot de passe réinitialisé.", "success")
            return redirect(url_for("admin_users"))

    body = f"""
      <h1>Réinitialiser mot de passe</h1>
      <p>Utilisateur : {u['fullName']}</p>
      <form method="post">
        <label>Nouveau mot de passe temporaire :</label><br>
        <input type="text" name="temp_password" required><br>
        <button type="submit">Réinitialiser</button>
      </form>
    """
    return render_page(body, "Réinitialiser mot de passe")

# -------------------------------------------------------------------
# Initialisation complète (villes + 7 utilisateurs)
# -------------------------------------------------------------------

def init_default_users_and_cities():
    init_cities()

    # Admin principal
    create_user(
        user_id="admin",
        full_name="Administrateur SMMD Alsace",
        short_name="Admin",
        login="admin",
        city_id="strasbourg",
        role="admin",
        temp_password="odile+++",
        must_change_password=True,
    )

    # Strasbourg
    create_user(
        user_id="florent_molin",
        full_name="Abbé Florent Molin",
        short_name="Abbé Florent",
        login="florent.molin",
        city_id="strasbourg",
        role="chef",
        temp_password="temp123",
        must_change_password=True,
    )

    create_user(
        user_id="guillaume_legall",
        full_name="Abbé Guillaume Le Gall",
        short_name="Abbé Guillaume",
        login="guillaume.legall",
        city_id="strasbourg",
        role="user",
        temp_password="temp123",
        must_change_password=True,
    )

    create_user(
        user_id="francois_carbonnieres",
        full_name="Abbé François de Carbonnières",
        short_name="Abbé François",
        login="francois.carbonnieres",
        city_id="strasbourg",
        role="user",
        temp_password="temp123",
        must_change_password=True,
    )

    # Colmar
    create_user(
        user_id="montfort_gillet",
        full_name="Abbé Montfort Gillet",
        short_name="Abbé Montfort",
        login="montfort.gillet",
        city_id="colmar",
        role="chef",
        temp_password="temp123",
        must_change_password=True,
    )

    create_user(
        user_id="mederic_bertaud",
        full_name="Frère Médéric Bertaud",
        short_name="Frère Médéric",
        login="mederic.bertaud",
        city_id="colmar",
        role="user",
        temp_password="temp123",
        must_change_password=True,
    )

    create_user(
        user_id="paul_trifault",
        full_name="Abbé Paul Trifault",
        short_name="Abbé Paul",
        login="paul.trifault",
        city_id="colmar",
        role="user",
        temp_password="temp123",
        must_change_password=True,
    )

    print("Villes et utilisateurs initiaux créés.")

# -------------------------------------------------------------------
# Lancement
# -------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "init":
        init_default_users_and_cities()
    else:
        app.run(host="0.0.0.0", port=5000, debug=True)
