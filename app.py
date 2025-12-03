import streamlit as st
import os
import json
from firebase_admin import initialize_app, credentials, firestore, exceptions
from datetime import datetime, date, timedelta
import pandas as pd
import bcrypt
from functools import lru_cache 
import io 

# --- CONSTANTES ---
# NOTE: Vous devez avoir ici vos constantes (COL_USERS, ROLES, DEFAULT_PASSWORD, etc.)
COL_USERS = "smmd_users" # Exemple de constante
ROLES = ["admin", "superviseur", "utilisateur"] # Exemple de constante

# --- FONCTION D'INITIALISATION FIREBASE ---

@st.cache_resource
def initialize_firebase():
    """Initialise Firebase en utilisant la variable d'environnement et retourne l'instance de l'application."""
    
    # 1. Gestion de la variable d'environnement manquante
    if 'FIREBASE_SERVICE_ACCOUNT' not in os.environ:
        st.error("ERREUR DE CONFIGURATION: Variable d'environnement 'FIREBASE_SERVICE_ACCOUNT' non définie.")
        st.stop()
        
    # 2. Chargement des credentials (compte de service)
    try:
        cred_json = json.loads(os.environ['FIREBASE_SERVICE_ACCOUNT'])
        cred = credentials.Certificate(cred_json)
    except Exception as e:
        st.error(f"Erreur lors du chargement du JSON de service Firebase: {e}")
        st.stop()
        
    # 3. Initialisation sécurisée
    try:
        # Tente l'initialisation. Si l'application existe déjà, ValueError est levée.
        app_instance = initialize_app(cred)
    except ValueError:
        # L'application par défaut existe déjà (à cause de Streamlit), nous la récupérons.
        # Utilisation de l'import 'app as fb_app' déjà présent dans les imports globaux si possible, ou comme ci-dessous:
        from firebase_admin import app as fb_app 
        app_instance = fb_app.get_app()
        
    # 4. Utilisation de l'instance pour initialiser le client Firestore
    db = firestore.client(app=app_instance)
        
    return app_instance, db # Retourne l'instance de l'application et du client db

# --- APPEL GLOBAL POUR DÉFINIR 'db' ET 'firebase_app' (CORRECTION CLÉ) ---

try:
    # La variable 'db' est maintenant définie au niveau global du script.
    firebase_app, db = initialize_firebase()
except Exception as e:
    st.error(f"Échec de l'initialisation de l'application : {e}")
    st.stop()
    
# NOTE: Le reste de vos fonctions (hash_password, check_password, get_all_houses,
# get_house_name, etc., ainsi que main()) doit suivre ici.
# Elles peuvent maintenant toutes utiliser la variable 'db' sans NameError.

# Exemple de fonction qui peut maintenant utiliser 'db' :
# def get_all_houses():
#     docs = db.collection('smmd_houses').stream()
#     return [{"id": doc.id, **doc.to_dict()} for doc in docs]

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
    'recette_exceptionnelle': 'Recette (Exceptionnelle)',
    'remboursement': 'Remboursement d\'Avance',
}

# -------------------------------------------------------------------
# --- 3. Fonctions Utilitaires Firestore ---
# -------------------------------------------------------------------

@st.cache_data(ttl=3600)
def get_categories():
    """Récupère et cache toutes les catégories depuis Firestore."""
    if not db: return {}
    try:
        docs = db.collection(COL_CATEGORIES).stream()
        # Assurez-vous que l'ID du document est la clé et le 'name' la valeur
        categories = {doc.id: doc.to_dict().get('name', 'N/A') for doc in docs}
        return categories
    except Exception:
        return {} 

@st.cache_data(ttl=3600)
def get_house_name(house_id):
    """Retourne le nom de la maison depuis Firestore."""
    if not db or not house_id: return "Maison Inconnue"
    try:
        doc = db.collection(COL_HOUSES).document(house_id).get()
        return doc.to_dict().get('name', 'Maison Inconnue') if doc.exists else 'Maison Inconnue'
    except Exception:
        return "Maison Inconnue"

@st.cache_data(ttl=3600)
def get_all_users_for_house(house_id):
    """Récupère tous les utilisateurs d'une maison (pour les jointures et la connexion)."""
    if not db or not house_id: return {}
    try:
        docs = db.collection(COL_USERS).where('house_id', '==', house_id).stream()
        users = {doc.id: doc.to_dict() for doc in docs}
        return users
    except Exception:
        return {}
    
def get_user_name_by_id(user_id):
    """Récupère le prénom et nom d'un utilisateur par ID (utilise les données mises en cache si possible)."""
    # Si l'utilisateur actuel est celui demandé
    if user_id == st.session_state.get('user_id'):
        data = st.session_state.get('user_data', {})
        return f"{data.get('first_name', 'Utilisateur')} {data.get('last_name', '')}".strip()
        
    # Sinon, on doit charger les utilisateurs de la maison
    house_id = st.session_state.get('house_id')
    users_data = get_all_users_for_house(house_id)
    user_info = users_data.get(user_id, {})
    return f"{user_info.get('first_name', 'Utilisateur')} {user_info.get('last_name', '')}".strip()

@st.cache_data(ttl=30)
def get_transactions_for_house(house_id):
    """
    RÉEL BDD: Récupère toutes les transactions de la maison depuis Firestore.
    """
    if not db or not house_id: return pd.DataFrame()
    
    try:
        docs = db.collection(COL_TRANSACTIONS).where('house_id', '==', house_id).stream()
        data = []
        for doc in docs:
            tx = doc.to_dict()
            tx['id'] = doc.id
            data.append(tx)

        if not data: return pd.DataFrame()
        
        df = pd.DataFrame(data)
        
        # Conversion des timestamps Firestore en datetime Python
        if 'date' in df.columns:
             # Tente de convertir en datetime, gère les cas où c'est déjà une datetime ou un Timestamp Firestore
             df['date'] = df['date'].apply(
                lambda x: x.astimezone(None) if hasattr(x, 'astimezone') else \
                          datetime.fromtimestamp(x.seconds + x.nanoseconds / 1e9) if hasattr(x, 'seconds') else x if hasattr(x, 'seconds') else x
             )
        
        # Jointures avec les utilisateurs et catégories (en utilisant les fonctions utilitaires)
        categories = get_categories()
        
        df['category_name'] = df['category'].apply(lambda cid: categories.get(cid, 'N/A'))
        df['full_name'] = df['user_id'].apply(get_user_name_by_id)
        
        # Tri
        return df.sort_values('date', ascending=False)
    
    except Exception as e:
        # st.error(f"Erreur lors de la récupération des transactions: {e}")
        return pd.DataFrame()

def get_user_transactions(house_id, user_id):
    """Filtre les transactions de la maison pour un utilisateur donné."""
    df = get_transactions_for_house(house_id)
    return df[df['user_id'] == user_id].copy()

# -------------------------------------------------------------------
# --- 4. Fonctions de Gestion des Transactions (CRUD) ---
# -------------------------------------------------------------------

def delete_transaction(transaction_id, house_id, user_id, user_role):
    """
    Supprime une transaction si l'utilisateur est autorisé. (Firestore Implémentation)
    """
    if not db: return False, "Erreur: Connexion BDD non établie."
    
    try:
        # 1. Vérifier les permissions
        # On utilise une requête directe pour vérifier la transaction si le cache n'est pas fiable/disponible
        doc_ref = db.collection(COL_TRANSACTIONS).document(transaction_id)
        doc = doc_ref.get()
        
        if not doc.exists:
             return False, "Transaction introuvable ou déjà supprimée."
        
        transaction_data = doc.to_dict()

        is_author = transaction_data.get('user_id') == user_id
        # Le chef de maison et l'admin peuvent annuler n'importe quelle transaction de leur maison
        is_house_admin = user_role == 'chef_de_maison' and transaction_data.get('house_id') == house_id
        is_admin = user_role == 'admin'

        if is_author or is_house_admin or is_admin:
            # 2. Suppression Firestore
            doc_ref.delete() 
            
            # Invalider le cache
            get_transactions_for_house.clear() 
            return True, f"Transaction #{transaction_id[:6]}... annulée avec succès."
        else:
            return False, "Vous n'avez pas la permission d'annuler cette transaction."

    except Exception as e:
        return False, f"Erreur lors de l'annulation de la transaction : {e}"

def validate_advance(transaction_id, house_id, validator_user_id):
    """Valide une dépense de type 'avance' (Firestore Implémentation)."""
    if not db: return False, "Erreur: Connexion BDD non établie."

    try:
        doc_ref = db.collection(COL_TRANSACTIONS).document(transaction_id)
        doc = doc_ref.get()
        
        if not doc.exists:
             return False, "Avance introuvable."
        
        transaction_data = doc.to_dict()

        if transaction_data.get('house_id') != house_id:
            return False, "Cette avance n'appartient pas à votre maison."
        if transaction_data.get('type') != 'depense_avance':
            return False, "Ce n'est pas un type de transaction 'avance'."
        if transaction_data.get('statut_avance') == 'validée':
            return False, "Cette avance est déjà validée."

        # Mise à jour du statut dans Firestore
        doc_ref.update({
            'statut_avance': 'validée', 
            'validator_id': validator_user_id,
            'validated_at': datetime.now()
        })
        
        # Invalider le cache
        get_transactions_for_house.clear() 
        return True, f"Avance de {transaction_data.get('amount', 0)} € validée avec succès."

    except Exception as e:
        return False, f"Erreur lors de la validation de l'avance : {e}"

# -------------------------------------------------------------------
# --- 5. Export de Données (Excel) ---
# -------------------------------------------------------------------

def generate_excel_report(df_all: pd.DataFrame, house_name: str) -> bytes:
    """
    Génère un rapport Excel structuré et lisible à partir du DataFrame de transactions.
    """
    
    if df_all.empty:
        # Créer un DataFrame vide avec la structure désirée si aucune donnée n'est trouvée
        report_df = pd.DataFrame(columns=['ID_Transaction', 'Date_Transaction', 'Type_Transaction', 
                                          'Montant_EUR', 'Effectué_Par', 'Description', 
                                          'Catégorie', 'Moyen_Paiement', 'Statut_Avance', 
                                          'ID_Utilisateur', 'ID_Maison', 'Date_Saisie', 
                                          'ID_Validateur', 'Date_Validation'])
    else:
        # Préparation du DataFrame pour l'export
        report_df = df_all.copy()
        
        # Renommage des colonnes pour la clarté en français
        report_df = report_df.rename(columns={
            'id': 'ID_Transaction',
            'date': 'Date_Transaction',
            'type': 'Type_Transaction_Code', # On garde le code pour l'analyse
            'amount': 'Montant_EUR',
            'full_name': 'Effectué_Par',
            'description': 'Description',
            'category_name': 'Catégorie',
            'payment_method': 'Moyen_Paiement',
            'statut_avance': 'Statut_Avance_Code', # On garde le code pour l'analyse
            'user_id': 'ID_Utilisateur',
            'house_id': 'ID_Maison',
            'created_at': 'Date_Saisie',
            'validator_id': 'ID_Validateur',
            'validated_at': 'Date_Validation',
        })
        
        # Création de colonnes lisibles
        report_df.insert(3, 'Type_Transaction', report_df['Type_Transaction_Code'].apply(lambda t: TX_TYPE_MAP.get(t, 'Autre')))
        report_df.insert(10, 'Statut_Avance', report_df['Statut_Avance_Code'].apply(lambda s: AVANCE_STATUS.get(s, 'N/A')))

        # Sélection et ordre des colonnes
        cols_final = [
            'ID_Transaction', 'Date_Transaction', 'Type_Transaction', 'Montant_EUR', 
            'Effectué_Par', 'Catégorie', 'Description', 'Moyen_Paiement', 
            'Statut_Avance', 'ID_Utilisateur', 'ID_Maison', 'Date_Saisie', 
            'ID_Validateur', 'Date_Validation', 'Type_Transaction_Code', 'Statut_Avance_Code'
        ]
        
        report_df = report_df.reindex(columns=cols_final)
        
        # Formatage des dates pour Excel (facultatif mais plus propre)
        for col in ['Date_Transaction', 'Date_Saisie', 'Date_Validation']:
            if col in report_df.columns:
                 # Assurez-vous que les colonnes de date sont bien des datetime avant de formater
                report_df[col] = pd.to_datetime(report_df[col], errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S').fillna('')
    
    # Génération du fichier Excel en mémoire
    output = io.BytesIO()
    # Utilisation de XlsxWriter comme moteur pour gérer les encodages
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        report_df.to_excel(writer, sheet_name='Transactions', index=False, encoding='utf-8')
    
    return output.getvalue()


# -------------------------------------------------------------------
# --- 6. Interfaces Utilisateur et Logique ---
# -------------------------------------------------------------------

def log_transaction(user_id, house_id, house_name):
    """ Interface de saisie de dépense / recette """
    st.subheader(f"Saisir une Transaction pour {house_name}")
    
    categories = get_categories() 
    if not categories:
        st.warning("Impossible de charger les catégories. Vérifiez la collection 'smmd_categories' dans Firestore.")
        return

    category_options = sorted(list(categories.values()))
    category_map = {v: k for k, v in categories.items()}
    
    with st.form("transaction_form", clear_on_submit=True):
        st.markdown("##### Détails du Mouvement")
        col1, col2 = st.columns(2)

        transaction_type = col1.selectbox(
            "Type de Mouvement",
            ['Dépense/Avance', 'Recette Exceptionnelle']
        )
        
        amount = col2.number_input("Montant (€)", min_value=0.01, format="%.2f")
        
        payment_method = st.selectbox(
            "Moyen de Paiement",
            options=PAYMENT_METHODS,
            help="Paiement par la Maison = **Dépense Commune**. Paiement Personnel = **Avance de Fonds** (validation chef requise)."
        )

        category_name = st.selectbox("Catégorie", options=['N/A'] + category_options)
        
        description = st.text_area("Description Détaillée")
        
        # Assurer que l'objet datetime.date est utilisé pour éviter les erreurs de sérialisation
        date_saisie = st.date_input("Date de la transaction", value=date.today())
        
        submitted = st.form_submit_button("Enregistrer la Transaction", type="primary")

        if submitted:
            if amount <= 0:
                st.error("Le montant doit être supérieur à zéro.")
                return

            # LOGIQUE CRITIQUE: Classification Dépense/Avance
            tx_type_firestore = ''
            statut_avance = 'validée'
            
            if transaction_type == 'Recette Exceptionnelle':
                tx_type_firestore = 'recette_exceptionnelle'
            
            elif payment_method in PAYMENT_METHODS_HOUSE:
                tx_type_firestore = 'depense_commune'
            
            elif payment_method in PAYMENT_METHODS_PERSONAL:
                tx_type_firestore = 'depense_avance'
                statut_avance = 'en_attente'

            # --------------------------------------------------------------------------

            transaction_data = {
                'house_id': house_id,
                'user_id': user_id,
                'type': tx_type_firestore,
                'amount': amount,
                'category': category_map.get(category_name) if category_name != 'N/A' else 'N/A',
                'description': description,
                'payment_method': payment_method,
                'date': datetime.combine(date_saisie, datetime.min.time()),
                'created_at': datetime.now(),
                'statut_avance': statut_avance 
            }
            
            try:
                # Enregistrement Firestore réel
                db.collection(COL_TRANSACTIONS).add(transaction_data) 
                
                get_transactions_for_house.clear() # Invalider le cache

                msg = f"Transaction enregistrée ! Type: {TX_TYPE_MAP.get(tx_type_firestore)}"
                if statut_avance == 'en_attente':
                    msg += " (⚠️ **Avance en attente de validation** par le Chef de Maison)."
                st.success(msg)
                st.rerun()

            except Exception as e:
                st.error(f"Erreur d'enregistrement dans Firestore : {e}")

def allocation_management(user_id):
    """ Interface de gestion de l'allocation mensuelle """
    st.subheader("⚙️ Gestion de votre Allocation Mensuelle")
    st.info("Cette fonction nécessite une logique de BDD dédiée pour la collection `smmd_allocations` et n'est actuellement qu'une simulation.")
    
    # Simulation des données (à remplacer par une lecture/écriture Firestore sur COL_ALLOCATIONS)
    current_allocation = 350.0 
    
    with st.form("allocation_form"):
        new_amount = st.number_input(
            "Montant de l'allocation mensuelle (€)", 
            min_value=0.0, 
            value=current_allocation, 
            step=50.0,
            format="%.2f"
        )
        submitted = st.form_submit_button("Sauvegarder l'Allocation", type="primary")
        
        if submitted:
             # Simulation de l'écriture BDD
             st.success(f"Simulation: Allocation mensuelle mise à jour à {new_amount} € dans la BDD.")

def user_transaction_history_and_cancellation(house_id, user_id, user_role):
    """Affiche l'historique et permet l'annulation des transactions pour l'utilisateur."""
    st.subheader("Historique de vos dépenses et avances")

    # 1. Récupérer les transactions de l'utilisateur (Firestore)
    user_transactions_df = get_user_transactions(house_id, user_id)
    
    if user_transactions_df.empty:
        st.info("Vous n'avez pas encore saisi de transactions.")
        return

    # Préparer le DataFrame pour l'affichage
    display_df = user_transactions_df.copy()
    display_df['Montant'] = display_df['amount'].apply(lambda x: f"{x:,.2f} €")
    display_df['Type'] = display_df['type'].apply(lambda t: TX_TYPE_MAP.get(t, 'Autre'))
    display_df['Catégorie'] = display_df['category_name']
    display_df['Statut Avance'] = display_df['statut_avance'].apply(lambda s: AVANCE_STATUS.get(s, 'N/A'))
    display_df['Transaction_ID'] = display_df['id']

    cols_to_show = ['date', 'Type', 'Montant', 'Catégorie', 'description', 'payment_method', 'Statut Avance', 'Transaction_ID']
    display_df = display_df[cols_to_show].rename(columns={
        'date': 'Date', 
        'description': 'Description', 
        'payment_method': 'Moyen de Paiement'
    }).sort_values('Date', ascending=False)
    
    st.dataframe(display_df.drop(columns=['Transaction_ID']), use_container_width=True)

    st.markdown("#### 🗑️ Annuler une Saisie Récente")
    st.caption("Vous pouvez annuler toute transaction que vous avez saisie.")
    
    annulable_df = display_df.copy()

    if not annulable_df.empty:
        with st.form("form_annulation_transaction", clear_on_submit=True):
            col1, col2 = st.columns([3, 1])
            
            # S'assurer que seules les transactions de cet utilisateur sont dans la liste
            transaction_to_delete = col1.selectbox(
                "Sélectionnez la transaction à annuler :",
                options=annulable_df['Transaction_ID'].tolist(),
                # Utiliser .dt.strftime pour formater la date si c'est un datetime
                format_func=lambda id: f"{annulable_df[annulable_df['Transaction_ID'] == id]['Date'].iloc[0].strftime('%Y-%m-%d')} - {annulable_df[annulable_df['Transaction_ID'] == id]['Montant'].iloc[0]} ({annulable_df[annulable_df['Transaction_ID'] == id]['Description'].iloc[0][:30]}...)"
            )
            
            submitted = col2.form_submit_button("Annuler la Dépense", type="secondary")

            if submitted and transaction_to_delete:
                success, message = delete_transaction(
                    transaction_to_delete, 
                    house_id,
                    user_id,
                    user_role
                )

                if success:
                    st.success(message)
                    st.rerun()
                else:
                    st.error(message)
    else:
        st.info("Aucune transaction à annuler trouvée.")

def user_dashboard():
    """ Tableau de bord utilisateur """
    user_id = st.session_state['user_id']
    house_id = st.session_state['house_id']
    user_role = st.session_state['role']
    house_name = get_house_name(house_id)
    
    st.title(f"Tableau de Bord de la Maison {house_name}")
    
    st.markdown("---")
    
    tab1, tab2, tab3 = st.tabs(["Saisie Transaction", "Historique & Annulation", "Allocation Mensuelle"])
    
    with tab1:
        log_transaction(user_id, house_id, house_name) 
        
    with tab3:
        allocation_management(user_id)

    with tab2:
        user_transaction_history_and_cancellation(house_id, user_id, user_role)

def admin_transaction_management(house_id, admin_user_id, admin_user_role):
    """
    Interface de gestion/annulation de transactions pour le Chef de Maison.
    Permet de visualiser et d'annuler n'importe quelle transaction de la maison.
    """
    st.header("Gestion et Annulation des Transactions de la Maison")
    st.info("⚠️ Vous pouvez annuler n'importe quelle transaction de la maison. Cette action est irréversible.")
    
    df_all = get_transactions_for_house(house_id)
    
    if df_all.empty:
        st.info("Aucune transaction enregistrée pour cette maison.")
        return

    # Préparation du DataFrame pour l'affichage
    display_df = df_all.copy()
    display_df['Montant'] = display_df['amount'].apply(lambda x: f"{x:,.2f} €")
    display_df['Type'] = display_df['type'].apply(lambda t: TX_TYPE_MAP.get(t, 'Autre'))
    display_df['Catégorie'] = display_df['category_name']
    display_df['Statut Avance'] = display_df['statut_avance'].apply(lambda s: AVANCE_STATUS.get(s, 'N/A'))
    display_df['Transaction_ID'] = display_df['id']

    cols_to_show = ['date', 'Type', 'Montant', 'full_name', 'Catégorie', 'description', 'payment_method', 'Statut Avance', 'Transaction_ID']
    display_df = display_df[cols_to_show].rename(columns={
        'date': 'Date', 
        'full_name': 'Saisi par',
        'description': 'Description', 
        'payment_method': 'Moyen de Paiement'
    }).sort_values('Date', ascending=False)
    
    st.markdown("##### Toutes les transactions (les plus récentes en premier)")
    st.dataframe(display_df.drop(columns=['Transaction_ID']), use_container_width=True)

    # Interface d'annulation
    st.markdown("---")
    st.markdown("#### 🗑️ Annulation d'une Transaction")
    
    with st.form("form_admin_annulation_transaction", clear_on_submit=True):
        col1, col2 = st.columns([3, 1])
        
        transaction_to_delete = col1.selectbox(
            "Sélectionnez la transaction à annuler :",
            options=display_df['Transaction_ID'].tolist(),
            format_func=lambda id: f"{display_df[display_df['Transaction_ID'] == id]['Date'].iloc[0].strftime('%Y-%m-%d')} - {display_df[display_df['Transaction_ID'] == id]['Montant'].iloc[0]} ({display_df[display_df['Transaction_ID'] == id]['Saisi par'].iloc[0]})"
        )
        
        submitted = col2.form_submit_button("Annuler la Transaction SÉLECTIONNÉE", type="secondary")

        if submitted and transaction_to_delete:
            success, message = delete_transaction(
                transaction_to_delete, 
                house_id,
                admin_user_id,
                admin_user_role # Utilisation du rôle admin/chef pour la permission
            )

            if success:
                st.success(message)
                get_transactions_for_house.clear() # Assurer la mise à jour
                st.rerun()
            else:
                st.error(message)

def advance_validation_interface(house_id, validator_user_id):
    """ Interface visible uniquement par les Chefs de Maison pour valider les avances. """
    st.header("✅ Validation des Avances de Fonds")
    st.markdown("Veuillez valider les avances faites par les utilisateurs avant qu'elles n'affectent le solde à rembourser.")

    
    # Récupérer toutes les transactions de la maison (Firestore)
    df_all = get_transactions_for_house(house_id)
    
    # Filtrer uniquement les avances en attente
    display_df = df_all[
        (df_all['type'] == 'depense_avance') & 
        (df_all['statut_avance'] == 'en_attente')
    ].copy()
    
    if display_df.empty:
        st.success("Aucune avance de fonds en attente de validation pour le moment.")
        return

    # Préparation du DataFrame pour l'affichage
    display_df['Date'] = display_df['date'].apply(lambda d: d.strftime('%Y-%m-%d') if isinstance(d, datetime) else 'N/A')
    display_df['Montant'] = display_df['amount'].apply(lambda x: f"{x:,.2f} €")
    display_df = display_df.rename(columns={
        'full_name': 'Avancé par', 
        'description': 'Description',
        'payment_method': 'Moyen de Paiement',
        'id': 'Transaction_ID'
    })
    
    cols_to_show = ['Date', 'Montant', 'Avancé par', 'Description', 'Moyen de Paiement', 'Transaction_ID']
    display_df = display_df[cols_to_show].sort_values('Date', ascending=False)
    
    st.warning(f"{len(display_df)} Avance(s) en attente de validation :")
    st.dataframe(display_df.drop(columns=['Transaction_ID']), use_container_width=True)

    # Interface de validation
    st.markdown("---")
    st.markdown("#### Action de Validation")
    
    with st.form("form_validation_avance"):
        col1, col2 = st.columns([3, 1])
        
        transaction_to_validate = col1.selectbox(
            "Sélectionnez la transaction à valider :",
            options=display_df['Transaction_ID'].tolist(),
            format_func=lambda id: f"[{id[:6]}...] {display_df[display_df['Transaction_ID'] == id]['Montant'].iloc[0]} par {display_df[display_df['Transaction_ID'] == id]['Avancé par'].iloc[0]}"
        )
        
        submitted = col2.form_submit_button("Valider l'Avance", type="primary")

        if submitted and transaction_to_validate:
            success, message = validate_advance(
                transaction_to_validate, 
                house_id,
                validator_user_id
            )

            if success:
                st.success(message)
                st.rerun()
            else:
                st.error(message)

def admin_interface():
    """ Interface pour Admin général et Chef de Maison """
    
    st.sidebar.markdown("---")
    
    role = st.session_state['role']
    house_id = st.session_state['house_id']
    user_id = st.session_state['user_id']
    house_name = get_house_name(house_id)
    
    df_all_transactions = get_transactions_for_house(house_id)


    if role == 'chef_de_maison':
        # Menu spécifique pour le Chef de Maison
        admin_tab = st.sidebar.radio(
            "Menu Chef de Maison",
            ['Rapports et Analyse', 'Validation des Avances', 'Gestion des Transactions']
        )
        
        if admin_tab == 'Validation des Avances':
            advance_validation_interface(house_id, user_id) 
        
        elif admin_tab == 'Gestion des Transactions':
            # Nouvelle fonction pour gérer et annuler toutes les transactions de la maison
            admin_transaction_management(house_id, user_id, role)

        elif admin_tab == 'Rapports et Analyse':
            st.title(f"Rapports et Analyse pour {house_name}")
            st.info("Cette section est dédiée aux rapports avancés, aux analyses et à l'export des données.")
            
            st.markdown("### 📊 Export des Données")
            
            excel_data = generate_excel_report(df_all_transactions, house_name)
            
            st.download_button(
                label="Exporter toutes les transactions en Excel",
                data=excel_data,
                file_name=f'transactions_{house_name}_{date.today().strftime("%Y%m%d")}.xlsx',
                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                type="primary"
            )
            st.caption("Le fichier Excel contient toutes les données brutes, y compris les ID et les codes de statut, pour une analyse approfondie.")

            st.markdown("---")
            st.subheader("Simulations et Solde (À implémenter)")
            st.info("Contenu des rapports d'analyse financière et budgétaire de la Maison ici.")
            
    elif role == 'admin':
        # Menu spécifique pour l'Admin général
        admin_tab = st.sidebar.radio(
            "Menu Administration Générale",
            ['Gestion Utilisateurs et Maisons', 'Rapports Globaux']
        )
        st.title("Panneau d'Administration Général")
        
        if admin_tab == 'Gestion Utilisateurs et Maisons':
             st.info("Outils de gestion globale des maisons, utilisateurs et catégories (Ajouter, Modifier, Supprimer). (À implémenter)")
        elif admin_tab == 'Rapports Globaux':
             st.info("Rapports consolidés sur toutes les maisons et l'activité générale. (À implémenter)")
             st.markdown("### 📊 Export des Données de la Maison")
             excel_data = generate_excel_report(df_all_transactions, house_name)
            
             st.download_button(
                label=f"Exporter les transactions de {house_name} en Excel",
                data=excel_data,
                file_name=f'transactions_{house_name}_{date.today().strftime("%Y%m%d")}.xlsx',
                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                type="primary"
             )

             

# -------------------------------------------------------------------
# --- 7. Fonctions d'Authentification (Firestore) ---
# -------------------------------------------------------------------

def get_user_by_username(username):
    """ Récupère les données utilisateur à partir du nom d'utilisateur. """
    if not db: return None
    try:
        # Recherche par nom d'utilisateur
        query = db.collection(COL_USERS).where('username', '==', username).limit(1).stream()
        for doc in query:
            user_data = doc.to_dict()
            user_data['id'] = doc.id
            return user_data
        return None
    except Exception as e:
        #st.error(f"Erreur de recherche utilisateur: {e}")
        return None

def handle_login(username, password):
    """ Logique de connexion et vérification des rôles (Firestore Implémentation). """
    
    user_info = get_user_by_username(username)
    
    if user_info:
        stored_hashed_password = user_info.get('password')
        
        # Vérification du mot de passe
        is_default_password = (password == DEFAULT_PASSWORD)
        
        password_is_valid = False
        if is_default_password:
             password_is_valid = True # Le mot de passe par défaut est toujours accepté
        elif stored_hashed_password:
             try:
                 password_is_valid = bcrypt.checkpw(password.encode('utf-8'), stored_hashed_password.encode('utf-8'))
             except Exception:
                  password_is_valid = False # Échoue si le hash n'est pas bon ou manquant

        if password_is_valid:
            # Si c'est le mot de passe par défaut, forcer le changement
            must_change = is_default_password and stored_hashed_password # Seulement si un hash existe déjà, sinon on assume que l'utilisateur a créé son propre mdp
            
            st.session_state['logged_in'] = True
            st.session_state['user_id'] = user_info['id']
            st.session_state['house_id'] = user_info['house_id']
            st.session_state['role'] = user_info['role']
            st.session_state['user_data'] = {'first_name': user_info.get('first_name'), 'last_name': user_info.get('last_name')}
            st.session_state['must_change_password'] = must_change
            st.rerun()
        else:
            st.error("Mot de passe incorrect.")
    else:
        st.error("Nom d'utilisateur inconnu.")
        
def password_reset_interface(user_id):
    """ Interface de réinitialisation du mot de passe (Firestore Implémentation) """
    st.title("Réinitialisation du Mot de Passe")
    st.warning("Vous devez changer votre mot de passe par défaut pour des raisons de sécurité.")
    
    with st.form("reset_password_form"):
        new_password = st.text_input("Nouveau Mot de Passe", type="password")
        confirm_password = st.text_input("Confirmer le Mot de Passe", type="password")
        
        if st.form_submit_button("Changer le Mot de Passe", type="primary"):
            if new_password == confirm_password and len(new_password) >= 6:
                try:
                    # Chiffrement du nouveau mot de passe
                    hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                    
                    # Mise à jour Firestore
                    db.collection(COL_USERS).document(user_id).update({'password': hashed_password})
                    
                    st.session_state['must_change_password'] = False
                    st.success("Mot de passe changé avec succès ! Vous pouvez continuer.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Erreur lors de la mise à jour du mot de passe: {e}")
            else:
                st.error("Les mots de passe ne correspondent pas ou sont trop courts (min 6 caractères).")

def login_interface():
    """ Interface de connexion """
    st.title("Connexion à l'application SMMD Compta")
    
    with st.form("login_form"):
        username = st.text_input("Nom d'utilisateur")
        password = st.text_input("Mot de passe", type="password")
        submitted = st.form_submit_button("Se Connecter", type="primary")

        if submitted:
            handle_login(username, password)
            
    st.caption("Note: Assurez-vous d'avoir des utilisateurs dans la collection `smmd_users` de Firestore. Le mot de passe par défaut est `first123`.")

# -------------------------------------------------------------------
# --- 8. Lancement de l'Application ---
# -------------------------------------------------------------------

def main():
    # Initialisation des variables de session
    if 'logged_in' not in st.session_state:
        st.session_state['logged_in'] = False
        st.session_state['user_id'] = None
        st.session_state['role'] = None

    if not st.session_state.get('initialized'):
        # Le code d'initialisation en haut du fichier gère les erreurs et affiche un message.
        return

    if not st.session_state['logged_in']:
        login_interface()
    
    else:
        # Sidebar pour les utilisateurs connectés
        if st.sidebar.button("Déconnexion", type="secondary"):
            st.session_state.clear()
            st.rerun()

        st.sidebar.markdown(f"""
            **Connecté en tant que :** {st.session_state['user_data'].get('first_name')} 
            **Rôle :** {st.session_state['role'].capitalize()} 
            **Maison :** {get_house_name(st.session_state['house_id'])}
        """)
        st.sidebar.markdown("---")

        if st.session_state.get('must_change_password', False):
            # L'utilisateur doit changer son mot de passe
            password_reset_interface(st.session_state['user_id'])
            
        else:
            # Redirection vers le tableau de bord ou l'interface d'administration
            user_role = st.session_state['role']
            # L'admin général et le chef de maison utilisent la même fonction admin_interface pour le menu latéral
            if user_role in ['admin', 'chef_de_maison']:
                # L'admin_interface gère les sous-menus Chef de Maison
                admin_interface() 
            else: 
                user_dashboard() # Rôle 'utilisateur'
                

if __name__ == '__main__':
    st.set_page_config(page_title="SM MMD Compta", layout="wide")
    main()