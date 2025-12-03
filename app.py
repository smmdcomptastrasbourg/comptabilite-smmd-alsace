import streamlit as st
import os
import json
from firebase_admin import initialize_app, credentials, firestore, exceptions
from datetime import datetime, date, timedelta
import pandas as pd
import bcrypt
from functools import lru_cache
import io
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows

# -------------------------------------------------------------------
# --- 1. Initialisation Firebase ---
# -------------------------------------------------------------------

# ATTENTION SÉCURITÉ GIT : 
# L'objet FIREBASE_SERVICE_ACCOUNT_INFO a été retiré pour des raisons de sécurité Git.
# Il est désormais chargé à partir d'une variable d'environnement.
# VOUS DEVEZ DÉFINIR LA VARIABLE D'ENVIRONNEMENT 'FIREBASE_SERVICE_ACCOUNT' 
# (contenant le JSON complet de la clé de service) DANS VOTRE ENVIRONNEMENT STREAMLIT.
# Exemple dans un terminal : export FIREBASE_SERVICE_ACCOUNT='{"type": "service_account", ...}'
# Ou dans .streamlit/secrets.toml pour Streamlit Cloud.
try:
    # 1. Tenter de charger les informations de la clé de service depuis l'environnement
    service_account_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    
    if service_account_json:
        FIREBASE_SERVICE_ACCOUNT_INFO = json.loads(service_account_json)
    else:
        # Fallback pour les tests locaux (si la variable d'env n'est pas définie)
        # Mais cela devrait ÉCHOUER si vous n'avez pas de clé ici.
        st.error("ERREUR DE CONFIGURATION: Variable d'environnement 'FIREBASE_SERVICE_ACCOUNT' non définie.")
        st.session_state['initialized'] = False
        st.stop()
        
    
    # 2. Initialisation
    if not st.session_state.get('db'):
        cred = credentials.Certificate(FIREBASE_SERVICE_ACCOUNT_INFO)
        
        try:
            # Tente d'initialiser ou de récupérer l'instance par défaut si elle existe
            from firebase_admin import get_apps
            if not get_apps():
                app = initialize_app(cred, name="smmd_app") 
            else:
                 # Si l'application a déjà été initialisée, on récupère l'instance existante (si elle a le bon nom)
                 from firebase_admin import get_app
                 try:
                     app = get_app("smmd_app")
                 except ValueError:
                     # Si l'app existe mais sans le nom 'smmd_app', on l'initialise avec le nom.
                     app = initialize_app(cred, name="smmd_app")

            st.session_state['db'] = firestore.client(app=app)
            st.session_state['initialized'] = True
        except exceptions.FirebaseError as fe:
            st.error(f"Erreur d'initialisation Firebase : {fe}")
            st.session_state['initialized'] = False
            st.stop()
        except Exception as e:
            st.error(f"Erreur inattendue lors de l'initialisation Firebase : {e}")
            st.session_state['initialized'] = False
            st.stop()

except json.JSONDecodeError:
    st.error("Erreur: La variable d'environnement 'FIREBASE_SERVICE_ACCOUNT' n'est pas un JSON valide.")
    st.session_state['initialized'] = False
    st.stop()
except Exception as e:
    st.error(f"Erreur critique lors du chargement de la configuration : {e}")
    st.session_state['initialized'] = False
    st.stop()


db = st.session_state.get('db')

# -------------------------------------------------------------------
# --- 2. Constantes globales ---
# -------------------------------------------------------------------

COL_TRANSACTIONS = 'smmd_transactions'
COL_HOUSES = 'smmd_houses' 
COL_USERS = 'smmd_users'
COL_ALLOCATIONS = 'smmd_allocations' 
COL_CATEGORIES = 'smmd_categories' 

PAYMENT_METHODS_HOUSE = ['CB Maison', 'Virement Maison']
PAYMENT_METHODS_PERSONAL = ['CB Perso', 'Chèque', 'Liquide', 'Virement Perso', 'Autre Personnel']
PAYMENT_METHODS = PAYMENT_METHODS_HOUSE + PAYMENT_METHODS_PERSONAL 

ROLES = ['admin', 'utilisateur', 'chef_de_maison']
DEFAULT_PASSWORD = "first123" 

AVANCE_STATUS = {
    'en_attente': 'En attente de validation',
    'validée': 'Validée',
    'annulée': 'Annulée'
}

TX_TYPE_MAP = {
    'depense_commune': 'Dépense Commune (Fonds Maison)',
    'depense_avance': 'Avance de Fonds (Remboursement requis)',
    'recette_mensuelle': 'Recette (Allocation Mensuelle)',
    'avance_personnelle': 'Avance de Fonds Personnel',
}

# -------------------------------------------------------------------
# --- INITIALISATION FIREBASE & UTILITAIRES DE BASE
# -------------------------------------------------------------------

# Initialisation de Firebase une seule fois
if 'firebase_app_initialized' not in st.session_state:
    try:
        # Tente de charger les informations du compte de service à partir de la variable d'environnement
        firebase_service_account_info = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
        
        if firebase_service_account_info:
            cred_json = json.loads(firebase_service_account_info)
            cred = credentials.Certificate(cred_json)
            initialize_app(cred)
            st.session_state["db"] = firestore.client()
            st.session_state['firebase_app_initialized'] = True
        else:
            st.error("Erreur: La variable d'environnement 'FIREBASE_SERVICE_ACCOUNT' n'est pas configurée.")
            st.stop()
            
    except Exception as e:
        st.error(f"Erreur critique lors de l'initialisation de Firebase: {e}")
        st.stop()

db = st.session_state.get("db")


# --- UTILS CRYPTOGRAPHIE ---
@st.cache_data
def hash_password(password):
    """Hashe un mot de passe pour le stockage."""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(password, hashed_password):
    """Vérifie si le mot de passe correspond au hash."""
    return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))

# --- UTILS FIREBASE ---

@lru_cache(maxsize=32)
def get_house_name(house_id):
    """Récupère le nom de la Maison à partir de son ID."""
    if not db or not house_id:
        return "N/A"
    try:
        house_doc = db.collection(COL_HOUSES).document(house_id).get()
        if house_doc.exists:
            return house_doc.to_dict().get('name', house_id)
        return "Maison Inconnue"
    except Exception:
        return "Erreur DB Maison"

def get_all_houses():
    """Récupère tous les documents de Maison."""
    if not db:
        return []
    try:
        docs = db.collection(COL_HOUSES).stream()
        return [{"id": doc.id, **doc.to_dict()} for doc in docs]
    except Exception as e:
        st.error(f"Erreur lors de la récupération des Maisons: {e}")
        return []

def get_all_categories():
    """Récupère toutes les catégories de dépenses."""
    if not db:
        return []
    try:
        docs = db.collection(COL_CATEGORIES).stream()
        return [{"id": doc.id, **doc.to_dict()} for doc in docs]
    except Exception as e:
        st.error(f"Erreur lors de la récupération des catégories: {e}")
        return []

def get_user_info(user_id):
    """Récupère les données utilisateur à partir de l'ID."""
    if not db or not user_id:
        return None
    try:
        doc = db.collection(COL_USERS).document(user_id).get()
        return doc.to_dict() if doc.exists else None
    except Exception:
        return None

# -------------------------------------------------------------------
# --- INTERFACES UTILISATEUR & ADMINISTRATEUR (Définitions) ---
# -------------------------------------------------------------------

def password_reset_interface(user_id):
    """Interface de réinitialisation de mot de passe forcée."""
    st.header("Réinitialisation du Mot de Passe")
    st.warning("Vous devez changer votre mot de passe pour des raisons de sécurité.")

    with st.form("password_reset_form"):
        new_password = st.text_input("Nouveau mot de passe", type="password")
        confirm_password = st.text_input("Confirmer le mot de passe", type="password")
        
        submitted = st.form_submit_button("Changer le mot de passe", type="primary")

        if submitted:
            if not new_password or not confirm_password:
                st.error("Veuillez remplir tous les champs.")
            elif new_password != confirm_password:
                st.error("Les mots de passe ne correspondent pas.")
            else:
                try:
                    hashed_pwd = hash_password(new_password)
                    db.collection(COL_USERS).document(user_id).update({
                        'password': hashed_pwd,
                        'must_change_password': False
                    })
                    st.success("Mot de passe changé avec succès. Veuillez vous reconnecter.")
                    st.session_state.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Erreur lors de la mise à jour du mot de passe: {e}")


def user_dashboard():
    """Tableau de bord de l'utilisateur standard (sans fonctions d'admin)."""
    st.header(f"Bienvenue, {st.session_state['user_data'].get('first_name')}!")
    st.info("Cette section est réservée aux fonctionnalités de base (saisie de dépenses, vue des transactions de la maison).")
    
    # Simple navigation par onglets pour l'utilisateur
    tab1, tab2 = st.tabs(["Saisie de Dépense", "Mes Transactions"])

    with tab1:
        st.subheader("Nouvelle Transaction")
        transaction_form(st.session_state['user_id'], st.session_state['house_id'])
        
    with tab2:
        st.subheader("Historique des Transactions de votre Maison")
        # Afficher les transactions de la Maison (simplifié)
        house_transactions = get_house_transactions(st.session_state['house_id'])
        if house_transactions:
            df = pd.DataFrame(house_transactions)
            df = format_transaction_df(df)
            st.dataframe(df, use_container_width=True)
        else:
            st.info("Aucune transaction enregistrée pour votre Maison.")


def transaction_form(user_id, house_id):
    """Formulaire unifié de saisie de transaction."""
    all_categories = get_all_categories()
    category_options = {c['name']: c['id'] for c in all_categories}

    with st.form("new_transaction_form", clear_on_submit=True):
        st.subheader("Détails de la Transaction")
        
        col_type, col_date = st.columns(2)
        
        # Le type de transaction n'inclut pas les recettes mensuelles ici
        tx_options = {k: v for k, v in TX_TYPE_MAP.items() if k != 'recette_mensuelle'}
        transaction_type = col_type.selectbox(
            "Type de Transaction", 
            options=list(tx_options.keys()), 
            format_func=lambda x: tx_options[x]
        )
        
        transaction_date = col_date.date_input("Date de la Transaction", value=date.today())
        
        amount = st.number_input("Montant (€)", min_value=0.01, format="%.2f")
        description = st.text_area("Description / Objet de la dépense")

        col_method, col_category = st.columns(2)
        method = col_method.selectbox("Méthode de Paiement", PAYMENT_METHODS)
        category_name = col_category.selectbox("Catégorie de Dépense", list(category_options.keys()))
        category_id = category_options.get(category_name)
        
        # Logique spécifique pour les avances de fonds personnels
        if transaction_type == 'avance_personnelle':
            st.warning("Ceci est une Avance Personnelle (Ex: avance d'argent à un membre qui sera remboursée plus tard).")
            # Logique d'avance (simplifiée pour cet exemple)
        
        submitted = st.form_submit_button("Enregistrer la Transaction", type="primary")

        if submitted:
            if amount <= 0 or not description or not category_id:
                st.error("Veuillez remplir tous les champs obligatoires.")
            else:
                try:
                    new_tx = {
                        'type': transaction_type,
                        'amount': amount,
                        'description': description,
                        'date': transaction_date.isoformat(),
                        'method': method,
                        'category_id': category_id,
                        'house_id': house_id,
                        'user_id': user_id,
                        'created_at': datetime.now().isoformat(),
                        'is_validated': False # Seules les avances sont validées, mais on garde le champ pour la cohérence
                    }
                    
                    db.collection(COL_TRANSACTIONS).add(new_tx)
                    st.success("Transaction enregistrée avec succès!")
                except Exception as e:
                    st.error(f"Erreur lors de l'enregistrement: {e}")


def admin_transaction_management():
    """Interface de gestion complète des transactions pour l'administrateur."""
    st.header("Gestion Complète des Transactions")
    
    # Récupérer toutes les transactions (simplifié pour cet exemple)
    all_transactions = get_all_transactions()
    
    if not all_transactions:
        st.info("Aucune transaction n'a été enregistrée.")
        return

    df = pd.DataFrame(all_transactions)
    df = format_transaction_df(df, detailed=True)
    
    st.subheader("Filtres et Visualisation")
    
    # Ajout d'une fonctionnalité d'export Excel
    excel_data = df_to_excel_bytes(df)
    st.download_button(
        label="Exporter en Excel (.xlsx)",
        data=excel_data,
        file_name="transactions_export.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="secondary"
    )

    st.dataframe(df, use_container_width=True)

    # Logique d'édition/suppression simplifiée (à développer)
    with st.expander("Actions Administratives (Édition/Suppression)"):
        st.write("Fonctionnalités à venir : Édition et suppression par ID.")


def advance_validation_interface():
    """Interface pour la validation des avances de fonds (remboursements)."""
    st.header("Validation des Avances de Fonds")
    st.info("Affiche les transactions de type 'depense_avance' en attente de validation.")
    
    # Filtre les transactions pour les avances non validées
    if not db:
        st.error("Base de données non initialisée.")
        return

    try:
        advances_query = db.collection(COL_TRANSACTIONS).where('type', '==', 'depense_avance').where('is_validated', '==', False).stream()
        advances = [{"id": doc.id, **doc.to_dict()} for doc in advances_query]
    except Exception as e:
        st.error(f"Erreur lors de la récupération des avances: {e}")
        return

    if not advances:
        st.success("Aucune avance en attente de validation.")
        return

    df = pd.DataFrame(advances)
    df = format_transaction_df(df, detailed=True)
    
    st.dataframe(df, use_container_width=True)

    # Logique de validation
    st.subheader("Valider une Avance")
    
    advance_id = st.text_input("Entrez l'ID de l'avance à valider (colonne 'ID')")
    
    if st.button("Valider l'Avance", type="primary"):
        if advance_id in df['ID'].values:
            try:
                db.collection(COL_TRANSACTIONS).document(advance_id).update({'is_validated': True})
                st.success(f"Avance {advance_id} marquée comme validée.")
                st.rerun()
            except Exception as e:
                st.error(f"Erreur lors de la validation: {e}")
        else:
            st.error("ID d'avance invalide ou non trouvé dans la liste.")


def admin_user_management():
    """Interface de gestion des utilisateurs (création, modification de rôle/maison)."""
    st.header("Gestion des Utilisateurs")
    
    # Onglets pour créer et gérer
    tab_create, tab_manage = st.tabs(["Créer un Utilisateur", "Liste & Modifier"])

    # --- Onglet Création ---
    with tab_create:
        st.subheader("Ajouter un Nouvel Utilisateur")
        all_houses = get_all_houses()
        house_options = {h['name']: h['id'] for h in all_houses}

        with st.form("new_user_form", clear_on_submit=True):
            col_id, col_name = st.columns(2)
            user_id = col_id.text_input("ID Utilisateur (Email sans @domaine.com)")
            first_name = col_name.text_input("Prénom")
            
            col_role, col_house = st.columns(2)
            role = col_role.selectbox("Rôle", ROLES)
            house_name = col_house.selectbox("Maison", list(house_options.keys()))
            house_id = house_options.get(house_name)
            
            submitted = st.form_submit_button("Créer l'Utilisateur", type="primary")

            if submitted:
                if not user_id or not first_name or not house_id:
                    st.error("Veuillez remplir tous les champs.")
                else:
                    try:
                        # Assurez-vous que l'ID n'est pas déjà pris
                        if db.collection(COL_USERS).document(user_id).get().exists:
                            st.error(f"L'ID utilisateur '{user_id}' existe déjà.")
                        else:
                            new_user = {
                                'first_name': first_name,
                                'role': role,
                                'house_id': house_id,
                                'password': hash_password(DEFAULT_PASSWORD), # Hash du mot de passe par défaut
                                'must_change_password': True, # Force le changement au premier login
                                'is_active': True,
                                'created_at': datetime.now().isoformat()
                            }
                            db.collection(COL_USERS).document(user_id).set(new_user)
                            st.success(f"Utilisateur {first_name} créé avec l'ID : `{user_id}`. Mot de passe par défaut : `{DEFAULT_PASSWORD}`")
                    except Exception as e:
                        st.error(f"Erreur lors de la création de l'utilisateur: {e}")

    # --- Onglet Gestion ---
    with tab_manage:
        st.subheader("Liste et Modification")
        users = [{"id": doc.id, **doc.to_dict()} for doc in db.collection(COL_USERS).stream()]
        
        if users:
            df = pd.DataFrame(users)
            df['Maison'] = df['house_id'].apply(get_house_name)
            df_display = df[['id', 'first_name', 'role', 'Maison', 'is_active', 'must_change_password', 'created_at']]
            st.dataframe(df_display, use_container_width=True)

            # Logique de modification (simplifiée)
            st.warning("La modification de rôle/maison se fera par une interface dédiée ou un éditeur de DataFrame à l'avenir.")


def admin_house_category_management():
    """Interface de gestion des Maisons et des Catégories de dépenses."""
    st.header("Gestion des Maisons et des Catégories")
    
    tab_house, tab_category = st.tabs(["Gestion des Maisons", "Gestion des Catégories"])
    
    # --- Onglet Maisons ---
    with tab_house:
        st.subheader("Ajouter une Maison")
        with st.form("new_house_form", clear_on_submit=True):
            house_id = st.text_input("ID de la Maison (Ex: strasbourg_foyer)")
            house_name = st.text_input("Nom de la Maison (Ex: Foyer de Strasbourg)")
            
            if st.form_submit_button("Ajouter la Maison", type="primary"):
                if not house_id or not house_name:
                    st.error("Veuillez remplir tous les champs.")
                else:
                    try:
                        db.collection(COL_HOUSES).document(house_id).set({'name': house_name, 'created_at': datetime.now().isoformat()})
                        st.success(f"Maison '{house_name}' ajoutée.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erreur lors de l'ajout de la Maison: {e}")

        st.subheader("Maisons Existantes")
        houses = get_all_houses()
        if houses:
            df = pd.DataFrame(houses)
            df['ID'] = df['id']
            st.dataframe(df[['ID', 'name']], use_container_width=True)

    # --- Onglet Catégories ---
    with tab_category:
        st.subheader("Ajouter une Catégorie de Dépense")
        with st.form("new_category_form", clear_on_submit=True):
            category_name = st.text_input("Nom de la Catégorie (Ex: Alimentation, Fournitures de Bureau)")
            
            if st.form_submit_button("Ajouter la Catégorie", type="primary"):
                if not category_name:
                    st.error("Veuillez entrer un nom.")
                else:
                    try:
                        # Crée un ID basé sur le nom pour l'unicité
                        category_id = category_name.lower().replace(" ", "_")
                        db.collection(COL_CATEGORIES).document(category_id).set({'name': category_name, 'created_at': datetime.now().isoformat()})
                        st.success(f"Catégorie '{category_name}' ajoutée.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erreur lors de l'ajout de la Catégorie: {e}")

        st.subheader("Catégories Existantes")
        categories = get_all_categories()
        if categories:
            df = pd.DataFrame(categories)
            df['ID'] = df['id']
            st.dataframe(df[['ID', 'name']], use_container_width=True)


def admin_interface():
    """Interface principale de l'administrateur avec menu de navigation latéral."""
    st.sidebar.markdown("## Menu Administration")
    
    # Le rôle est utilisé ici pour limiter les vues si nécessaire
    role = st.session_state['role'] 
    
    tab_options = {
        "Accueil Admin": "admin_home",
        "Gestion Utilisateurs": "admin_user_management",
        "Gestion Maisons & Catégories": "admin_house_category_management",
        "Gestion des Transactions": "admin_transaction_management",
        "Validations d'Avances": "advance_validation_interface",
    }
    
    # Si l'utilisateur est chef de maison, on pourrait vouloir limiter les options
    if role == 'chef_de_maison':
        # Exemple de restriction pour le Chef de Maison
        del tab_options["Gestion Utilisateurs"]
        del tab_options["Gestion Maisons & Catégories"]


    # st.sidebar.radio crée une variable, pas une fonction. C'est le contexte du NameError.
    selected_tab_name = st.sidebar.radio("Navigation", list(tab_options.keys()))
    
    # Stocker l'ID de la fonction dans l'état de session
    st.session_state['admin_tab_id'] = tab_options[selected_tab_name]

    # Redirection vers la fonction appropriée
    if st.session_state['admin_tab_id'] == "admin_user_management":
        admin_user_management()
    elif st.session_state['admin_tab_id'] == "admin_house_category_management":
        admin_house_category_management()
    elif st.session_state['admin_tab_id'] == "admin_transaction_management":
        admin_transaction_management()
    elif st.session_state['admin_tab_id'] == "advance_validation_interface":
        advance_validation_interface()
    elif st.session_state['admin_tab_id'] == "admin_home":
        st.header("Tableau de bord Général Admin/Chef de Maison")
        st.info("Vue d'ensemble et statistiques à venir ici.")
        # Affichage des soldes des maisons (simplifié)
        st.subheader("Soldes des Maisons")
        # Logique pour calculer et afficher les soldes (à développer)
        st.write("Le calcul des soldes des Maisons sera affiché ici.")
    else:
        st.error("Interface non trouvée.")


# -------------------------------------------------------------------
# --- UTILS DE DONNÉES (pour affichage) ---
# -------------------------------------------------------------------

def get_all_transactions():
    """Récupère toutes les transactions de la DB."""
    if not db:
        return []
    try:
        docs = db.collection(COL_TRANSACTIONS).stream()
        return [{"id": doc.id, **doc.to_dict()} for doc in docs]
    except Exception as e:
        st.error(f"Erreur lors de la récupération des transactions: {e}")
        return []

def get_house_transactions(house_id):
    """Récupère les transactions pour une Maison donnée."""
    if not db or not house_id:
        return []
    try:
        query = db.collection(COL_TRANSACTIONS).where('house_id', '==', house_id).stream()
        return [{"id": doc.id, **doc.to_dict()} for doc in query]
    except Exception as e:
        st.error(f"Erreur lors de la récupération des transactions de la Maison: {e}")
        return []

def format_transaction_df(df, detailed=False):
    """Formate le DataFrame des transactions pour l'affichage."""
    if df.empty:
        return df

    # Renommage des colonnes
    df = df.rename(columns={
        'id': 'ID',
        'type': 'Type (Clé)',
        'amount': 'Montant (€)',
        'description': 'Description',
        'date': 'Date',
        'method': 'Méthode',
        'house_id': 'Maison (Clé)',
        'user_id': 'Utilisateur (Clé)',
        'category_id': 'Catégorie (Clé)',
        'is_validated': 'Validée'
    })
    
    # Ajout des noms lisibles
    df['Type'] = df['Type (Clé)'].map(TX_TYPE_MAP)
    df['Maison'] = df['Maison (Clé)'].apply(get_house_name)
    
    # Pour la lisibilité, on met les noms en premier
    columns_order = ['ID', 'Date', 'Montant (€)', 'Description', 'Type', 'Maison', 'Méthode']
    
    if detailed:
        # Ajout des informations détaillées pour l'admin
        # Logique pour récupérer les noms d'utilisateur et de catégorie complets (omise pour la simplicité)
        columns_order.extend(['Utilisateur (Clé)', 'Catégorie (Clé)', 'Validée'])

    # Supprimer les colonnes clés redondantes si on a les noms lisibles, sauf si en mode détaillé
    cols_to_drop = [col for col in ['Type (Clé)', 'Maison (Clé)'] if col in df.columns]
    df = df.drop(columns=cols_to_drop, errors='ignore')
    
    # Tenter de trier par date
    if 'Date' in df.columns:
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
        df = df.sort_values(by='Date', ascending=False)
        df['Date'] = df['Date'].dt.strftime('%Y-%m-%d') # Reformatage pour l'affichage

    # S'assurer que 'Montant (€)' est formaté
    df['Montant (€)'] = df['Montant (€)'].round(2)
    
    return df[columns_order]

def df_to_excel_bytes(df):
    """Convertit un DataFrame en un fichier Excel en mémoire (bytes)."""
    output = io.BytesIO()
    wb = Workbook()
    ws = wb.active
    
    # Écrire les en-têtes (noms des colonnes)
    ws.append(list(df.columns))
    
    # Écrire les lignes du DataFrame
    for r_idx, r in enumerate(dataframe_to_rows(df, header=False, index=False)):
        # Commencer à la deuxième ligne car la première est l'en-tête
        for c_idx, value in enumerate(r):
            ws.cell(row=r_idx + 2, column=c_idx + 1, value=value)

    wb.save(output)
    return output.getvalue()


# -------------------------------------------------------------------
# --- Fonction Principale ---
# -------------------------------------------------------------------

def main():
    """Fonction principale de l'application Streamlit."""
    
    st.title("Comptabilité SMMD Alsace")

    # Vérification de l'authentification (si l'utilisateur n'est pas connecté)
    if not st.session_state.get('authenticated', False):
        # --- Interface de Connexion ---
        st.header("Connexion")
        
        col_login, col_create = st.columns(2)

        with col_login.form("login_form"):
            st.subheader("Accès Utilisateur")
            email_id = st.text_input("Identifiant (Ex: prenom.nom)", placeholder="Ex: jean.dupont")
            password = st.text_input("Mot de Passe", type="password")
            login_submitted = st.form_submit_button("Se connecter", type="primary")

            if login_submitted:
                if not db:
                    st.error("Base de données non initialisée.")
                    return

                try:
                    # L'ID du document est l'email_id
                    user_doc = db.collection(COL_USERS).document(email_id).get()
                    
                    if user_doc.exists:
                        user_data = user_doc.to_dict()
                        if check_password(password, user_data.get('password', '')):
                            st.session_state['authenticated'] = True
                            st.session_state['user_id'] = email_id
                            st.session_state['user_data'] = user_data
                            st.session_state['role'] = user_data.get('role', 'utilisateur')
                            st.session_state['house_id'] = user_data.get('house_id')
                            st.session_state['must_change_password'] = user_data.get('must_change_password', False)
                            st.success(f"Bienvenue, {user_data.get('first_name')}!")
                            st.rerun() # Recharger l'application pour afficher le contenu
                        else:
                            st.error("Mot de passe incorrect.")
                    else:
                        st.error("Nom d'utilisateur inconnu.")
                except Exception as e:
                    st.error(f"Erreur de connexion : {e}")
        
        # Le panneau de création n'est pas destiné à être utilisé en production sans vérification admin
        with col_create:
            st.subheader("Création de Compte (Admin)")
            st.warning("La création de compte est gérée dans l'interface d'administration.")
            st.caption(f"Note: Le mot de passe par défaut pour les nouveaux utilisateurs est : `{DEFAULT_PASSWORD}`")


    else:
        # --- Interface Connectée ---
        
        # Bouton de déconnexion
        if st.sidebar.button("Déconnexion", type="secondary"):
            st.session_state.clear()
            st.rerun()

        # Informations utilisateur dans la barre latérale
        st.sidebar.markdown(f"""
            **Connecté en tant que :** {st.session_state['user_data'].get('first_name')} 
            **Rôle :** {st.session_state['role'].capitalize()} 
            **Maison :** {get_house_name(st.session_state['house_id'])}
        """)
        st.sidebar.markdown("---")

        # Vérification du changement de mot de passe forcé
        if st.session_state.get('must_change_password', False):
            # L'utilisateur doit changer son mot de passe
            password_reset_interface(st.session_state['user_id'])
            
        else:
            # Redirection vers le tableau de bord ou l'interface d'administration
            user_role = st.session_state['role']
            
            # L'admin général et le chef de maison utilisent la même fonction admin_interface pour le menu latéral
            if user_role in ['admin', 'chef_de_maison']:
                admin_interface() 
            else: 
                user_dashboard() # Rôle 'utilisateur'
                

# -------------------------------------------------------------------
# --- Lancement de l'Application ---\r\n
# -------------------------------------------------------------------
if __name__ == '__main__':
    st.set_page_config(page_title="Comptabilité SMMD Alsace", layout="wide")
    main()