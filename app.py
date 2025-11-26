import os
import csv
import io
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

# Firestore
db = firestore.Client()

USERS_COLLECTION = "users"
CITIES_COLLECTION = "cities"
ALLOC_CONFIGS_COLLECTION = "allocationConfigs"
TRANSACTIONS_COLLECTION = "transactions"

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
  <style>
    body { font-family: sans-serif; margin: 2rem; }
    nav a { margin-right: 1rem; }
    .error { color: red; }
    .success { color: green; }
    table { border-collapse: collapse; margin-top: 1rem; }
    th, td { border: 1px solid #ccc; padding: 0.4rem 0.8rem; }
    form { margin-top: 1rem; }
    input, select { margin: 0.2rem 0; padding: 0.2rem 0.4rem; }
  </style>
</head>
<body>
  <nav>
    {% if session.user_id %}
      Bonjour {{ session.short_name }} |
      <a href="{{ url_for('dashboard') }}">Tableau de bord</a>
      <a href="{{ url_for('income') }}">Recettes</a>
      <a href="{{ url_for('expense') }}">Dépenses</a>
      {% if session.role in ['chef', 'admin'] %}
        <a href="{{ url_for('chef_advances') }}">Avances (chef)</a>
        <a href="{{ url_for('chef_export') }}">Export ville</a>
      {% endif %}
      {% if session.role == 'admin' %}
        <a href="{{ url_for('admin_users') }}">Admin utilisateurs</a>
      {% endif %}
      <a href="{{ url_for('logout') }}">Déconnexion</a>
    {% else %}
      <a href="{{ url_for('login') }}">Connexion</a>
    {% endif %}
  </nav>
  <hr>

  {% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
      <ul>
      {% for category, msg in messages %}
        <li class="{{ category }}">{{ msg }}</li>
      {% endfor %}
      </ul>
    {% endif %}
  {% endwith %}

  {{ body|safe }}
</body>
</html>
"""

def render_page(body_html: str, title: str = None):
    return render_template_string(BASE_LAYOUT, body=body_html, title=title)

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

    if request.method == "POST" and request.form.get("form_type") == "allocation":
        try:
            monthly_amount = float(request.form.get("monthly_amount").replace(",", "."))
        except Exception:
            flash("Montant invalide.", "error")
        else:
            if monthly_amount <= 0:
                flash("Le montant doit être positif.", "error")
            else:
                upsert_allocation_config(user["id"], user["cityId"], school_year, monthly_amount)
                flash("Allocation mensuelle mise à jour pour l'année scolaire en cours.", "success")

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

    config = get_allocation_config(user["id"], school_year)
    monthly_amount = config["monthlyAmount"] if config else None

    body = f"""
      <h1>Recettes</h1>
      <h2>Allocation mensuelle (année scolaire {school_year})</h2>
      <form method="post">
        <input type="hidden" name="form_type" value="allocation">
        <div>
          <label>Montant mensuel (€) :</label><br>
          <input type="text" name="monthly_amount" value="{monthly_amount if monthly_amount else ''}" required>
        </div>
        <button type="submit">Enregistrer</button>
      </form>

      <h2>Recette ponctuelle ({year_month})</h2>
      <form method="post">
        <input type="hidden" name="form_type" value="extra_income">
        <div>
          <label>Montant (€) :</label><br>
          <input type="text" name="amount" required>
        </div>
        <div>
          <label>Description :</label><br>
          <input type="text" name="description">
        </div>
        <button type="submit">Ajouter</button>
      </form>
    """
    return render_page(body, "Recettes")

# -------------------------------------------------------------------
# Dépenses
# -------------------------------------------------------------------

@app.route("/expense", methods=["GET", "POST"])
def expense():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    user = current_user()
    if not user:
        return redirect(url_for("login"))

    today = date.today()

    if request.method == "POST":
        form_type = request.form.get("form_type")
        desc = request.form.get("description", "").strip() or "Dépense"

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
            )
            flash("Avance de frais enregistrée.", "success")

        return redirect(url_for("expense"))

    body = """
      <h1>Dépenses</h1>

      <h2>Dépense CB de la maison</h2>
      <form method="post">
        <input type="hidden" name="form_type" value="cb_ville">
        <label>Montant (€):</label><br>
        <input type="text" name="amount" required><br>
        <label>Description:</label><br>
        <input type="text" name="description"><br>
        <button type="submit">Enregistrer</button>
      </form>

      <h2>Avance de frais</h2>
      <form method="post">
        <input type="hidden" name="form_type" value="avance">
        <label>Montant (€):</label><br>
        <input type="text" name="amount" required><br>
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
    """
    return render_page(body, "Dépenses")

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
# Chef/Admin : Export CSV
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
         "paymentMethod", "description", "userFullName",
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
