import streamlit as st
import os
import json
from firebase_admin import initialize_app, credentials, firestore, exceptions
from datetime import datetime
import pandas as pd
import bcrypt
from functools import lru_cache # Pour le caching des fonctions de BDD

# -------------------------------------------------------------------
# --- Constantes globales
# -------------------------------------------------------------------

# Ces ID devraient correspondre aux chemins de base définis par les règles de sécurité
COL_TRANSACTIONS = 'transactions'
COL_HOUSES = 'houses'
COL_USERS = 'users'
COL_ALLOCATIONS = 'allocations' # Pour l'allocation mensuelle de référence

# Liste des méthodes de paiement et des rôles pour les formulaires
PAYMENT_METHODS = ['carte', 'virement', 'liquide', 'autre']
ROLES = ['admin', 'utilisateur', 'chef_de_maison']
TITLES = ['M.', 'Mme']
# Le mot de passe par défaut pour les nouveaux utilisateurs
DEFAULT_PASSWORD = "first123" 


# -------------------------------------------------------------------
# --- Configuration et Initialisation de Firebase
# -------------------------------------------------------------------

# Récupération de la configuration Firebase à partir des variables d'environnement
firebase_config_str = os.environ.get('FIREBASE_CONFIG')

if not firebase_config_str:
    # Condition de sécurité: Arrêter si la configuration critique est manquante.
    st.error("Erreur de configuration: La variable d'environnement 'FIREBASE_CONFIG' est introuvable. Veuillez la configurer.")
    st.stop()
    
try:
    firebase_config = json.loads(firebase_config_str)
except json.JSONDecodeError:
    st.error("Erreur de configuration: La variable 'FIREBASE_CONFIG' n'est pas un JSON valide.")
    st.stop()


@st.cache_resource
def initialize_firebase_connection():
    """
    Initialise l'application Firebase et retourne le client Firestore.
    Utilise @st.cache_resource pour s'assurer que cette fonction ne s'exécute qu'une seule fois 
    lors des réexécutions de Streamlit.
    """
    try:
        # Récupère l'ID de l'application (nom d'instance)
        app_id = firebase_config.get('app_id', 'default-smmd-app')
        
        # 1. Vérifie si l'application existe déjà.
        from firebase_admin import get_app
        try:
            # Tente de récupérer l'instance existante
            app = get_app(app_id)
            # st.toast(f"Réutilisation de l'instance Firebase: {app_id}")
        except ValueError:
            # 2. Si elle n'existe pas, l'initialise.
            cred = credentials.Certificate(firebase_config)
            app = initialize_app(cred, name=app_id)
            # st.toast(f"Nouvelle initialisation de l'instance Firebase: {app_id}")
        
        # Retourne le client Firestore
        return firestore.client(app=app)
        
    except Exception as e:
        st.error(f"Erreur d'initialisation Firebase : {e}")
        st.stop() # Arrêter l'exécution en cas d'échec critique

# --- Initialisation du Client Firestore (Utilise la fonction mise en cache)
db = initialize_firebase_connection()


# -------------------------------------------------------------------
# --- Fonctions Utilitaires (Hachage, Caching BDD)
# -------------------------------------------------------------------

def hash_password(password):
    """Hache un mot de passe en utilisant Bcrypt."""
    password_bytes = password.encode('utf-8')
    # bcrypy.gensalt() génère un sel aléatoire
    hashed_bytes = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
    return hashed_bytes.decode('utf-8')

def check_password(password, hashed_password):
    """Vérifie un mot de passe en clair avec son hash Bcrypt."""
    password_bytes = password.encode('utf-8')
    hashed_bytes = hashed_password.encode('utf-8')
    return bcrypt.checkpw(password_bytes, hashed_bytes)

@st.cache_data
def get_all_users():
    """Récupère tous les utilisateurs (pour Admin)"""
    users_stream = db.collection(COL_USERS).stream()
    return {d.id: d.to_dict() for d in users_stream}

@st.cache_data
def get_all_houses():
    """Récupère toutes les maisons (pour Admin)"""
    houses_stream = db.collection(COL_HOUSES).stream()
    return {d.id: d.to_dict() for d in houses_stream}

def get_house_name(house_id):
    """Récupère le nom d'une maison à partir de son ID (utilise le cache)"""
    return get_all_houses().get(house_id, {}).get('name', 'Maison Inconnue')

@st.cache_data(ttl=600) # Cache de 10 minutes pour les transactions
def get_house_transactions(house_id):
    """Récupère toutes les transactions pour une maison donnée."""
    if not house_id:
        return pd.DataFrame()
        
    try:
        # Récupère les transactions liées à la house_id de l'utilisateur
        q = db.collection(COL_TRANSACTIONS).where('house_id', '==', house_id).stream()
        data = [d.to_dict() | {'doc_id': d.id} for d in q]
        
        if not data:
            return pd.DataFrame()
            
        df = pd.DataFrame(data)
        # Convertir les dates pour le tri
        df['created_at_dt'] = pd.to_datetime(df['created_at'])
        # Trier par date
        return df.sort_values(by='created_at_dt', ascending=False).drop(columns=['created_at_dt'])
        
    except exceptions.NotFound:
        return pd.DataFrame()
    except Exception as e:
        st.error(f"Erreur lors de la récupération des transactions: {e}")
        return pd.DataFrame()


# -------------------------------------------------------------------
# --- Fonctions CRUD et Logique (Corps complets pour la démo)
# -------------------------------------------------------------------

def save_transaction(house_id, user_id, type, amount, nature, payment_method=None, notes=None):
    """Enregistre une nouvelle transaction dans Firestore."""
    try:
        data = {
            'house_id': house_id, 
            'user_id': user_id, 
            'type': type, # 'depense', 'recette_mensuelle', 'depense_avance', 'remboursement'
            'amount': round(float(amount), 2), 
            'nature': nature,
            'payment_method': payment_method, 
            'created_at': datetime.now().isoformat(),
            'status': 'validé' if type != 'depense_avance' else 'en_attente_remboursement', 
            'month_year': datetime.now().strftime('%Y-%m') 
        }
        # Ajout du document à la collection
        doc_ref = db.collection(COL_TRANSACTIONS).add(data)
        st.toast("Transaction enregistrée !", icon='✅')
        # Invalide le cache des transactions pour forcer un rechargement
        get_house_transactions.clear()
        return doc_ref.id 
    except Exception as e:
        st.error(f"Erreur lors de l'enregistrement de la transaction : {e}")
        return None

def update_transaction(doc_id, data):
    """Met à jour une transaction existante."""
    try:
        db.collection(COL_TRANSACTIONS).document(doc_id).update(data)
        st.toast("Transaction mise à jour !", icon='✏️')
        get_house_transactions.clear()
        return True
    except Exception as e:
        st.error(f"Erreur de mise à jour de la transaction : {e}")
        return False

def delete_transaction(doc_id):
    """Supprime une transaction."""
    try:
        db.collection(COL_TRANSACTIONS).document(doc_id).delete()
        st.toast("Transaction supprimée !", icon='🗑️')
        get_house_transactions.clear() 
        return True
    except Exception as e: st.error(f"Erreur de suppression de transaction : {e}")
    
def set_monthly_allocation(user_id, house_id, amount):
    """Définit ou met à jour l'allocation mensuelle d'un utilisateur et crée/met à jour la recette correspondante."""
    try:
        amount = round(float(amount), 2)
        # 1. Mettre à jour l'enregistrement d'allocation pour l'utilisateur
        db.collection(COL_ALLOCATIONS).document(user_id).set({'amount': amount, 'house_id': house_id, 'updated': datetime.now().isoformat()})
        
        # 2. Mettre à jour ou créer la transaction de 'recette_mensuelle' pour le mois en cours
        current_month = datetime.now().strftime('%Y-%m')
        user_name = st.session_state['user_data'].get('first_name', user_id)
        
        # Recherche de la transaction de recette existante pour ce mois
        q = db.collection(COL_TRANSACTIONS).where('user_id', '==', user_id).where('month_year', '==', current_month).where('type', '==', 'recette_mensuelle').limit(1).stream()
        existing_tx = next(q, None)
        
        if existing_tx:
            # Si elle existe, la mettre à jour
            db.collection(COL_TRANSACTIONS).document(existing_tx.id).update({'amount': amount})
        else:
            # Sinon, créer une nouvelle transaction de recette
            save_transaction(house_id, user_id, 'recette_mensuelle', amount, f"Allocation Mensuelle de {user_name} (Mois en cours)", payment_method='virement')
            
        st.toast(f"Allocation mensuelle mise à jour à {amount}€ pour ce mois.", icon="💸")
        get_house_transactions.clear() 
        return True
    except Exception as e: st.error(f"Erreur lors de la mise à jour de l'allocation: {e}")
    

def calculate_balances(df, current_user_id):
    """
    Calcule le solde total de la maison et le solde personnel de l'utilisateur
    (Fonction simplifiée pour la démo, nécessite une logique financière plus robuste en production).
    """
    if df.empty:
        return 0.00, 0.00
    
    # Simuler le solde de la maison (Recettes - Dépenses)
    house_balance = df[df['type'].str.contains('recette')]['amount'].sum() - df[df['type'].str.contains('depense')]['amount'].sum()
    
    # Solde personnel (argent avancé par l'utilisateur - argent dépensé par l'utilisateur)
    # Ceci est très simplifié
    user_contributions = df[(df['user_id'] == current_user_id) & (df['type'].str.contains('recette'))]['amount'].sum()
    user_expenses = df[(df['user_id'] == current_user_id) & (df['type'].str.contains('depense'))]['amount'].sum()
    user_balance = user_contributions - user_expenses
    
    return round(house_balance, 2), round(user_balance, 2)


def delete_user(user_id):
    """Supprime un utilisateur."""
    try:
        db.collection(COL_USERS).document(user_id).delete()
        st.toast(f"Utilisateur {user_id} supprimé.", icon='🗑️')
        get_all_users.clear()
        st.rerun()
        return True
    except Exception as e: st.error(f"Erreur de suppression d'utilisateur: {e}")

def delete_house(house_id):
    """Supprime une maison."""
    try:
        db.collection(COL_HOUSES).document(house_id).delete()
        st.toast(f"Maison {house_id} supprimée.", icon='🗑️')
        get_all_houses.clear()
        st.rerun()
        return True
    except Exception as e: st.error(f"Erreur de suppression de maison: {e}")


# -------------------------------------------------------------------
# --- Interfaces Utilisateur
# -------------------------------------------------------------------

def password_reset_interface(user_id):
    """Interface pour forcer un changement de mot de passe à la première connexion."""
    st.title("🔒 Premier Mot de Passe: Changement Obligatoire")
    st.warning("Pour des raisons de sécurité, veuillez définir un nouveau mot de passe.")
    
    new_password = st.text_input("Nouveau Mot de Passe", type="password", key="new_pw_reset")
    confirm_password = st.text_input("Confirmer le Nouveau Mot de Passe", type="password", key="confirm_pw_reset")

    if st.button("Changer le Mot de Passe"):
        if new_password != confirm_password:
            st.error("Les mots de passe ne correspondent pas.")
        elif len(new_password) < 6:
            st.error("Le mot de passe doit contenir au moins 6 caractères.")
        else:
            try:
                # 1. Hacher le nouveau mot de passe
                hashed_new_password = hash_password(new_password)
                
                # 2. Mettre à jour Firestore
                db.collection(COL_USERS).document(user_id).update({
                    'password_hash': hashed_new_password,
                    'must_change_password': False # Désactiver l'obligation de changement
                })
                
                st.success("Mot de passe mis à jour avec succès! Veuillez vous reconnecter.")
                # Déconnecter l'utilisateur après le changement de mot de passe
                st.session_state.clear()
                st.rerun()
                
            except Exception as e:
                st.error(f"Erreur lors de la mise à jour du mot de passe: {e}")

def user_dashboard(): 
    """Affiche le tableau de bord de l'utilisateur pour la gestion des dépenses."""
    user_data = st.session_state['user_data']
    house_id = st.session_state['house_id']
    user_id = st.session_state['user_id']
    house_name = get_house_name(house_id)
    
    st.title(f"🏠 Gestion pour {house_name}")
    st.header(f"Bonjour, {user_data.get('first_name', 'Utilisateur')}!")

    # 1. Récupération des données
    df_transactions = get_house_transactions(house_id)
    house_balance, user_balance = calculate_balances(df_transactions, user_id)

    # 2. Affichage des soldes
    col_h_bal, col_u_bal = st.columns(2)
    
    with col_h_bal:
        st.metric(label="Solde du Foyer", value=f"{house_balance:,.2f} €", 
                  delta="Solde estimé de la caisse commune (Recettes - Dépenses)")
        
    with col_u_bal:
        # Affiche le solde personnel
        st.metric(label="Mon Solde Personnel", value=f"{user_balance:,.2f} €", 
                  delta_color="off", help="Vos avances/recettes personnelles vs vos dépenses")

    st.markdown("---")

    # 3. Formulaire pour ajouter une transaction
    with st.expander("➕ Ajouter une nouvelle dépense/recette", expanded=False):
        with st.form("new_transaction_form", clear_on_submit=True):
            st.subheader("Détails de la Transaction")
            
            col1, col2 = st.columns(2)
            with col1:
                nature = st.text_input("Nature de la Transaction (ex: Courses alimentaires, Loyer, Câble)", required=True)
                amount = st.number_input("Montant (€)", min_value=0.01, format="%.2f", required=True)
            with col2:
                tx_type = st.radio("Type de Mouvement", 
                                   options=['depense', 'recette_mensuelle', 'depense_avance'], 
                                   format_func=lambda x: x.replace('_', ' ').capitalize(), 
                                   horizontal=True)
                payment_method = st.selectbox("Méthode de Paiement (si dépense)", PAYMENT_METHODS)
            
            notes = st.text_area("Notes additionnelles (facultatif)")
            
            if st.form_submit_button("Enregistrer la Transaction"):
                # Simuler la logique de 'recette_mensuelle' pour ne pas la laisser manuelle
                if tx_type == 'recette_mensuelle':
                    st.error("La 'Recette Mensuelle' est gérée via l'allocation (section Admin/Chef de Maison). Veuillez choisir Dépense ou Avance.")
                else:
                    save_transaction(house_id, user_id, tx_type, amount, nature, payment_method, notes)
                    # Forcer l'actualisation du tableau après l'ajout
                    st.rerun() 

    st.markdown("---")
    
    # 4. Affichage des Transactions
    st.subheader("Historique des Transactions")
    if df_transactions.empty:
        st.info("Aucune transaction enregistrée pour l'instant.")
    else:
        # Nettoyer le DataFrame pour l'affichage
        display_df = df_transactions.copy()
        display_df['amount'] = display_df['amount'].apply(lambda x: f"{x:,.2f} €")
        display_df['created_at'] = pd.to_datetime(display_df['created_at']).dt.strftime('%d/%m/%Y %H:%M')
        display_df['type'] = display_df['type'].str.replace('_', ' ').str.capitalize()
        
        # Renommer les colonnes pour l'affichage
        display_df = display_df.rename(columns={
            'created_at': 'Date',
            'nature': 'Description',
            'type': 'Type',
            'amount': 'Montant',
            'user_id': 'Par',
            'payment_method': 'Méthode'
        })
        
        # Sélectionner et ordonner les colonnes pertinentes
        cols_to_display = ['Date', 'Description', 'Montant', 'Type', 'Par', 'Méthode', 'status', 'doc_id']
        st.dataframe(display_df[cols_to_display], use_container_width=True, hide_index=True)


def admin_interface():
    """Affiche l'interface Admin pour la gestion des utilisateurs et des maisons."""
    st.title("👑 Panneau d'Administration")
    st.warning("⚠️ Ceci est une simulation. Le code complet est omis pour la clarté mais la structure est en place.")
    
    tab1, tab2, tab3 = st.tabs(["Gestion Utilisateurs", "Gestion Foyers", "Paramètres Allocation"])
    
    # Simuler le contenu de la gestion des utilisateurs
    with tab1:
        st.header("Utilisateurs Actuels")
        users = get_all_users()
        users_df = pd.DataFrame(users.values(), index=users.keys())
        if not users_df.empty:
            st.dataframe(users_df[['first_name', 'last_name', 'role', 'house_id', 'must_change_password']], use_container_width=True)
            
        st.markdown("---")
        st.subheader("Ajouter un Nouvel Utilisateur")
        with st.form("new_user_form", clear_on_submit=True):
            col_u1, col_u2, col_u3 = st.columns(3)
            with col_u1:
                new_uid = st.text_input("ID Utilisateur (Login)", required=True)
                first_name = st.text_input("Prénom", required=True)
            with col_u2:
                last_name = st.text_input("Nom", required=True)
                role = st.selectbox("Rôle", ROLES)
            with col_u3:
                title = st.selectbox("Titre", TITLES)
                house_id = st.selectbox("Foyer Associé", get_all_houses().keys(), format_func=get_house_name)
                
            if st.form_submit_button("Créer l'Utilisateur"):
                if db.collection(COL_USERS).document(new_uid).get().exists:
                    st.error("Cet ID Utilisateur existe déjà.")
                else:
                    new_user_data = {
                        'first_name': first_name,
                        'last_name': last_name,
                        'title': title,
                        'role': role,
                        'house_id': house_id,
                        'password_hash': hash_password(DEFAULT_PASSWORD), # Hachage du mot de passe par défaut
                        'must_change_password': True, # Forcer le changement à la première connexion
                        'created_at': datetime.now().isoformat()
                    }
                    try:
                        db.collection(COL_USERS).document(new_uid).set(new_user_data)
                        st.success(f"Utilisateur {new_uid} créé avec le mot de passe par défaut : {DEFAULT_PASSWORD}")
                        get_all_users.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erreur de création: {e}")

    # Simuler le contenu de la gestion des foyers
    with tab2:
        st.header("Foyers Actuels")
        houses = get_all_houses()
        houses_df = pd.DataFrame(houses.values(), index=houses.keys())
        if not houses_df.empty:
            st.dataframe(houses_df, use_container_width=True)

        st.markdown("---")
        st.subheader("Ajouter un Nouveau Foyer")
        with st.form("new_house_form", clear_on_submit=True):
            house_id = st.text_input("ID Foyer (Unique)", required=True)
            house_name = st.text_input("Nom du Foyer (Ex: Maison Bleue)", required=True)
            
            if st.form_submit_button("Créer le Foyer"):
                if db.collection(COL_HOUSES).document(house_id).get().exists:
                    st.error("Cet ID de Foyer existe déjà.")
                else:
                    try:
                        db.collection(COL_HOUSES).document(house_id).set({'name': house_name, 'created_at': datetime.now().isoformat()})
                        st.success(f"Foyer '{house_name}' créé.")
                        get_all_houses.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erreur de création: {e}")

    # Simuler le contenu de l'allocation mensuelle
    with tab3:
        st.header("Définir l'Allocation Mensuelle")
        st.info("Cette allocation sera utilisée pour générer automatiquement la recette mensuelle de l'utilisateur.")
        
        users = get_all_users()
        user_ids = list(users.keys())
        
        if user_ids:
            selected_user_id = st.selectbox("Sélectionner l'Utilisateur", user_ids, format_func=lambda uid: f"{users[uid].get('first_name', uid)} ({uid})")
            
            allocation_amount = st.number_input(f"Allocation (€) pour {users[selected_user_id].get('first_name')}", min_value=0.00, format="%.2f", key="allocation_input")
            
            if st.button("Mettre à jour l'Allocation"):
                if selected_user_id and users.get(selected_user_id, {}).get('house_id'):
                    set_monthly_allocation(selected_user_id, users[selected_user_id]['house_id'], allocation_amount)
                else:
                    st.error("Veuillez sélectionner un utilisateur ou vérifier que l'utilisateur a un foyer associé.")


# -------------------------------------------------------------------
# --- Logique d'Authentification et Flux Principal
# -------------------------------------------------------------------

def authentication_and_main_flow():
    """Gère l'authentification et l'affichage de l'interface principale."""
    
    # 1. Vérification et initialisation de l'état de la session
    if 'logged_in' not in st.session_state:
        st.session_state['logged_in'] = False
        st.session_state['role'] = None
        st.session_state['user_id'] = None
        st.session_state['house_id'] = None
        st.session_state['user_data'] = {}
        st.session_state['must_change_password'] = False


    # 2. Formulaire de Connexion
    if not st.session_state['logged_in']:
        
        st.header("Connexion au Portail de Gestion")
        
        # Pour la démo, on utilise l'ID utilisateur comme clé de document Firestore
        with st.form("login_form"):
            st.subheader("Identifiez-vous")
            username = st.text_input("Nom d'utilisateur (votre ID unique)", key="login_username_input")
            password = st.text_input("Mot de passe", type="password", key="login_password_input") 
            
            if st.form_submit_button("Se Connecter", type="primary"):
                # Récupérer les données de l'utilisateur
                try:
                    # L'ID utilisateur est le nom du document dans la collection USERS
                    user_doc = db.collection(COL_USERS).document(username).get()
                    if user_doc.exists:
                        user_data = user_doc.to_dict()
                        hashed_pw = user_data.get('password_hash', '')
                        
                        # Vérification du mot de passe
                        if check_password(password, hashed_pw):
                            # Connexion réussie : mettre à jour la session
                            st.session_state['logged_in'] = True
                            st.session_state['user_id'] = username
                            st.session_state['user_data'] = user_data
                            st.session_state['role'] = user_data.get('role', 'utilisateur')
                            st.session_state['house_id'] = user_data.get('house_id')
                            st.session_state['must_change_password'] = user_data.get('must_change_password', False)

                            st.success(f"Bienvenue, {user_data.get('first_name')}!")
                            st.rerun()
                        else:
                            st.error("Mot de passe incorrect.")
                    else:
                        st.error("Nom d'utilisateur inconnu.")
                except Exception as e:
                    st.error(f"Erreur de connexion : {e}")
        
        # Afficher la note sur le mot de passe par défaut
        st.caption(f"Note: Pour les nouveaux utilisateurs, le mot de passe initial est : `{DEFAULT_PASSWORD}`")


    # 3. Logique post-connexion
    else:
        # Bouton de Déconnexion dans la barre latérale
        if st.sidebar.button("Déconnexion", type="secondary"):
            st.session_state.clear()
            st.rerun()

        # Affichage du statut
        st.sidebar.markdown(f"""
            **Connecté en tant que :** {st.session_state['user_data'].get('first_name')}  
            **Rôle :** {st.session_state['role'].capitalize()}  
            **Foyer :** {get_house_name(st.session_state['house_id'])}
        """)
        st.sidebar.markdown("---")

        # Si l'utilisateur doit changer son mot de passe
        if st.session_state.get('must_change_password', False):
            password_reset_interface(st.session_state['user_id'])
            
        # Sinon, afficher l'interface principale selon le rôle
        else:
            if st.session_state['role'] == 'admin':
                admin_interface()
            # Les autres rôles (utilisateur, chef_de_maison) ont le même tableau de bord pour l'instant
            else:
                user_dashboard()

# -------------------------------------------------------------------
# --- Lancement de l'Application ---
# -------------------------------------------------------------------
if __name__ == '__main__':
    st.set_page_config(page_title="SM Mediadrive", layout="wide", initial_sidebar_state="expanded")
    authentication_and_main_flow()