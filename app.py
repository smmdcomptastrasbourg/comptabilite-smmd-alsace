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
</head>

<body class="bg-light">

  <div class="container my-4">
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
    if request.method == "POST":
        login_name = request.form.get("login", "").strip()
        password = request.form.get("password", "")

        user = get_user_by_login(login_name)
        if not user or not user.get("active", False):
            flash("Identifiant ou mot de passe incorrect.", "error")
        else:
            password_hash = user.get("passwordHash")
            if not password_hash or not check_password_hash(password_hash, password):
                flash("Identifiant ou mot de passe incorrect.", "error")
            else:
                login_user(user)
                update_user(user["id"], lastLoginAt=utc_now_iso())
                if user.get("mustChangePassword", False):
                    return redirect(url_for("change_password_first"))
                return redirect(url_for("dashboard"))

    body = """
      <h1>Connexion</h1>
      <form method="post">
        <div>
          <label>Identifiant :</label><br>
          <input type="text" name="login" required>
        </div>
        <div>
          <label>Mot de passe :</label><br>
          <input type="password" name="password" required>
        </div>
        <button type="submit">Se connecter</button>
      </form>
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
    if not session.get("user_id"):
        return redirect(url_for("login"))
    user = current_user()
    if not user:
        return redirect(url_for("login"))

    today = date.today()
    school_year = get_school_year_for_date(today)
    year_month = get_year_month(today)

    ensure_allocation_transaction_for_month(user, today)

    city_balance = get_city_annual_balance(user["cityId"], school_year)
    personal_balance = get_personal_monthly_balance(user["id"], year_month)

    body = f"""
      <h1>Comptabilité SMMD Alsace</h1>
      <h2>Tableau de bord</h2>
      <p>Utilisateur : {user['fullName']} ({user['role']})</p>
      <p>Ville : {user['cityId'].capitalize()}</p>
      <p>Année scolaire : {school_year}</p>

      <h3>Solde annuel du compte de la ville</h3>
      <p><strong>{city_balance:.2f} €</strong></p>

      <h3>Mon solde mensuel personnel ({year_month})</h3>
      <p><strong>{personal_balance:.2f} €</strong></p>
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
    school_year = get_school_year_for_date(today)
    year_month = get_year_month(today)

    # --- Traitement du formulaire "allocation mensuelle" ---
    if request.method == "POST" and request.form.get("form_type") == "allocation":
        try:
            monthly_amount = float(request.form.get("monthly_amount").replace(",", "."))
        except Exception:
            flash("Montant invalide.", "error")
        else:
            if monthly_amount <= 0:
                flash("Le montant doit être positif.", "error")
            else:
                # 1) Met à jour la config d'allocation pour l'année scolaire en cours
                upsert_allocation_config(user["id"], user["cityId"], school_year, monthly_amount)

                # 2) Met à jour (ou crée) la transaction d'allocation pour le MOIS EN COURS
                alloc_query = (
                    db.collection(TRANSACTIONS_COLLECTION)
                    .where("userId", "==", user["id"])
                    .where("cityId", "==", user["cityId"])
                    .where("schoolYear", "==", school_year)
                    .where("yearMonth", "==", year_month)
                    .where("source", "==", "allocation_mensuelle")
                    .limit(1)
                )

                existing_alloc_doc = None
                for doc in alloc_query.stream():
                    existing_alloc_doc = doc
                    break

                if existing_alloc_doc:
                    # On met simplement à jour le montant de la transaction existante
                    existing_alloc_doc.reference.update(
                        {
                            "amount": float(monthly_amount),
                            "updatedAt": utc_now_iso(),
                        }
                    )
                else:
                    # Aucune transaction pour ce mois : on la crée
                    create_transaction(
                        city_id=user["cityId"],
                        user_id=user["id"],
                        d=today,
                        ttype="income",
                        source="allocation_mensuelle",
                        amount=float(monthly_amount),
                        payment_method="virement",
                        is_advance=False,
                        advance_status=None,
                        description=f"Allocation mensuelle {year_month}",
                    )

                flash(
                    "Allocation mensuelle mise à jour pour le mois en cours et les mois suivants de l'année scolaire.",
                    "success",
                )

    # --- Traitement du formulaire "recette ponctuelle" ---
    if request.method == "POST" and request.form.get("form_type") == "extra_income":
        desc = request.form.get("description", "").strip() or "Recette ponctuelle"
        try:
            amount = float(request.form.get("amount").replace(",", "."))
        except Exception:
            flash("Montant invalide.", "error")
        else:
            if amount <= 0:
                flash("Le montant doit être positif.", "error")
            else:
                create_transaction(
                    city_id=user["cityId"],
                    user_id=user["id"],
                    d=today,
                    ttype="income",
                    source="recette_ponctuelle",
                    amount=amount,
                    payment_method="autre",
                    is_advance=False,
                    advance_status=None,
                    description=desc,
                )
                flash(f"Recette ponctuelle de {amount:.2f} € ajoutée.", "success")

    # On recharge la config après éventuelle mise à jour
    config = get_allocation_config(user["id"], school_year)
    monthly_amount = config["monthlyAmount"] if config else None

    # Petit bloc affichant clairement le montant actuel
    if monthly_amount is not None:
        current_alloc_html = f"<p>Montant actuel de l'allocation : <strong>{monthly_amount:.2f} €</strong></p>"
    else:
        current_alloc_html = "<p>Aucune allocation mensuelle définie pour cette année scolaire.</p>"

    body = f"""
      <h1>Recettes</h1>
      <h2>Allocation mensuelle (année scolaire {school_year})</h2>

      {current_alloc_html}

      <form method="post" class="mb-4">
        <input type="hidden" name="form_type" value="allocation">
        <div class="mb-3">
          <label class="form-label">Montant mensuel (€) :</label><br>
          <input class="form-control" type="text" name="monthly_amount" value="{monthly_amount if monthly_amount else ''}" required>
        </div>
        <button class="btn btn-primary" type="submit">Enregistrer</button>
      </form>

      <h2>Recette ponctuelle ({year_month})</h2>
      <form method="post">
        <input type="hidden" name="form_type" value="extra_income">
        <div class="mb-3">
          <label class="form-label">Montant (€) :</label><br>
          <input class="form-control" type="text" name="amount" required>
        </div>
        <div class="mb-3">
          <label class="form-label">Description :</label><br>
          <input class="form-control" type="text" name="description">
        </div>
        <button class="btn btn-success" type="submit">Ajouter</button>
      </form>
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
            flash("Dépense CB ville enregistrée.", "success")

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
      <h1>Dépenses</h1>

      <h2>Dépense CB de la maison</h2>
      <form method="post">
        <input type="hidden" name="form_type" value="cb_ville">
        <label>Montant (€):</label><br>
        <input type="text" name="amount" required><br>
        <label>Catégorie :</label><br>
        <select name="category_id" required>
          <option value="">-- choisir --</option>
          {category_options}
        </select><br>
        <label>Description:</label><br>
        <input type="text" name="description"><br>
        <button type="submit">Enregistrer</button>
      </form>

      <h2>Avance de frais</h2>
      <form method="post">
        <input type="hidden" name="form_type" value="avance">
        <label>Montant (€):</label><br>
        <input type="text" name="amount" required><br>
        <label>Catégorie :</label><br>
        <select name="category_id" required>
          <option value="">-- choisir --</option>
          {category_options}
        </select><br>
        <label>Moyen de paiement :</label><br>
        <select name="payment_method">
          <option value="cb_perso">CB personnelle</option>
          <option value="cheque">Chèque</option>
          <option value="especes">Espèces</option>
        </select><br>
        <label>Description:</label><br>
        <input type="text" name="description"><br>
        <button type="submit">Enregistrer</button>
      </form>
      <p><em>Les catégories sont définies par l'administrateur.</em></p>
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
        flash("Firestore demande peut-être un index pour cette requête. Consulte la console Firebase si besoin.", "error")
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

        if source != "allocation_mensuelle":
            last_cancellable_tx_id = tx_id

        category_name = t.get("categoryName") or ""

        rows_html += f"""
          <tr>
            <td>{t.get('date')}</td>
            <td>{type_label}</td>
            <td>{source_label}</td>
            <td>{category_name}</td>
            <td>{t.get('paymentMethod') or ''}</td>
            <td>{amount:.2f}</td>
            <td>{t.get('description') or ''}</td>
            <td>{t.get('advanceStatus') or ''}</td>
          </tr>
        """

    selected_year = selected_date.year
    selected_month = selected_date.month

    cancel_block = ""
    if last_cancellable_tx_id:
        cancel_url = url_for("cancel_last_operation")
        cancel_block = f"""
          <form method="post" action="{cancel_url}" style="margin-top:1rem;">
            <input type="hidden" name="year" value="{selected_year}">
            <input type="hidden" name="month" value="{selected_month}">
            <button type="submit" onclick="return confirm('Annuler définitivement la dernière opération de ce mois ?');">
              Annuler la dernière opération de ce mois
            </button>
          </form>
        """

    body = f"""
      <h1>Mes opérations</h1>
      <p>Année scolaire : {school_year}</p>

      <form method="get">
        <label>Année :</label>
        <input type="number" name="year" value="{selected_year}" min="2000" max="2100" required>
        <label>Mois :</label>
        <input type="number" name="month" value="{selected_month}" min="1" max="12" required>
        <button type="submit">Afficher</button>
      </form>

      {cancel_block}

      <h2>Opérations pour {year_month}</h2>
      <table>
        <tr>
          <th>Date</th>
          <th>Type</th>
          <th>Source</th>
          <th>Catégorie</th>
          <th>Moyen de paiement</th>
          <th>Montant (€)</th>
          <th>Description</th>
          <th>Statut avance</th>
        </tr>
        {rows_html if rows_html else "<tr><td colspan='8'>Aucune opération pour ce mois.</td></tr>"}
      </table>

      <h3>Total du mois : {total:.2f} €</h3>
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
        flash("Firestore demande peut-être un index pour cette requête. Consulte la console Firebase si besoin.", "error")
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
        .stream()
    )

    rows = ""
    for doc in docs:
        t = doc.to_dict()
        tx_id = doc.id
        u = get_user_by_id(t.get("userId"))
        uname = u["fullName"] if u else "?"
        action = ""
        if t.get("advanceStatus") != "rembourse":
            action = f'<a href="{url_for("chef_mark_reimbursed", tx_id=tx_id)}">Remboursée</a>'
        rows += f"""
          <tr>
            <td>{tx_id}</td>
            <td>{t.get('date')}</td>
            <td>{uname}</td>
            <td>{t.get('amount')}</td>
            <td>{t.get('paymentMethod')}</td>
            <td>{t.get('description')}</td>
            <td>{t.get('advanceStatus')}</td>
            <td>{action}</td>
          </tr>
        """

    body = f"""
      <h1>Avances de frais ({user['cityId'].capitalize()})</h1>
      <table>
        <tr>
          <th>ID</th>
          <th>Date</th>
          <th>Utilisateur</th>
          <th>Montant</th>
          <th>Moyen</th>
          <th>Description</th>
          <th>Statut</th>
          <th>Action</th>
        </tr>
        {rows}
      </table>
    """
    return render_page(body, "Avances de frais")

@app.route("/chef/advances/<tx_id>/mark-reimbursed")
def chef_mark_reimbursed(tx_id):
    require_login()
    require_chef_or_admin()

    ref = db.collection(TRANSACTIONS_COLLECTION).document(tx_id)
    if not ref.get().exists:
        abort(404)

    ref.update(
        {
            "advanceStatus": "rembourse",
            "updatedAt": utc_now_iso(),
        }
    )
    flash("Avance marquée remboursée.", "success")
    return redirect(url_for("chef_advances"))

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

        rows += f"""
          <tr>
            <td>{t.get('date')}</td>
            <td>{ttype}</td>
            <td>{source}</td>
            <td>{cat_name}</td>
            <td>{pay}</td>
            <td>{amount:.2f}</td>
            <td>{uname}</td>
            <td>{adv_status}</td>
            <td>{desc}</td>
          </tr>
        """

    export_url = url_for(
        "chef_city_transactions_export",
        mode=mode,
        year=year,
        month=month,
    )

    body = f"""
      <h1>Compta maison – {user['cityId'].capitalize()}</h1>

      <form method="get" style="margin-bottom: 1rem;">
        <label>Mode :</label>
        <label><input type="radio" name="mode" value="month" {"checked" if mode == "month" else ""}> Mois</label>
        <label><input type="radio" name="mode" value="schoolyear" {"checked" if mode == "schoolyear" else ""}> Année scolaire</label>

        <label style="margin-left:1rem;">Année :</label>
        <input type="number" name="year" value="{year}" min="2000" max="2100" required>

        <label>Mois :</label>
        <input type="number" name="month" value="{month}" min="1" max="12">

        <button type="submit">Afficher</button>
      </form>

      <p><strong>Filtre :</strong> {subtitle} – Ville : {user['cityId']}</p>
      <p><strong>Nombre d'opérations :</strong> {count} – <strong>Total :</strong> {total:.2f} €</p>

      <p>
        <a href="{export_url}">📥 Exporter en CSV (mêmes filtres)</a>
      </p>

      <table>
        <tr>
          <th>Date</th>
          <th>Type</th>
          <th>Source</th>
          <th>Catégorie</th>
          <th>Moyen</th>
          <th>Montant (€)</th>
          <th>Utilisateur</th>
          <th>Statut avance</th>
          <th>Description</th>
        </tr>
        {rows if rows else "<tr><td colspan='9'>Aucune opération pour ce filtre.</td></tr>"}
      </table>
    """
    return render_page(body, "Compta maison")


@app.route("/chef/compta/export")
def chef_city_transactions_export():
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
        filename = f"chef_compta_{user['cityId']}_{school_year}.csv"
    else:
        q = q.where("yearMonth", "==", year_month)
        filename = f"chef_compta_{user['cityId']}_{year_month}.csv"

    q = q.order_by("date")

    try:
        docs = list(q.stream())
    except Exception:
        flash("Firestore demande peut-être un index pour l'export (chef_city_transactions_export).", "error")
        return redirect(url_for("chef_city_transactions", mode=mode, year=year, month=month))

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        ["date", "yearMonth", "schoolYear",
         "type", "source", "amount", "paymentMethod",
         "categoryName", "description", "userFullName",
         "isAdvance", "advanceStatus"]
    )

    for doc in docs:
        t = doc.to_dict()
        u = get_user_by_id(t.get("userId"))
        uname = u["fullName"] if u else ""
        writer.writerow([
            t.get("date"),
            t.get("yearMonth"),
            t.get("schoolYear"),
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

    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

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
        flash("Firestore demande peut-être un index pour cette requête (admin_transactions). Consulte la console Firebase si besoin.", "error")
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
            <td>{t.get('date')}</td>
            <td>{ttype}</td>
            <td>{source}</td>
            <td>{cat_name}</td>
            <td>{pay}</td>
            <td>{amount:.2f}</td>
            <td>{uname}</td>
            <td>{adv_status}</td>
            <td>{desc}</td>
            <td><a href="{delete_url}" onclick="return confirm('Supprimer définitivement cette opération ?');">Annuler</a></td>
          </tr>
        """

    export_url = url_for(
        "admin_transactions_export",
        city=city,
        mode=mode,
        year=year,
        month=month,
    )

    body = f"""
      <h1>Admin compta – toutes opérations</h1>

      <form method="get" style="margin-bottom: 1rem;">
        <label>Ville :</label>
        <select name="city">
          <option value="all" {"selected" if city == "all" else ""}>Toutes</option>
          <option value="strasbourg" {"selected" if city == "strasbourg" else ""}>Strasbourg</option>
          <option value="colmar" {"selected" if city == "colmar" else ""}>Colmar</option>
        </select>

        <label style="margin-left:1rem;">Mode :</label>
        <label><input type="radio" name="mode" value="month" {"checked" if mode == "month" else ""}> Mois</label>
        <label><input type="radio" name="mode" value="schoolyear" {"checked" if mode == "schoolyear" else ""}> Année scolaire</label>

        <label style="margin-left:1rem;">Année :</label>
        <input type="number" name="year" value="{year}" min="2000" max="2100" required>

        <label>Mois :</label>
        <input type="number" name="month" value="{month}" min="1" max="12">

        <button type="submit">Afficher</button>
      </form>

      <p><strong>Filtre :</strong> {subtitle} – Ville : {"toutes" if city == "all" else city}</p>
      <p><strong>Nombre d'opérations :</strong> {count} – <strong>Total :</strong> {total:.2f} €</p>

      <p>
        <a href="{export_url}">📥 Exporter en CSV (mêmes filtres)</a>
      </p>

      <table>
        <tr>
          <th>ID</th>
          <th>Ville</th>
          <th>Date</th>
          <th>Type</th>
          <th>Source</th>
          <th>Catégorie</th>
          <th>Moyen</th>
          <th>Montant (€)</th>
          <th>Utilisateur</th>
          <th>Statut avance</th>
          <th>Description</th>
          <th>Action</th>
        </tr>
        {rows if rows else "<tr><td colspan='12'>Aucune opération pour ce filtre.</td></tr>"}
      </table>
    """
    return render_page(body, "Admin compta")


@app.route("/admin/transactions/export")
def admin_transactions_export():
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
        filename = f"admin_compta_{city if city!='all' else 'toutes_villes'}_{school_year}.csv"
    else:
        q = q.where("yearMonth", "==", year_month)
        filename = f"admin_compta_{city if city!='all' else 'toutes_villes'}_{year_month}.csv"

    q = q.order_by("date")

    try:
        docs = list(q.stream())
    except Exception:
        flash("Firestore demande peut-être un index pour l'export (admin_transactions_export).", "error")
        return redirect(url_for("admin_transactions", city=city, mode=mode, year=year, month=month))

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        ["id", "cityId", "date", "yearMonth", "schoolYear",
         "type", "source", "amount", "paymentMethod",
         "categoryName", "description", "userFullName",
         "isAdvance", "advanceStatus"]
    )

    for doc in docs:
        t = doc.to_dict()
        tx_id = doc.id
        u = get_user_by_id(t.get("userId"))
        uname = u["fullName"] if u else ""
        writer.writerow([
            tx_id,
            t.get("cityId"),
            t.get("date"),
            t.get("yearMonth"),
            t.get("schoolYear"),
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

    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/admin/transactions/delete/<tx_id>")
def admin_delete_transaction(tx_id):
    require_login()
    require_admin()

    city = request.args.get("city", "all")
    mode = request.args.get("mode", "month")
    year = request.args.get("year")
    month = request.args.get("month")

    ref = db.collection(TRANSACTIONS_COLLECTION).document(tx_id)
    doc = ref.get()
    if not doc.exists:
        flash("Opération introuvable.", "error")
    else:
        data = doc.to_dict()
        amount = float(data.get("amount", 0.0))
        source = data.get("source") or ""
        desc = data.get("description") or ""
        ref.delete()
        flash(f"Opération supprimée (source={source}, montant={amount:.2f} €, description='{desc}').", "success")

    return redirect(url_for("admin_transactions", city=city, mode=mode, year=year, month=month))

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
