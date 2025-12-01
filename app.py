import streamlit as st
import os
import json
import firebase_admin
from firebase_admin import credentials, firestore, auth
import pandas as pd
from datetime import datetime
import bcrypt # Nécessaire pour le hachage des mots de passe

# --- Configuration et Initialisation ---

# Variables d'environnement pour Canvas/Render

APP_ID = os.environ.get('__app_id', 'default-app-id')
FIREBASE_CONFIG_VAR_NAME = '__firebase_config'

# --- Fonctions Firebase ---

@st.cache_resource
def initialize_firebase():
    """Initialise Firebase Admin SDK avec les variables d'environnement."""

    # 1. Tente de lire la configuration JSON depuis l'environnement
    firebase_config_str = os.environ.get(FIREBASE_CONFIG_VAR_NAME)

    if not firebase_config_str:
        st.error(f"Erreur: Config Firebase introuvable. Veuillez définir la variable d'environnement '{FIREBASE_CONFIG_VAR_NAME}' sur Render/Streamlit Cloud.")
        return None
        
    try:
        firebase_config = json.loads(firebase_config_str)
    except json.JSONDecodeError:
        st.error("Erreur: La variable d'environnement Firebase n'est pas un JSON valide.")
        return None

    # 2. Initialisation de Firebase
    try:
        # Si Firebase n'est pas déjà initialisé, l'initialiser
        if not firebase_admin._apps:
            # Crée les identifiants à partir du JSON du compte de service
            cred = credentials.Certificate(firebase_config)
            firebase_admin.initialize_app(cred, name=APP_ID)
            
        # 3. Récupère les instances de services
        db_instance = firestore.client(app=firebase_admin.get_app(APP_ID))
        
        return db_instance

    except Exception as e:
        st.error(f"Erreur d'initialisation Firebase : {e}")
        return None

# --- Fonctions de base de données (Chemins) ---

def get_db():
    """Retourne l'instance de la base de données Firestore."""
    return initialize_firebase()

def get_collection_ref(db, collection_name):
    """Retourne la référence à une collection publique standard."""
    # Chemin public standard pour la collaboration dans Canvas
    return db.collection('artifacts').document(APP_ID).collection('public').document('data').collection(collection_name)

def get_settings_doc_ref(db, doc_name):
    """Retourne la référence à un document dans la collection de paramètres (smmd_settings)."""
    return get_collection_ref(db, 'smmd_settings').document(doc_name)

# --- Fonctions d'Authentification ---

@st.cache_data(ttl=60) # Cache les utilisateurs pour éviter les lectures excessives
def get_all_users():
    """Récupère tous les utilisateurs pour la connexion."""
    db = get_db()
    if not db:
        return {}
    users_ref = get_collection_ref(db, 'smmd_users')
    users = {}
    try:
        docs = users_ref.stream()
        for doc in docs:
            # --- CORRECTION DE L'ERREUR 'tuple' object has no attribute 'id' ---
            if not hasattr(doc, 'id'):
                 # Ignorer les objets inattendus pour éviter l'erreur
                 st.warning(f"Objet inattendu trouvé dans le stream d'utilisateurs. Type: {type(doc)}")
                 continue
            # -----------------------------------------------------------------

            user_data = doc.to_dict()
            # Stocke l'ID du document pour les futures mises à jour
            user_data['doc_id'] = doc.id 
            
            # Utilise l'email comme clé de recherche rapide
            if 'email' in user_data:
                users[user_data['email']] = user_data
    except Exception as e:
        st.error(f"Erreur de lecture des utilisateurs : {e}")
        return {}
    return users

def hash_password(password):
    """Hashe un mot de passe en utilisant bcrypt."""
    password_bytes = password.encode('utf-8')
    # Utilise un salt généré aléatoirement
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode('utf-8')

def validate_login(email, password):
    """
    Vérifie l'email et le mot de passe.

    🚨🚨 MODIFICATION TEMPORAIRE DE DÉPANNAGE 🚨🚨
    Permet la connexion avec mot de passe en clair pour le tout premier
    administrateur si le hachage a échoué.
    """
    users = get_all_users()
    user_data = users.get(email)

    if not user_data:
        st.error("Email ou mot de passe incorrect.")
        return None

    hashed_password_db = user_data.get('password', '').encode('utf-8')

    # 1. Tentative de vérification Bcrypt (Méthode sécurisée standard)
    try:
        # Vérifie si le mot de passe entré correspond au hash stocké
        if bcrypt.checkpw(password.encode('utf-8'), hashed_password_db):
            st.session_state['logged_in'] = True
            st.session_state['user'] = user_data
            return user_data
    except ValueError:
        # Ceci se produit si le hash stocké n'est pas un hash Bcrypt valide
        pass 

    # 2. 🚨 LOGIQUE DE DÉPANNAGE TEMPORAIRE 🚨
    # Si le Bcrypt échoue ET que le mot de passe stocké EST EN CLAIR :
    if password == user_data.get('password'):
        # On ne permet ceci que pour les admins (pour limiter l'abus)
        if user_data.get('role') == 'admin':
            st.warning("Connexion effectuée avec mot de passe en clair (mode dépannage). VEUILLEZ CHANGER CE MOT DE PASSE IMMÉDIATEMENT.")
            st.session_state['logged_in'] = True
            st.session_state['user'] = user_data
            return user_data
        else:
            st.error("Mot de passe incorrect (mode Bcrypt non valide).")
            return None

    st.error("Email ou mot de passe incorrect.")
    return None

def logout():
    """Déconnecte l'utilisateur."""
    st.session_state['logged_in'] = False
    st.session_state['user'] = {}
    st.cache_data.clear() # Efface le cache des utilisateurs/données

# --- Fonctions de Gestion des Catégories ---

@st.cache_data(ttl=60)
def get_expense_categories(db):
    """Récupère la liste des catégories de dépenses."""
    categories_ref = get_settings_doc_ref(db, 'expense_categories')
    try:
        doc = categories_ref.get()
        if doc.exists:
            return doc.to_dict().get('categories', [])
        return []
    except Exception as e:
        st.error(f"Erreur de lecture des catégories : {e}")
        return []

def save_expense_categories(db, categories_list):
    """Enregistre la liste mise à jour des catégories de dépenses."""
    categories_ref = get_settings_doc_ref(db, 'expense_categories')
    try:
        # Stocke les catégories dans un tableau
        categories_ref.set({'categories': categories_list}, merge=True)
        st.cache_data.clear() # Invalide le cache des catégories
        return True
    except Exception as e:
        st.error(f"Erreur lors de l'enregistrement des catégories : {e}")
        return False

# --- Fonctions de Gestion des Transactions ---

def save_transaction(db, transaction_data):
    """Enregistre une nouvelle transaction dans la collection smmd_transactions."""
    transactions_ref = get_collection_ref(db, 'smmd_transactions')
    try:
        transactions_ref.add(transaction_data)
        st.cache_data.clear() # Invalide le cache des transactions
        return True
    except Exception as e:
        st.error(f"Erreur lors de l'enregistrement de l'opération: {e}")
        return False

@st.cache_data(ttl=60)
def get_transactions(db):
    """Récupère toutes les transactions."""
    transactions_ref = get_collection_ref(db, 'smmd_transactions')
    transactions = []
    try:
        docs = transactions_ref.stream()
        for doc in docs:
            # --- CORRECTION DE L'ERREUR 'tuple' object has no attribute 'id' ---
            if not hasattr(doc, 'id'):
                 # Ignorer les objets inattendus pour éviter l'erreur
                 st.warning(f"Objet inattendu trouvé dans le stream de transactions. Type: {type(doc)}")
                 continue
            # -----------------------------------------------------------------

            data = doc.to_dict()
            data['doc_id'] = doc.id # Stocke l'ID du document pour référence future

            # Assure que le montant est un nombre
            try:
                data['amount'] = float(data.get('amount', 0))
            except ValueError:
                data['amount'] = 0.0
                
            transactions.append(data)
        
        # Convertit en DataFrame pour un traitement facile
        df = pd.DataFrame(transactions)
        
        # Assure les colonnes nécessaires (si aucune transaction n'existe)
        if df.empty:
             df = pd.DataFrame(columns=['type', 'expense_category', 'description', 'amount', 'date', 'recorded_by', 'doc_id'])
        
        # Convertit la colonne 'date' en datetime, en gérant les timestamps Firestore
        if 'date' in df.columns:
            # Gère les dates stockées sous forme de timestamps Firestore
            df['date'] = df['date'].apply(
                lambda x: x.strftime('%Y-%m-%d') if isinstance(x, datetime) else pd.to_datetime(x).strftime('%Y-%m-%d')
            )

        return df

    except Exception as e:
        st.error(f"Erreur de lecture des opérations : {e}")
        return pd.DataFrame()

# --- Pages et Sections de l'Application ---

def login_page():
    """Page de connexion et de déconnexion."""
    st.title("Connexion SMMD Alsace")

    with st.container(border=True):
        email = st.text_input("Email")
        password = st.text_input("Mot de passe", type="password")
        
        if st.button("Se connecter", type="primary"):
            user = validate_login(email, password)
            if user:
                st.success(f"Bienvenue, {user.get('name')}!")
                st.rerun() # Rafraîchit pour afficher la page principale
        
        if st.session_state.get('logged_in'):
            st.button("Déconnexion", on_click=logout)

def setup_allocation_page(db, user_data):
    """Page pour définir l'allocation mensuelle initiale."""
    st.title("Configuration Initiale : Votre Allocation Mensuelle")
    st.markdown(f"Bienvenue, **{user_data.get('name')}**. Pour commencer, veuillez définir votre allocation budgétaire mensuelle.")

    with st.form("allocation_form", clear_on_submit=False):
        allocation = st.number_input(
            "Montant de l'allocation mensuelle (€)",
            min_value=0.01,
            value=1500.00,
            step=50.00,
            format="%.2f"
        )
        submitted = st.form_submit_button("Enregistrer l'Allocation", type="primary")

        if submitted:
            # Récupérer l'ID du document utilisateur stocké dans l'état de session
            user_doc_id = user_data.get('doc_id')
            
            if user_doc_id:
                try:
                    users_ref = get_collection_ref(db, 'smmd_users')
                    user_doc_ref = users_ref.document(user_doc_id)
                    
                    user_doc_ref.update({
                        'monthly_allocation': allocation,
                    })
                    
                    # Mettre à jour l'état de session pour refléter le changement
                    st.session_state['user']['monthly_allocation'] = allocation
                    
                    st.success(f"Allocation de {allocation:.2f} € enregistrée avec succès!")
                    st.balloons()
                    st.rerun()
                except Exception as e:
                    st.error(f"Erreur lors de l'enregistrement de l'allocation: {e}")
            else:
                st.error("Erreur critique: ID de document utilisateur manquant.")

def transaction_form_section(db, transaction_type):
    """Section de formulaire pour Recettes, Dépenses ou Avances."""
    
    # Détermine le libellé pour le type de transaction
    type_labels = {
        'revenue': 'Recette', 
        'expense': 'Dépense', 
        'advance': 'Avance'
    }
    label = type_labels.get(transaction_type, 'Opération')
    
    col_amount, col_date = st.columns([1, 1])
    
    with st.form(f"form_{transaction_type}", clear_on_submit=True):
        
        # Champ Montant
        with col_amount:
            # Le montant est négatif pour les dépenses et avances, positif pour les recettes
            amount = st.number_input(f"Montant de la {label} (€)", min_value=0.01, format="%.2f")

        # Champ Date
        with col_date:
            date_trans = st.date_input("Date de l'opération", datetime.now().date())
        
        
        # --- Gestion spécifique pour les Dépenses (Catégories) ---
        category = None
        if transaction_type == 'expense':
            expense_categories = get_expense_categories(db)
            if expense_categories:
                # Ajoute une option "--- Choisir une catégorie ---" en première position
                categories_list = sorted(expense_categories)
                categories_list.insert(0, "--- Choisir une catégorie ---")
                
                category = st.selectbox("Catégorie de Dépense", categories_list)
                
                # La description devient optionnelle si une catégorie est sélectionnée
                description = st.text_area("Description / Motif (Optionnel)", max_chars=250)
                
                # Validation pour s'assurer qu'au moins la catégorie est choisie OU qu'une description est fournie
                is_valid_expense = (category != "--- Choisir une catégorie ---") or description
                if not is_valid_expense:
                    st.warning("Veuillez sélectionner une catégorie ou fournir une description détaillée.")


                # Construction de la description finale pour la base de données
                if category != "--- Choisir une catégorie ---":
                    transaction_description = f"[{category}]{f' - {description}' if description else ''}"
                else:
                    transaction_description = description
                    
            else:
                st.warning("Aucune catégorie de dépense définie. Veuillez en ajouter dans l'onglet 'Paramètres & Gestion'.")
                description = st.text_area("Description / Motif", max_chars=250)
                transaction_description = description
                category = "Non catégorisé"
        
        else: # Pour Recette et Avance
            description = st.text_area("Description / Motif", max_chars=250)
            transaction_description = description

        # Bouton de soumission
        submitted = st.form_submit_button(f"Enregistrer la {label}", type="primary")
        
        if submitted:
            # Validation plus stricte à la soumission pour Recette/Avance
            if transaction_type != 'expense' and not transaction_description:
                st.warning("Veuillez fournir une description.")
                return
            
            # Validation de la dépense
            if transaction_type == 'expense' and not is_valid_expense:
                # Afficher l'avertissement déjà défini et arrêter
                return
            
            # Ajustement du signe du montant
            final_amount = amount
            if transaction_type in ['expense', 'advance']:
                final_amount = -amount # Dépenses et avances sont négatives
            
            transaction_data = {
                'type': transaction_type,
                'description': transaction_description,
                'amount': final_amount,
                'date': date_trans.strftime('%Y-%m-%d'), # Stocke en format string ISO 8601
                'recorded_by': st.session_state.user.get('name'),
                'recorded_at': firestore.SERVER_TIMESTAMP,
            }
            
            # Ajoute la catégorie explicitement pour les dépenses (pour les filtres futurs)
            if transaction_type == 'expense' and category and category != "--- Choisir une catégorie ---":
                transaction_data['expense_category'] = category
            else:
                # Si non catégorisé, assure que la clé n'est pas présente ou est vide
                transaction_data['expense_category'] = "" 

            if save_transaction(db, transaction_data):
                st.success(f"{label} de {final_amount:.2f} € enregistrée avec succès!")

def data_export_section(df_transactions):
    """Section pour l'affichage et l'exportation des données."""
    st.header("Historique Détaillé et Exportation")
    
    if df_transactions.empty:
        st.info("Aucune opération enregistrée pour le moment.")
        return

    # Calcul du solde actuel
    balance = df_transactions['amount'].sum()
    st.metric("Solde Total Actuel", f"{balance:.2f} €", delta_color="normal")
    
    st.subheader("Filtres")
    
    # Ajout d'une colonne de filtre pour la catégorie de dépense
    cols_filter = st.columns(4)
    
    with cols_filter[0]:
        # Assure que 'Toutes' est la première option si les types existent
        type_options = ['Toutes'] + list(df_transactions['type'].unique())
        selected_type = st.selectbox("Filtrer par Type", type_options)

    with cols_filter[1]:
        # Filtre Catégorie de Dépense (uniquement si le type est 'expense' ou 'Toutes')
        category_options = ['Toutes']
        if 'expense_category' in df_transactions.columns:
            # Utilise un set pour obtenir les valeurs uniques et retire les chaînes vides, puis trie
            unique_categories = {c for c in df_transactions['expense_category'].dropna().unique() if c}
            category_options += sorted(list(unique_categories))
        
        selected_category = st.selectbox("Filtrer par Catégorie", category_options)
        
    # Assure que les dates min et max existent avant d'essayer de les convertir
    if 'date' in df_transactions.columns and not df_transactions['date'].empty:
        min_date_val = pd.to_datetime(df_transactions['date']).min()
        max_date_val = pd.to_datetime(df_transactions['date']).max()
    else:
        min_date_val = datetime.now().date()
        max_date_val = datetime.now().date()

    with cols_filter[2]:
        min_filter = st.date_input("Date de début", value=min_date_val, min_value=min_date_val)

    with cols_filter[3]:
        max_filter = st.date_input("Date de fin", value=max_date_val, max_value=max_date_val)
        
    df_filtered = df_transactions.copy()

    # Application des filtres
    if selected_type != 'Toutes':
        df_filtered = df_filtered[df_filtered['type'] == selected_type]

    if selected_category != 'Toutes':
        df_filtered = df_filtered[df_filtered['expense_category'] == selected_category]


    # Filtrage par date
    df_filtered['date_dt'] = pd.to_datetime(df_filtered['date'])
    df_filtered = df_filtered[
        (df_filtered['date_dt'] >= pd.to_datetime(min_filter)) & 
        (df_filtered['date_dt'] <= pd.to_datetime(max_filter))
    ]
    df_filtered = df_filtered.drop(columns=['date_dt'])
    
    # Tri par date (du plus récent au plus ancien)
    df_filtered = df_filtered.sort_values(by='date', ascending=False)

    st.subheader(f"Résultats ({len(df_filtered)} opérations)")

    # Mise en forme pour l'affichage
    df_display = df_filtered.rename(columns={
        'type': 'Type',
        'expense_category': 'Catégorie Dép.',
        'description': 'Description',
        'amount': 'Montant (€)',
        'date': 'Date',
        'recorded_by': 'Enregistré par'
        # 'doc_id' est retiré de l'affichage
    })
    
    df_display['Montant (€)'] = df_display['Montant (€)'].apply(lambda x: f"{x:.2f}")

    # Nettoyage des colonnes avant l'affichage
    cols_to_display = ['Date', 'Type', 'Catégorie Dép.', 'Description', 'Montant (€)', 'Enregistré par']
    df_display = df_display.reindex(columns=cols_to_display).fillna('') # Remplacer NaN par chaîne vide

    st.dataframe(df_display, use_container_width=True)
    
    # Bouton d'exportation
    csv = df_filtered.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="Exporter les données filtrées (CSV)",
        data=csv,
        file_name=f'export_smmd_compta_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv',
        mime='text/csv',
        type="secondary"
    )

def manage_categories_section(db):
    """UI pour l'Admin pour ajouter et supprimer des catégories de dépenses."""
    st.header("Gestion des Catégories de Dépenses")
    
    current_categories = get_expense_categories(db)
    
    # --- Affichage des catégories existantes ---
    st.subheader("Catégories Actuelles")
    if current_categories:
        # Afficher la liste comme un tableau simple ou une liste
        st.markdown(f"**Total :** {len(current_categories)} catégories")
        st.write(", ".join(sorted(current_categories)))
    else:
        st.info("Aucune catégorie de dépense n'est encore définie.")

    st.markdown("---")
    
    # --- Ajouter une catégorie ---
    col_add, col_del = st.columns(2)
    
    with col_add:
        st.subheader("Ajouter une catégorie")
        with st.form("add_category_form", clear_on_submit=True):
            new_category = st.text_input("Nom de la catégorie (ex: Loyer, Carburant)").strip()
            add_submitted = st.form_submit_button("Ajouter la catégorie", type="primary")

            if add_submitted and new_category:
                # Normalisation : première lettre en majuscule, reste en minuscule
                new_category_normalized = new_category.capitalize()
                
                if new_category_normalized in current_categories:
                    st.warning(f"La catégorie '{new_category_normalized}' existe déjà.")
                else:
                    updated_categories = current_categories + [new_category_normalized]
                    if save_expense_categories(db, updated_categories):
                        st.success(f"Catégorie '{new_category_normalized}' ajoutée avec succès.")
                        st.rerun()
            elif add_submitted:
                st.warning("Veuillez entrer un nom pour la catégorie.")
    
    # --- Supprimer une catégorie ---
    with col_del:
        st.subheader("Supprimer une catégorie")
        if current_categories:
            with st.form("delete_category_form", clear_on_submit=True):
                category_to_delete = st.selectbox(
                    "Sélectionnez la catégorie à supprimer",
                    sorted(current_categories)
                )
                delete_submitted = st.form_submit_button("Supprimer la sélection", type="secondary")

                if delete_submitted:
                    updated_categories = [c for c in current_categories if c != category_to_delete]
                    if save_expense_categories(db, updated_categories):
                        st.success(f"Catégorie '{category_to_delete}' supprimée avec succès.")
                        st.rerun()
        else:
            st.info("Rien à supprimer.")


def admin_page():
    """Page d'administration (pour les utilisateurs avec le rôle 'admin' ou 'chef')."""
    user_role = st.session_state.user.get('role', 'inconnu')
    st.title(f"Tableau de Bord {user_role.capitalize()} - {st.session_state.user.get('name')}")
    st.markdown(f"**Rôle :** `{user_role}`")

    db = get_db()
    
    # Récupère toutes les transactions une seule fois
    df_transactions = get_transactions(db)

    # Affiche l'allocation et le solde
    col_info, col_logout = st.columns([3, 1])
    
    with col_info:
        allocation = st.session_state.user.get('monthly_allocation', 'Non défini')
        st.info(f"**Allocation mensuelle :** `{allocation} €`")

    with col_logout:
        st.button("Déconnexion", on_click=logout)
        
    st.markdown("---")
        
    # Définition des onglets disponibles en fonction du rôle
    available_tabs = ["📊 Opérations", "📚 Historique et Export", "⚙️ Paramètres & Gestion"]
    
    # Le chef (chef) n'a pas accès à la gestion des paramètres
    if user_role == 'chef':
        tab_op, tab_hist_export = st.tabs(available_tabs[:-1])
    else: # admin
        tab_op, tab_hist_export, tab_settings = st.tabs(available_tabs)

    # Saisie des opérations
    with tab_op:
        st.header("Saisie d'une Nouvelle Opération")
        tab_recette, tab_depense, tab_avance = st.tabs(["💰 Recette", "💸 Dépense", "🤝 Avance"])
        
        with tab_recette:
            transaction_form_section(db, 'revenue')
            
        with tab_depense:
            transaction_form_section(db, 'expense')

        with tab_avance:
            transaction_form_section(db, 'advance')

    # Historique et Export
    with tab_hist_export:
        data_export_section(df_transactions)

    # Gestion des Paramètres (Uniquement pour Admin)
    if user_role == 'admin':
        with tab_settings:
            st.header("Gestion de l'Application")
            
            # Gestion des Catégories de Dépenses
            manage_categories_section(db)
            
            st.markdown("---")
            
            # Gestion des Utilisateurs (WIP)
            st.subheader("Gestion des Utilisateurs")
            st.info("Cette section est en cours de développement. Elle permettra de créer, modifier et assigner des rôles aux utilisateurs.")


# --- Fonction Principale ---

def main():
    """Fonction principale de l'application Streamlit."""

    # Configuration de la page
    st.set_page_config(page_title="SMMD Alsace", layout="wide", initial_sidebar_state="collapsed")

    # Injecte le CSS pour le bandeau rouge (Personnalisation visuelle SMMD)
    st.markdown("""
        <style>
        /* Couleurs et style pour le thème SMMD Alsace */
        .st-emotion-cache-1gsv2z, .st-emotion-cache-15zrmu {
            background-color: #A30000; /* Rouge SMMD pour le header/navbar */
            color: white;
        }
        .st-emotion-cache-1n60912 a {
            color: white !important;
        }
        .st-emotion-cache-1gsv2z {
            padding-top: 15px !important;
            padding-bottom: 15px !important;
        }
        h1 {
            color: #A30000; /* Titres en rouge SMMD */
        }
        /* Style pour les boutons primaires (connexion et enregistrement) */
        .st-emotion-cache-czk5ad { 
            background-color: #A30000;
            border-color: #A30000;
        }
        </style>
        """, unsafe_allow_html=True)

    # Assure que l'état de session existe
    if 'logged_in' not in st.session_state:
        st.session_state['logged_in'] = False
        st.session_state['user'] = {}

    # Tente d'initialiser la DB
    db = get_db()

    if db is None:
        # Si Firebase n'a pas pu être initialisé, affiche un message d'erreur
        st.stop()

    # Logique de routage
    if st.session_state.get('logged_in'):
        user_data = st.session_state.user
        user_role = user_data.get('role')
        
        # 1. Vérification de la configuration initiale (allocation mensuelle)
        is_setup_complete = user_data.get('monthly_allocation') is not None
        
        if not is_setup_complete:
            # Si l'allocation n'est pas définie, afficher la page de configuration
            setup_allocation_page(db, user_data)
        
        # 2. Routage normal si la configuration est complète
        # L'admin_page gère maintenant les rôles 'admin' et 'chef'
        elif user_role in ['admin', 'chef']:
            admin_page()
        else:
            # Pour les rôles inconnus
            st.error("Accès non autorisé pour ce rôle.")
            st.button("Déconnexion", on_click=logout)
    else:
        login_page()


if __name__ == '__main__':
    main()