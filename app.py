import streamlit as st
import pandas as pd
import json
import os
from datetime import datetime
import hashlib
import bcrypt
import os
from firebase_admin import credentials, initialize_app, firestore

# --- Configuration & Constantes ---
# Variables d'environnement de l'environnement Canvas
APP_ID = os.environ.get('__app_id', 'compta-smmd-default')
USER_ID = os.environ.get('__user_id', 'unknown_user') 

# Chemins Firestore (Public Data pour la collaboration)
COL_USERS = f"artifacts/{APP_ID}/public/data/smmd_users"
COL_HOUSES = f"artifacts/{APP_ID}/public/data/smmd_houses"
COL_TRANSACTIONS = f"artifacts/{APP_ID}/public/data/smmd_transactions"
COL_ALLOCATIONS = f"artifacts/{APP_ID}/public/data/smmd_allocations"

# Constantes de l'application
ROLES = ["admin", "chef_de_maison", "normal"]
TITLES = ["Abbé", "Frère"]
PAYMENT_METHODS = ["CB Maison", "CB Personnelle (Avance)", "Chèque Personnel (Avance)", "Liquide Personnel (Avance)"]
HOUSE_PAYMENT_METHODS = ["CB Maison"]

# --- Initialisation Firebase ---
@st.cache_resource
def initialize_firebase():
    """Initialise Firebase Admin SDK avec les variables d'environnement de Canvas."""
    try:
        firebase_config_str = os.environ.get('__firebase_config')
        if not firebase_config_str:
            st.error("Erreur: Config Firebase introuvable.")
            return None
        
        firebase_config = json.loads(firebase_config_str)
        cred = credentials.Certificate(firebase_config)
        
        try:
            # Tente d'initialiser une nouvelle app
            app = initialize_app(cred, name=APP_ID)
        except ValueError:
            # Si elle est déjà initialisée, récupère l'instance existante
            import firebase_admin
            app = firebase_admin.get_app(name=APP_ID)
            
        return firestore.client(app=app)
    except Exception as e:
        st.error(f"Erreur d'initialisation de Firebase: {e}")
        return None

db = initialize_firebase()
if db is None:
    st.stop()


# --- Authentification & Utilisateurs ---
def hash_password(password):
    """Hache le mot de passe en utilisant Bcrypt."""
    password_bytes = password.encode('utf-8')
    # Utilise bcrypt.gensalt() pour générer un salt (intégré au hash)
    hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
    return hashed.decode('utf-8')

@st.cache_data
def get_all_users(refresh=False):
    """Récupère tous les utilisateurs (caché par défaut)."""
    try:
        docs = db.collection(COL_USERS).stream()
        return {doc.id: doc.to_dict() for doc in docs}
    except: return {}

def authenticate_user(username, password):
    """
    Vérifie les identifiants de l'utilisateur.
    
    CONTINGENCE SPÉCIALE (TEMPORAIRE):
    Permet la connexion immédiate de l'utilisateur 'admin' avec 
    le mot de passe 'florent1234', pour la première initialisation.
    
    CETTE LOGIQUE DOIT ÊTRE SUPPRIMÉE UNE FOIS LE COMPTE ADMIN VÉRITABLE CRÉÉ.
    
    :param username: Nom d'utilisateur saisi.
    :param password: Mot de passe saisi.
    :return: True si l'authentification réussit, False sinon.
    """
    
    # 🚨 1. BOOTSTRAP ADMIN CHECK (À supprimer après la première connexion) 🚨
    # Fournit un accès de secours pour l'initialisation du premier compte Admin sécurisé.
    if username == 'admin' and password == 'florent1234':
        st.session_state['logged_in'] = True
        # Initialise les données de session nécessaires pour l'interface
        st.session_state['user_data'] = {'first_name': 'Super', 'last_name': 'Admin', 'role': 'admin', 'username': 'admin'}
        st.session_state['user_id'] = 'admin' 
        st.session_state['role'] = 'admin'
        st.session_state['house_id'] = 'bootstrap_house_id' 
        st.toast("Connexion Admin de Secours Réussie ! Créez immédiatement un vrai compte Admin.", icon='🔑')
        return True

    try:
        # 2. Tentative de récupération et vérification standard (bcrypt)
        # Assurez-vous que COL_USERS et db sont correctement définis et initialisés dans app.py
        q = db.collection(COL_USERS).where('username', '==', username).limit(1).stream()
        user_doc = next(q, None)
        
        if not user_doc:
            return False # Utilisateur non trouvé
            
        user_data = user_doc.to_dict()
        stored_hash = user_data.get('password_hash', '').encode('utf-8')
        password_bytes = password.encode('utf-8')
        
        # Vérification Bcrypt standard (bcrypt doit être importé)
        if stored_hash and bcrypt.checkpw(password_bytes, stored_hash):
            # Succès de l'authentification
            st.session_state['logged_in'] = True
            st.session_state['user_data'] = user_data
            st.session_state['user_id'] = user_doc.id 
            st.session_state['role'] = user_data.get('role')
            st.session_state['house_id'] = user_data.get('house_id')
            return True
            
        # 3. Échec de l'authentification (mot de passe incorrect)
        return False
        
    except Exception as e: 
        print(f"Auth Error: {e}")
        return False

def logout():
    """Déconnecte l'utilisateur et recharge l'application."""
    st.session_state['logged_in'] = False
    st.session_state['user_data'] = {}
    st.session_state['role'] = None
    st.rerun()

# --- Transactions & Maisons (Récupération) ---

def save_transaction(house_id, user_id, type, amount, nature, payment_method=None, notes=None):
    """
    Enregistre une nouvelle transaction dans Firestore et retourne l'ID du document.
    """
    try:
        data = {
            'house_id': house_id, 'user_id': user_id, 'type': type,
            'amount': round(float(amount), 2), 'nature': nature,
            'payment_method': payment_method, 'created_at': datetime.now().isoformat(),
            'status': 'validé' if type != 'depense_avance' else 'en_attente_remboursement', 
            'month_year': datetime.now().strftime('%Y-%m') 
        }
        doc_ref = db.collection(COL_TRANSACTIONS).add(data)
        st.toast("Enregistré !", icon='✅')
        get_house_transactions.clear()
        return doc_ref.id # Retourne l'ID du document créé
    except Exception as e:
        st.error(f"Erreur: {e}")
        return None

@st.cache_data(ttl=60)
def get_house_transactions(house_id):
    """Récupère toutes les transactions d'une maison donnée."""
    try:
        query = db.collection(COL_TRANSACTIONS).where('house_id', '==', house_id).stream()
        return pd.DataFrame([d.to_dict() | {'doc_id': d.id} for d in query])
    except: return pd.DataFrame()

@st.cache_data
def get_all_houses():
    """Récupère toutes les maisons (villes) enregistrées."""
    try:
        docs = db.collection(COL_HOUSES).stream()
        return {doc.id: doc.to_dict() for doc in docs}
    except: return {}

@st.cache_data
def get_house_name(house_id):
    """Récupère le nom d'une maison à partir de son ID."""
    try:
        doc = db.collection(COL_HOUSES).document(house_id).get()
        return doc.to_dict().get('name', 'Inconnue') if doc.exists else 'Inconnue'
    except: return 'Inconnue'

def calculate_balances(df, uid):
    """Calcule le solde de la maison et le solde des avances de l'utilisateur."""
    recettes = df[df['type'].str.contains('recette')]['amount'].sum()
    depenses_maison = df[df['payment_method'] == 'CB Maison']['amount'].sum()
    house_bal = round(recettes - depenses_maison, 2)
    
    avances = df[(df['user_id'] == uid) & (df['type'] == 'depense_avance')]['amount'].sum()
    remb = df[(df['user_id'] == uid) & (df['type'] == 'depense_avance') & (df['status'] == 'remboursé')]['amount'].sum()
    perso_bal = round(avances - remb, 2)
    return house_bal, perso_bal

def set_monthly_allocation(user_id, house_id, amount):
    """
    Met à jour l'allocation mensuelle. 
    Cette modification remplace l'ancien montant dans la base de référence 
    (COL_ALLOCATIONS) pour les mois futurs et met à jour/crée la transaction 
    pour le mois en cours.
    """
    amount = round(float(amount), 2)
    
    # 1. Met à jour l'allocation de référence (COL_ALLOCATIONS). 
    # CELA ASSURE QUE CE MONTANT EST L'ALLOCATION PAR DÉFAUT POUR TOUS LES MOIS SUIVANTS.
    db.collection(COL_ALLOCATIONS).document(user_id).set({'amount': amount, 'updated': datetime.now().isoformat()})
    
    current_month = datetime.now().strftime('%Y-%m')
    u_name = st.session_state['user_data'].get('first_name', 'User')

    # 2. Cherche la transaction d'allocation pour le mois en cours.
    q = db.collection(COL_TRANSACTIONS).where('user_id', '==', user_id).where('month_year', '==', current_month).where('type', '==', 'recette_mensuelle').limit(1).stream()
    ex = next(q, None)
    
    if ex:
        # Met à jour la transaction existante pour le mois en cours (remplace l'ancien montant)
        db.collection(COL_TRANSACTIONS).document(ex.id).update({'amount': amount})
    else:
        # Crée une nouvelle transaction pour le mois en cours si elle n'existe pas encore
        save_transaction(house_id, user_id, 'recette_mensuelle', amount, f"Allocation Mensuelle de {u_name}")
        
    st.toast(f"Allocation mensuelle mise à jour à {amount}€ pour ce mois et les suivants.", icon="💸")
    # Invalide le cache des transactions pour refléter immédiatement le changement sur le tableau de bord
    get_house_transactions.clear() 
    st.rerun()

def update_transaction(doc_id, data):
    """Met à jour les champs d'une transaction dans Firestore."""
    try:
        db.collection(COL_TRANSACTIONS).document(doc_id).update(data)
        st.toast("Transaction mise à jour !", icon='✏️')
        get_house_transactions.clear()
        return True
    except Exception as e:
        st.error(f"Erreur de mise à jour: {e}")
        return False

def delete_transaction(doc_id):
    """Supprime une transaction par son ID de document."""
    try:
        db.collection(COL_TRANSACTIONS).document(doc_id).delete()
        st.toast("Supprimé !", icon='🗑️')
        get_house_transactions.clear() # Vider le cache de toutes les maisons
        st.rerun()
    except Exception as e: st.error(str(e))


# --- Fonctions de Suppression Admin ---

def delete_user(user_id):
    """Supprime un utilisateur de la collection COL_USERS."""
    try:
        doc_ref = db.collection(COL_USERS).document(user_id)
        if doc_ref.get().exists:
            doc_ref.delete()
            st.toast(f"Utilisateur {user_id} supprimé !", icon='🗑️')
            get_all_users.clear() # Invalide le cache des utilisateurs
            return True
        else:
            st.error(f"Utilisateur {user_id} introuvable.")
            return False
    except Exception as e:
        st.error(f"Erreur suppression utilisateur: {e}")
        return False

def delete_house(house_id):
    """
    Supprime une maison de la collection COL_HOUSES.
    ⚠️ Avertissement: Les transactions associées ne sont PAS supprimées.
    """
    try:
        doc_ref = db.collection(COL_HOUSES).document(house_id)
        if doc_ref.get().exists:
            doc_ref.delete()
            st.toast(f"Maison {house_id} supprimée !", icon='🗑️')
            
            # Invalide les caches liés aux maisons
            get_all_houses.clear()
            get_house_name.clear()
            get_house_transactions.clear() 
            st.rerun() 
            return True
        else:
            st.error(f"Maison {house_id} introuvable.")
            return False
    except Exception as e:
        st.error(f"Erreur suppression maison: {e}")
        return False

# ----------------------------------------------------
# --- Définition des Interfaces Utilisateur (User) ---
# ----------------------------------------------------

# ----------------------------------------------------
# --- Interface de Changement de Mot de Passe ---
# ----------------------------------------------------

# ATTENTION : La variable DEFAULT_PASSWORD est nécessaire pour informer l'utilisateur.
# Assurez-vous que cette valeur est la même que celle utilisée dans l'interface admin.
DEFAULT_PASSWORD = "first123"

def password_reset_interface(user_id):
    """Affiche une interface pour forcer l'utilisateur à changer son mot de passe."""
    
    user_info = st.session_state.get('user_data', {})
    
    st.title(f"🔒 Bienvenue, {user_info.get('first_name')} !")
    st.warning("⚠️ Pour votre sécurité, vous devez définir un nouveau mot de passe.")
    st.caption(f"Le mot de passe temporaire est : `{DEFAULT_PASSWORD}`. Ne l'utilisez pas comme nouveau mot de passe.")

    with st.form("reset_password_form"):
        new_pw = st.text_input("Nouveau mot de passe", type="password")
        confirm_pw = st.text_input("Confirmer le mot de passe", type="password")
        
        if st.form_submit_button("Changer mon mot de passe", type="primary"):
            if not new_pw or len(new_pw) < 6:
                st.error("Le nouveau mot de passe doit contenir au moins 6 caractères.")
            elif new_pw != confirm_pw:
                st.error("Les mots de passe ne correspondent pas.")
            else:
                try:
                    # Hacher le nouveau mot de passe
                    new_hash = hash_password(new_pw)
                    
                    # Mettre à jour Firestore
                    db.collection(COL_USERS).document(user_id).update({
                        'password_hash': new_hash,
                        'must_change_password': False, # Désactive la demande de changement
                        'updated_at': datetime.now().isoformat()
                    })
                    
                    # Mettre à jour l'état de la session
                    st.session_state['must_change_password'] = False
                    st.toast("Mot de passe mis à jour avec succès !", icon='✅')
                    st.rerun() # Recharger pour afficher le tableau de bord
                    
                except Exception as e:
                    st.error(f"Erreur lors de la mise à jour du mot de passe: {e}")


# ----------------------------------------------------
# --- Définition des Interfaces Utilisateur (User) ---
# ----------------------------------------------------

def user_dashboard(): # <<<< VÉRIFIEZ QUE CETTE LIGNE EST BIEN 'def user_dashboard():'
    """Affiche le tableau de bord de l'utilisateur standard."""
    # S'assurer que house_id n'est pas l'ID factice de bootstrap
    hid = st.session_state['house_id'] if st.session_state['house_id'] != 'bootstrap_house_id' else None
    
    if not hid:
        st.warning("Vous devez être affecté à une maison pour accéder au tableau de bord. Veuillez contacter l'administrateur.")
        return

    role = st.session_state['role']
    df = get_house_transactions(hid)
    h_bal, p_bal = calculate_balances(df, st.session_state['user_id']) if not df.empty else (0,0)
    
    st.title(f"🏠 {get_house_name(hid)}")
    c1, c2 = st.columns(2)
    c1.metric("Solde Maison", f"{h_bal} €")
    c2.metric("Vos Avances", f"{p_bal} €")
    
    tabs = ["Recettes", "Dépenses"]
    if role == 'chef_de_maison': tabs.append("Chef")
    
    t_list = st.tabs(tabs)
    
    # ... (le reste de la fonction user_dashboard)

# ----------------------------------------------------
# --- Définition des Interfaces Utilisateur (User) ---
# ----------------------------------------------------

def user_dashboard(): # <<<< VÉRIFIEZ QUE CETTE LIGNE EST BIEN 'def user_dashboard():'
    """Affiche le tableau de bord de l'utilisateur standard."""
    # S'assurer que house_id n'est pas l'ID factice de bootstrap
    hid = st.session_state['house_id'] if st.session_state['house_id'] != 'bootstrap_house_id' else None
    
    if not hid:
        st.warning("Vous devez être affecté à une maison pour accéder au tableau de bord. Veuillez contacter l'administrateur.")
        return

    role = st.session_state['role']
    df = get_house_transactions(hid)
    h_bal, p_bal = calculate_balances(df, st.session_state['user_id']) if not df.empty else (0,0)
    
    st.title(f"🏠 {get_house_name(hid)}")
    c1, c2 = st.columns(2)
    c1.metric("Solde Maison", f"{h_bal} €")
    c2.metric("Vos Avances", f"{p_bal} €")
    
    tabs = ["Recettes", "Dépenses"]
    if role == 'chef_de_maison': tabs.append("Chef")
    
    t_list = st.tabs(tabs)
    
    # ... (le reste de la fonction user_dashboard)

# ----------------------------------------------------
# --- Définition de l'Interface Admin ---
# ----------------------------------------------------
def admin_interface():
    st.header("👑 Admin")
    t1, t2, t3 = st.tabs(["Utilisateurs", "Maisons", "Audit"])
    
    # ---------------------------
    # T1: Utilisateurs (Création & Suppression)
    # ---------------------------
    with t1:
        st.subheader("Créer un nouvel utilisateur")
        with st.form("new_user"):
            c1, c2, c3 = st.columns(3)
            ti = c1.selectbox("Titre", TITLES)
            fn = c2.text_input("Prénom")
            ln = c3.text_input("Nom")
            pw = st.text_input("Mdp", type="password")
            houses = get_all_houses()
            h_opts = {v['name']: k for k, v in houses.items()}
            role = st.selectbox("Rôle", ROLES)
            house = st.selectbox("Maison", list(h_opts.keys()) if h_opts else ["-"])
            
            if st.form_submit_button("Créer"):
                uname = f"{fn.lower()}_{ln.lower()}"
                if pw:
                    db.collection(COL_USERS).document(uname).set({
                        'title': ti, 'first_name': fn, 'last_name': ln, 'username': uname,
                        'password_hash': hash_password(pw), 'role': role, 'house_id': h_opts.get(house)
                    })
                    get_all_users.clear()
                    st.success(f"Créé: {uname}")
                else:
                    st.error("Le mot de passe est requis.")

        st.markdown("---")
        st.subheader("Supprimer un utilisateur")
        all_users = get_all_users()
        user_opts = {f"{u_data.get('first_name', 'N/A')} {u_data.get('last_name', 'N/A')} ({k})": k 
                     for k, u_data in all_users.items()}
        
        if user_opts:
            user_to_delete_display = st.selectbox("Utilisateur à supprimer", list(user_opts.keys()), key="del_user_select")
            user_to_delete_id = user_opts[user_to_delete_display]
            
            # Empêcher l'admin de se supprimer lui-même
            if user_to_delete_id == st.session_state.get('user_id'):
                st.warning("Vous ne pouvez pas supprimer votre propre compte.")
            else:
                if st.button(f"🗑️ Confirmer la suppression de l'utilisateur", key="del_user_btn"):
                    delete_user(user_to_delete_id)
                    st.rerun() 
        else:
            st.info("Aucun utilisateur à supprimer.")


    # ---------------------------
    # T2: Maisons (Création & Suppression)
    # ---------------------------
    with t2:
        st.subheader("Créer une nouvelle maison")
        with st.form("new_house"):
            name = st.text_input("Nom Ville")
            if st.form_submit_button("Créer"):
                hid = name.lower().replace(' ', '_')
                db.collection(COL_HOUSES).document(hid).set({'name': name})
                get_all_houses.clear()
                st.rerun()

        st.markdown("---")
        st.subheader("Supprimer une maison")
        houses = get_all_houses()
        h_opts = {v['name']: k for k, v in houses.items()}
        
        if h_opts:
            house_to_delete_name = st.selectbox("Maison à supprimer", list(h_opts.keys()), key="del_house_select")
            house_to_delete_id = h_opts[house_to_delete_name]

            st.error("⚠️ Cette action est IRRÉVERSIBLE et ne supprime **PAS** les transactions liées dans Firestore. Vous devrez les supprimer manuellement ou les réaffecter.")

            if st.button(f"🗑️ Confirmer la suppression de la maison '{house_to_delete_name}'", key="del_house_btn"):
                delete_house(house_to_delete_id)
        else:
            st.info("Aucune maison à supprimer.")


    # ---------------------------
    # T3: Audit (Modification/Suppression des Transactions)
    # ---------------------------
    with t3:
        st.subheader("Audit des Transactions et Opérations")

        # 1. Récupération de TOUTES les transactions
        all_tx_stream = db.collection(COL_TRANSACTIONS).stream()
        all_tx = [d.to_dict() | {'doc_id': d.id} for d in all_tx_stream]

        if not all_tx:
            st.info("Aucune transaction enregistrée.")
            return

        df_all_tx = pd.DataFrame(all_tx)
        
        # Mapping pour l'affichage (récupère les caches existants)
        house_map = {k: v['name'] for k, v in get_all_houses().items()}
        all_users_data = get_all_users()
        user_map = {k: f"{v.get('first_name', 'N/A')} {v.get('last_name', 'N/A')} ({v.get('username', 'N/A')})" for k, v in all_users_data.items()}
        
        df_all_tx['house_name'] = df_all_tx['house_id'].map(house_map).fillna('N/A')
        df_all_tx['user_name'] = df_all_tx['user_id'].map(user_map).fillna('N/A')
        
        # Colonnes à afficher pour l'audit
        display_cols = ['doc_id', 'created_at', 'house_name', 'user_name', 'type', 'amount', 'nature', 'payment_method', 'status']
        st.dataframe(df_all_tx[display_cols], use_container_width=True, height=300)

        # 2. Section Modification/Suppression
        st.markdown("---")
        st.subheader("Modifier / Supprimer une Transaction")
        
        # Création des options de sélection
        tx_options = {f"{row['created_at'][:10]} - {row['nature']} ({row['amount']}€) - ID: {row['doc_id']}": row['doc_id'] 
                      for _, row in df_all_tx.sort_values(by='created_at', ascending=False).iterrows()}
        
        selected_tx_key = st.selectbox(
            "Sélectionner la Transaction", 
            list(tx_options.keys()), 
            key="audit_tx_select"
        )
        selected_doc_id = tx_options[selected_tx_key]
        
        # Récupérer les données de la transaction sélectionnée
        selected_tx_data = df_all_tx[df_all_tx['doc_id'] == selected_doc_id].iloc[0].to_dict()
        
        st.caption(f"ID du Document sélectionné : `{selected_doc_id}`")
        
        # 2a. Formulaire de Modification
        st.markdown("##### ✏️ Modification")
        
        # Définir toutes les options possibles (pour éviter les erreurs d'index)
        ALL_TRANSACTION_TYPES = ['recette_mensuelle', 'recette_exceptionnelle', 'depense_maison', 'depense_avance']
        ALL_STATUS = ['validé', 'en_attente_remboursement', 'remboursé']
        
        # S'assurer que les valeurs par défaut existent dans les listes d'options
        default_type = selected_tx_data.get('type')
        default_method = selected_tx_data.get('payment_method')
        default_status = selected_tx_data.get('status', 'validé')

        with st.form("edit_tx_form"):
            c1, c2 = st.columns(2)
            new_amount = c1.number_input("Montant (EUR)", value=float(selected_tx_data['amount']), key="edit_amount")
            new_nature = c2.text_input("Nature", value=selected_tx_data['nature'], key="edit_nature")
            
            c3, c4 = st.columns(2)
            new_type = c3.selectbox("Type", ALL_TRANSACTION_TYPES, index=ALL_TRANSACTION_TYPES.index(default_type) if default_type in ALL_TRANSACTION_TYPES else 2, key="edit_type")
            new_method = c4.selectbox("Moyen de Paiement", PAYMENT_METHODS, index=PAYMENT_METHODS.index(default_method) if default_method in PAYMENT_METHODS else 0, key="edit_method")
            
            new_status = st.selectbox("Statut", ALL_STATUS, index=ALL_STATUS.index(default_status) if default_status in ALL_STATUS else 0, key="edit_status")
            
            if st.form_submit_button("Sauvegarder les Modifications", type="primary"):
                update_data = {
                    'amount': round(float(new_amount), 2),
                    'nature': new_nature,
                    'type': new_type,
                    'payment_method': new_method,
                    'status': new_status,
                    'updated_at': datetime.now().isoformat()
                }
                update_transaction(selected_doc_id, update_data)
                st.rerun()

        # 2b. Bouton de Suppression
        st.markdown("##### 🗑️ Suppression")
        st.error(f"La suppression est définitive pour la transaction : {selected_doc_id}")
        if st.button(f"Supprimer la Transaction sélectionnée ({selected_tx_data['nature']})", key="delete_tx_btn"):
            delete_transaction(selected_doc_id) # Utilise la fonction existante
            # st.rerun() est dans delete_transaction


# ----------------------------------------------------
# --- Définition du Style CSS pour le bandeau rouge ---
# ----------------------------------------------------
def set_red_theme_band():
    """Injecte du CSS pour colorer le bandeau supérieur en rouge."""
    st.markdown("""
    <style>
    /* Change la couleur de fond de la barre latérale */
    [data-testid="stSidebar"] {
        background-color: #f0f2f6; /* Laisse la barre latérale claire */
    }

    /* Change la couleur de fond du bandeau principal (où se trouve le hamburger menu) */
    [data-testid="stHeader"] {
        background-color: #FF4B4B; /* Rouge vif de Streamlit */
    }
    /* S'assurer que le texte/logo dans le bandeau reste visible (Correction de l'accolade) */
    [data-testid="stHeader"] .st-emotion-cache-18ni91u, 
    [data-testid="stHeader"] .st-emotion-cache-12qukfr {
        color: white; 
    }
    </style>
    """, unsafe_allow_html=True)


# ----------------------------------------------------
# --- Main Loop (Démarrage de l'Application) ---
# ----------------------------------------------------
if __name__ == '__main__':
    st.set_page_config(page_title="Compta Smmd", page_icon="💰", layout="wide")
    
    # 🎨 Appel du style pour le bandeau rouge
    set_red_theme_band() 
    
    if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
    
    if st.session_state['logged_in']:
        # Barre latérale pour la déconnexion
        with st.sidebar:
            st.write(f"Connecté: {st.session_state['user_data'].get('first_name')} ({st.session_state['role']})")
            if st.button("Déconnexion", key="sidebar_logout"): logout()

        # Affichage de l'interface appropriée
        if st.session_state['role'] == 'admin':
            admin_interface()
        else:
            user_dashboard()
    else:
        # Interface de Connexion
        st.title("Connexion")
        u = st.text_input("Nom d'utilisateur (prenom_nom)")
        p = st.text_input("Mot de passe", type="password")
        if st.button("Se connecter", key="login_btn"):
            if authenticate_user(u, p): 
                st.toast("Connexion réussie !", icon='🥳')
                st.rerun()
            else: 
                st.error("Nom d'utilisateur ou mot de passe incorrect.")