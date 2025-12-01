import streamlit as st
import os
import json
import pandas as pd
from datetime import datetime
from functools import lru_cache

# --- Dépendances à Simuler/Assumer pour un fichier complet ---
# Dans une application réelle, vous auriez besoin des packages suivants dans requirements.txt:
# streamlit
# pandas
# firebase-admin (ou google-cloud-firestore pour un usage plus spécifique)
# bcrypt
# -----------------------------------------------------------

# Simulation de l'import Firebase et Bcrypt
try:
    import bcrypt
    # Simulation de l'import Firebase (si l'utilisateur utilise un setup standard)
    from firebase_admin import credentials, initialize_app, firestore
    
    # 🚨 Initialisation de la base de données (basée sur l'environnement Canvas) 🚨
    # Récupération de la configuration Firebase et de l'ID d'application
    firebase_config_str = os.environ.get('__firebase_config')
    app_id = os.environ.get('__app_id', 'default-smmd-app')

    if firebase_config_str and firebase_config_str != '{}':
        # Convertir la chaîne JSON en dictionnaire
        firebase_config = json.loads(firebase_config_str)

        # Chercher les clés nécessaires pour l'initialisation du SDK Admin (Service Account)
        # On suppose que la configuration est stockée dans une variable d'environnement ou est chargée
        
        # Le code ici est simplifié pour un environnement qui passe la config comme un dict
        # Dans un environnement réel comme Render ou Streamlit Cloud, on utilise un secret
        # contenant les clés du compte de service.
        
        # Si vous utilisez Streamlit Cloud/Render, vous devez fournir les clés 
        # du compte de service (Service Account) en tant que secrets.
        
        try:
            # Tente de charger les identifiants depuis la chaîne de configuration
            if 'private_key' in firebase_config:
                cred = credentials.Certificate(firebase_config)
                if not initialize_app(cred, name=app_id):
                    initialize_app(cred, name=app_id) # Si non initialisé
                db = firestore.client(app=initialize_app(cred, name=app_id))
            else:
                # Si ce n'est pas un Service Account, cela échouera si l'app n'est pas déjà initialisée.
                # On assume que la config permet au moins un firestore.client() si l'app est lancée.
                try:
                    db = firestore.client()
                except Exception:
                     # Fallback si l'initialisation a échoué (souvent dans l'environnement local)
                     st.error("Échec de l'initialisation de Firestore. Vérifiez les secrets.")
                     db = None 

        except ValueError as e:
            # Firebase est probablement déjà initialisé, on récupère le client
            if "already exists" in str(e):
                db = firestore.client(app=initialize_app(name=app_id))
            else:
                st.error(f"Erreur d'initialisation Firebase: {e}")
                db = None
    else:
        st.error("Configuration Firebase introuvable dans les variables d'environnement.")
        db = None
        
except ImportError:
    st.error("Les librairies `bcrypt` ou `firebase-admin` sont manquantes. Veuillez les ajouter à `requirements.txt`.")
    db = None

# --- CONSTANTES GLOBALES (Collections et Enums) ---
COL_USERS = 'smmd_users'
COL_HOUSES = 'smmd_houses'
COL_TRANSACTIONS = 'smmd_transactions'
COL_ALLOCATIONS = 'smmd_allocations'

DEFAULT_PASSWORD = "first123"
TITLES = ["M.", "Mme", "Autre"]
ROLES = ["utilisateur", "chef_de_maison", "admin"]
PAYMENT_METHODS = ["Avance personnelle", "Compte de la maison", "Autre"]
HOUSE_PAYMENT_METHODS = ["Compte de la maison"]

# --- UTILS CRYPTO & AUTH ---

def hash_password(password):
    """Génère le hash Bcrypt du mot de passe."""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def authenticate_user(username, password):
    """
    Vérifie les identifiants de l'utilisateur.
    
    CONTINGENCE SPÉCIALE (TEMPORAIRE):
    Permet la connexion immédiate de l'utilisateur 'admin_admin' avec 
    le mot de passe 'admin1234+++', pour la première initialisation.
    
    CETTE LOGIQUE DOIT ÊTRE SUPPRIMÉE UNE FOIS LE COMPTE ADMIN VÉRITABLE CRÉÉ.
    """
    if db is None:
        st.error("Base de données non connectée.")
        return False
        
    # 🚨 1. BOOTSTRAP ADMIN CHECK (NOUVEAUX IDENTIFIANTS) 🚨
    if username == 'admin_admin' and password == 'admin1234+++':
        st.session_state['logged_in'] = True
        st.session_state['user_data'] = {'first_name': 'Super', 'last_name': 'Admin', 'role': 'admin', 'username': 'admin_admin'}
        st.session_state['user_id'] = 'admin_admin' 
        st.session_state['role'] = 'admin'
        st.session_state['house_id'] = 'bootstrap_house_id' # ID factice
        st.toast("Connexion Admin de Secours Réussie ! Créez immédiatement un vrai compte Admin.", icon='🔑')
        return True
    
    try:
        # 2. Tentative de récupération et vérification standard (bcrypt)
        q = db.collection(COL_USERS).where('username', '==', username).limit(1).stream()
        user_doc = next(q, None)
        
        if not user_doc:
            return False
            
        user_data = user_doc.to_dict()
        stored_hash = user_data.get('password_hash', '').encode('utf-8')
        password_bytes = password.encode('utf-8')
        
        # Vérification Bcrypt standard
        if stored_hash and bcrypt.checkpw(password_bytes, stored_hash):
            st.session_state['logged_in'] = True
            st.session_state['user_data'] = user_data
            st.session_state['user_id'] = user_doc.id 
            st.session_state['role'] = user_data.get('role')
            st.session_state['house_id'] = user_data.get('house_id')
            
            # Vérifie si le mot de passe doit être changé (clé ajoutée par l'admin)
            st.session_state['must_change_password'] = user_data.get('must_change_password', False)
            
            return True
            
        # 3. Échec de l'authentification
        return False
        
    except Exception as e: 
        print(f"Auth Error: {e}")
        return False


# --- UTILS FIREBASE (Mise en cache pour la performance) ---

@st.cache_data(ttl=3600) # Cache 1h
def get_all_houses():
    """Récupère toutes les maisons."""
    if db is None: return {}
    return {doc.id: doc.to_dict() for doc in db.collection(COL_HOUSES).stream()}

@st.cache_data(ttl=3600) # Cache 1h
def get_house_name(house_id):
    """Récupère le nom d'une maison par son ID."""
    return get_all_houses().get(house_id, {}).get('name', 'Maison Inconnue')

@st.cache_data(ttl=3600) # Cache 1h
def get_all_users():
    """Récupère tous les utilisateurs."""
    if db is None: return {}
    return {doc.id: doc.to_dict() for doc in db.collection(COL_USERS).stream()}

@st.cache_data(ttl=5) # Cache court (5 secondes) pour les données dynamiques
def get_house_transactions(house_id):
    """Récupère toutes les transactions pour une maison."""
    if db is None: return pd.DataFrame()
    
    docs = db.collection(COL_TRANSACTIONS).where('house_id', '==', house_id).stream()
    data = [doc.to_dict() | {'doc_id': doc.id} for doc in docs]
    
    if not data:
        return pd.DataFrame()
        
    df = pd.DataFrame(data)
    # Assurer les types de colonnes
    df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
    df['created_at'] = pd.to_datetime(df['created_at'], errors='coerce')
    return df

# --- UTILS CORE LOGIC ---

def calculate_balances(df, user_id):
    """Calcule le solde de la maison et le solde personnel de l'utilisateur."""
    if df.empty: return 0, 0
    
    # Solde Maison
    # Les recettes sont positives, les dépenses sont négatives
    df_recettes = df[df['type'].str.startswith('recette')]
    df_dep_maison = df[df['type'] == 'depense_maison']
    
    total_recettes = df_recettes['amount'].sum()
    total_dep_maison = df_dep_maison['amount'].sum()
    
    house_balance = total_recettes - total_dep_maison
    
    # Solde Personnel (Avances dues par ou à l'utilisateur)
    
    # 1. Total des avances faites par cet utilisateur (doit être remboursé par la maison)
    total_avance_faites = df[(df['user_id'] == user_id) & (df['type'] == 'depense_avance') & (df['status'] != 'remboursé')]['amount'].sum()
    
    # 2. Total des avances reçues par cet utilisateur (doit rembourser la maison ou un autre)
    # (Logique non implémentée, ici on se concentre sur les avances faites par l'utilisateur)
    
    personal_balance = total_avance_faites
    
    return round(house_balance, 2), round(personal_balance, 2)

# --- UTILS ADMIN ---

def delete_user(user_id):
    """Supprime un utilisateur et invalide les caches."""
    if db is None: return
    try:
        db.collection(COL_USERS).document(user_id).delete()
        st.toast("Utilisateur supprimé !", icon='🗑️')
        get_all_users.clear()
        st.rerun()
    except Exception as e: st.error(str(e))

def delete_house(house_id):
    """Supprime une maison et invalide les caches."""
    if db is None: return
    try:
        db.collection(COL_HOUSES).document(house_id).delete()
        st.toast("Maison supprimée !", icon='🗑️')
        get_all_houses.clear()
        st.rerun()
    except Exception as e: st.error(str(e))


# --- FONCTIONS CRUD DE TRANSACTION (Intégrées ici) ---

def save_transaction(house_id, user_id, type, amount, nature, payment_method=None, notes=None):
    """
    Enregistre une nouvelle transaction dans Firestore et retourne l'ID du document.
    """
    if db is None: return None
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

def update_transaction(doc_id, data):
    """Met à jour les champs d'une transaction dans Firestore."""
    if db is None: return False
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
    if db is None: return
    try:
        db.collection(COL_TRANSACTIONS).document(doc_id).delete()
        st.toast("Supprimé !", icon='🗑️')
        get_house_transactions.clear() # Vider le cache de toutes les maisons
        st.rerun()
    except Exception as e: st.error(str(e))

def set_monthly_allocation(user_id, house_id, amount):
    """
    Met à jour l'allocation mensuelle. 
    """
    if db is None: return
    amount = round(float(amount), 2)
    
    # 1. Met à jour l'allocation de référence (COL_ALLOCATIONS). 
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
    get_house_transactions.clear() 
    st.rerun()


# --- INTERFACES UTILISATEUR (Intégrées ici) ---

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

def user_dashboard():
    """Affiche le tableau de bord de l'utilisateur standard."""
    # S'assurer que house_id n'est pas l'ID factice de bootstrap
    hid = st.session_state['house_id'] if st.session_state['house_id'] != 'bootstrap_house_id' else None
    
    if not hid:
        st.warning("Vous devez être affecté à une maison pour accéder au tableau de bord. Veuillez contacter l'administrateur.")
        return

    role = st.session_state['role']
    # Utiliser les caches pour récupérer les données
    df = get_house_transactions(hid)
    h_bal, p_bal = calculate_balances(df, st.session_state['user_id']) if not df.empty else (0,0)
    
    st.title(f"🏠 {get_house_name(hid)}")
    c1, c2 = st.columns(2)
    c1.metric("Solde Maison", f"{h_bal} €")
    c2.metric("Vos Avances", f"{p_bal} €")
    
    tabs = ["Recettes", "Dépenses"]
    if role == 'chef_de_maison': tabs.append("Chef")
    
    t_list = st.tabs(tabs)
    
    with t_list[0]: # Recettes
        st.subheader("Enregistrer une Recette")
        
        # Récupère l'allocation actuelle (pour l'affichage par défaut)
        current_alloc_doc = db.collection(COL_ALLOCATIONS).document(st.session_state['user_id']).get()
        current_alloc_amount = current_alloc_doc.to_dict().get('amount', 0.0) if current_alloc_doc.exists else 0.0
        
        with st.form("alloc"):
            st.markdown(f"**Allocation Mensuelle (Actuel: {current_alloc_amount} €)**")
            v = st.number_input("Nouveau Montant de l'allocation", min_value=0.0, value=current_alloc_amount, key="alloc_v")
            st.info("Ce nouveau montant sera appliqué au mois en cours et à tous les mois suivants.")
            if st.form_submit_button("Valider Allocation", key="alloc_btn"): 
                set_monthly_allocation(st.session_state['user_id'], hid, v)
        
        st.markdown("---")
        with st.form("rec"):
            st.markdown("**Recette Exceptionnelle**")
            v = st.number_input("Montant", min_value=0.0, key="rec_v")
            n = st.text_input("Nature (ex: Remboursement prêt)", key="rec_n")
            if st.form_submit_button("Ajouter Recette", key="rec_btn"): 
                save_transaction(hid, st.session_state['user_id'], 'recette_exceptionnelle', v, n)
                st.rerun()

    with t_list[1]: # Dépenses
        st.subheader("Enregistrer une Dépense")
        
        # Récupère l'ID de la dernière dépense stockée (si elle existe)
        last_depense_id = st.session_state.get('last_depense_id')
        
        # --- Formulaire de Dépense ---
        with st.form("dep"):
            v = st.number_input("Montant", min_value=0.0, key="dep_v")
            n = st.text_input("Nature (ex: Courses Leclerc)", key="dep_n")
            m = st.radio("Moyen de Paiement", PAYMENT_METHODS, key="dep_m")
            if st.form_submit_button("Ajouter Dépense", key="dep_btn"):
                typ = 'depense_maison' if m in HOUSE_PAYMENT_METHODS else 'depense_avance'
                new_id = save_transaction(hid, st.session_state['user_id'], typ, v, n, m)
                
                # Stocker l'ID uniquement si c'est une dépense pour la suppression/modification immédiate
                if new_id and typ.startswith('depense'):
                    st.session_state['last_depense_id'] = new_id 
                elif 'last_depense_id' in st.session_state:
                    del st.session_state['last_depense_id']
                    
                st.rerun()

        # --- Zone de confirmation/modification/suppression immédiate ---
        if last_depense_id:
            try:
                # Tente de récupérer les détails pour l'affichage de confirmation
                last_tx_doc = db.collection(COL_TRANSACTIONS).document(last_depense_id).get()
                
                # Double vérification : doc existe ET est bien une dépense de CET utilisateur
                if last_tx_doc.exists and last_tx_doc.to_dict().get('user_id') == st.session_state['user_id'] and last_tx_doc.to_dict().get('type', '').startswith('depense'):
                    tx_data = last_tx_doc.to_dict()
                    st.markdown("---")
                    st.info(f"Dernière dépense enregistrée: **{tx_data['nature']}** ({tx_data['amount']} €) - {tx_data['payment_method']}.")
                    
                    st.markdown("##### Que souhaitez-vous faire ?")
                    
                    # 1. Modification
                    with st.expander("✏️ Modifier la Dépense"):
                        with st.form(f"edit_tx_user_{last_depense_id}"):
                            # Assurez-vous que le montant est un float pour l'affichage
                            new_amount = st.number_input("Montant (EUR)", value=float(tx_data['amount']), key="edit_amount_u")
                            new_nature = st.text_input("Nature", value=tx_data['nature'], key="edit_nature_u")
                            
                            default_method_index = PAYMENT_METHODS.index(tx_data['payment_method']) if tx_data['payment_method'] in PAYMENT_METHODS else 0
                            new_method = st.radio("Moyen de Paiement", PAYMENT_METHODS, index=default_method_index, key="edit_method_u")
                            
                            if st.form_submit_button("Sauvegarder les Modifications", type="primary"):
                                new_type = 'depense_maison' if new_method in HOUSE_PAYMENT_METHODS else 'depense_avance'
                                update_data = {
                                    'amount': round(float(new_amount), 2),
                                    'nature': new_nature,
                                    'type': new_type,
                                    'payment_method': new_method,
                                    'updated_at': datetime.now().isoformat()
                                }
                                update_transaction(last_depense_id, update_data)
                                # Le 'rerun' est géré dans update_transaction, mais on garde l'ID de session pour réafficher l'expander si besoin.
                                st.rerun() 

                    # 2. Suppression (Annulation)
                    c1_del, c2_del = st.columns(2)
                    with c1_del:
                        st.warning("Annulation (Suppression définitive)")
                        if st.button("🗑️ Annuler cette Dépense", key="delete_last_tx_btn_u"):
                            delete_transaction(last_depense_id)
                            # delete_transaction appelle st.rerun()
                    
                    # 3. Confirmation/Validation (Retirer de la session state pour cacher l'interface)
                    with c2_del:
                        st.success("Confirmation (Elle est correcte)")
                        if st.button("✅ Confirmer et Continuer", key="confirm_last_tx_btn_u"):
                            del st.session_state['last_depense_id']
                            st.toast("Dépense validée. Vous pouvez en enregistrer une nouvelle.", icon='👍')
                            st.rerun()

                else:
                    # Si le doc n'existe plus, n'appartient pas à cet utilisateur ou n'est pas une dépense, on nettoie
                    if 'last_depense_id' in st.session_state:
                         del st.session_state['last_depense_id']
                         st.rerun()
            except Exception as e:
                # Gérer les erreurs de récupération (Firestore)
                print(f"Error checking last transaction: {e}")
                if 'last_depense_id' in st.session_state:
                    del st.session_state['last_depense_id']
                    st.rerun()


    if role == 'chef_de_maison' and len(t_list) > 2:
        with t_list[2]: # Chef (Validation des Avances)
            st.subheader("Historique des Transactions")
            if not df.empty:
                st.dataframe(df)
                pending = df[(df['type'] == 'depense_avance') & (df['status'] == 'en_attente_remboursement')]
                if not pending.empty:
                    st.warning(f"{len(pending)} avance(s) en attente de remboursement")
                    uids = pending['user_id'].unique()
                    
                    st.markdown("---")
                    st.subheader("Valider les Remboursements")
                    u = st.selectbox("Membre à rembourser", uids)
                    
                    if st.button(f"Confirmer le Remboursement des avances de {u}"):
                        # Marque toutes les avances d'un utilisateur comme remboursées
                        for d in db.collection(COL_TRANSACTIONS).where('user_id','==',u).where('status','==','en_attente_remboursement').stream():
                            db.collection(COL_TRANSACTIONS).document(d.id).update({'status': 'remboursé'})
                        st.success("Remboursements validés. Actualisation...")
                        get_house_transactions.clear()
                        st.rerun()
                else:
                    st.info("Aucune avance en attente de remboursement.")
                    
def admin_interface():
    """Affiche l'interface complète de l'administrateur, incluant Audit (T3)."""
    st.header("👑 Admin")
    t1, t2, t3 = st.tabs(["Utilisateurs", "Maisons", "Audit"])
    
    # ---------------------------
    # T1: Utilisateurs (Création & Suppression)
    # ---------------------------
    with t1:
        st.subheader("Créer un nouvel utilisateur")
        st.info(f"Le mot de passe par défaut est défini sur : **`{DEFAULT_PASSWORD}`**. L'utilisateur sera forcé de le changer à la première connexion.")
        with st.form("new_user"):
            c1, c2, c3 = st.columns(3)
            ti = c1.selectbox("Titre", TITLES)
            fn = c2.text_input("Prénom")
            ln = c3.text_input("Nom")
            houses = get_all_houses()
            h_opts = {v['name']: k for k, v in houses.items()}
            role = st.selectbox("Rôle", ROLES)
            house = st.selectbox("Maison", list(h_opts.keys()) if h_opts else ["-"])
            
            if st.form_submit_button("Créer l'utilisateur"):
                uname = f"{fn.lower()}_{ln.lower()}"
                
                # Hacher le mot de passe par défaut
                default_pw_hash = hash_password(DEFAULT_PASSWORD)
                
                # Enregistrement avec le nouveau statut
                db.collection(COL_USERS).document(uname).set({
                    'title': ti, 'first_name': fn, 'last_name': ln, 'username': uname,
                    'password_hash': default_pw_hash, 
                    'role': role, 
                    'house_id': h_opts.get(house),
                    'must_change_password': True # 🚨 CLÉ POUR LE CHANGEMENT DE MOT DE PASSE FORCÉ
                })
                get_all_users.clear()
                st.success(f"Créé: {uname}. Mot de passe par défaut: {DEFAULT_PASSWORD}")
                st.rerun() 


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
        if db is None:
            st.error("Connexion DB requise.")
            return

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
        
        ALL_TRANSACTION_TYPES = ['recette_mensuelle', 'recette_exceptionnelle', 'depense_maison', 'depense_avance']
        ALL_STATUS = ['validé', 'en_attente_remboursement', 'remboursé']
        
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
            delete_transaction(selected_doc_id)
            # delete_transaction appelle st.rerun()


# --- INTERFACE DE CONNEXION ---

def login_form():
    """Affiche le formulaire de connexion."""
    st.title("Connexion SMMD")
    st.markdown("Entrez votre identifiant et mot de passe.")
    
    with st.form("login_form"):
        username = st.text_input("Identifiant (ex: prenom_nom)")
        password = st.text_input("Mot de passe", type="password")
        
        if st.form_submit_button("Se connecter", type="primary"):
            if authenticate_user(username, password):
                st.rerun()
            else:
                st.error("Identifiant ou mot de passe incorrect.")


# --- FONCTION PRINCIPALE ---

def main():
    """Gère l'état de la session et affiche l'interface appropriée."""
    st.set_page_config(page_title="SMMD - Gestion Financière", layout="wide")

    # Initialisation de l'état de session si non défini
    if 'logged_in' not in st.session_state:
        st.session_state['logged_in'] = False
        st.session_state['role'] = None
        st.session_state['user_id'] = None
        st.session_state['user_data'] = {}
        st.session_state['must_change_password'] = False
        st.session_state['house_id'] = None
    
    # Barre latérale (toujours visible)
    with st.sidebar:
        st.title("SMMD App")
        if st.session_state['logged_in']:
            st.success(f"Connecté: {st.session_state['user_data'].get('first_name')} ({st.session_state['role']})")
            
            # Bouton de déconnexion
            if st.button("Déconnexion"):
                # Nettoyage de l'état de session
                st.session_state.clear()
                st.experimental_rerun() # Force la réinitialisation de l'état
        else:
            st.info("Veuillez vous connecter.")
            
    # Redirection vers l'interface appropriée
    if st.session_state['logged_in']:
        
        # 1. Demande de changement de mot de passe (priorité maximale)
        if st.session_state.get('must_change_password', False):
             password_reset_interface(st.session_state['user_id'])

        # 2. Interface Admin
        elif st.session_state['role'] == 'admin':
            admin_interface()

        # 3. Interface Utilisateur Standard (inclut Chef de Maison)
        elif st.session_state['role'] in ['utilisateur', 'chef_de_maison']:
            user_dashboard()
            
        else:
            # Rôle inconnu ou non géré (sécurité)
            st.error("Rôle utilisateur inconnu. Veuillez vous déconnecter et réessayer.")
            
    # Affichage du formulaire de connexion si déconnecté
    else:
        login_form()

if __name__ == "__main__":
    main()