import streamlit as st
import os
import json
from firebase_admin import initialize_app, credentials, firestore, exceptions
from datetime import datetime, date, timedelta
import pandas as pd
import bcrypt
from functools import lru_cache 

# -------------------------------------------------------------------
# --- Constantes globales
# -------------------------------------------------------------------

# Ces ID correspondent aux noms de collection à la racine de Firestore
COL_TRANSACTIONS = 'smmd_transactions'
COL_HOUSES = 'smmd_houses' # Collection pour les Maisons (anciennement Foyers)
COL_USERS = 'smmd_users'
COL_ALLOCATIONS = 'smmd_allocations' 
COL_CATEGORIES = 'smmd_categories' 

# Liste des méthodes de paiement et des rôles pour les formulaires
PAYMENT_METHODS = ['carte', 'virement', 'liquide', 'chèque', 'autre']
ROLES = ['admin', 'utilisateur', 'chef_de_maison']
TITLES = ['Frère', 'Abbé']
# Le mot de passe par défaut pour les nouveaux utilisateurs
DEFAULT_PASSWORD = "first123" 

# Mappage des types de transaction pour l'affichage dans l'interface utilisateur
TX_TYPE_MAP = {
    'depense_commune': 'Dépense Commune (Fonds Maison)',
    'depense_avance': 'Avance de Fonds (Remboursement requis)',
    'recette_mensuelle': 'Recette (Allocation Mensuelle)',
    'recette_exceptionnelle': 'Recette Exceptionnelle',
    'remboursement': 'Remboursement d\'Avance'
}

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
    """
    try:
        app_id = firebase_config.get('app_id', 'default-smmd-app')
        
        from firebase_admin import get_app
        try:
            app = get_app(app_id)
        except ValueError:
            cred = credentials.Certificate(firebase_config)
            app = initialize_app(cred, name=app_id)
        
        return firestore.client(app=app)
        
    except Exception as e:
        st.error(f"Erreur d'initialisation Firebase : {e}")
        st.stop() 

# --- Initialisation du Client Firestore (Utilise la fonction mise en cache)
db = initialize_firebase_connection()


# -------------------------------------------------------------------
# --- Fonctions Utilitaires (Hachage, Caching BDD, Logique Année Scolaire)
# -------------------------------------------------------------------

def hash_password(password):
    """Hache un mot de passe en utilisant Bcrypt."""
    password_bytes = password.encode('utf-8')
    hashed_bytes = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
    return hashed_bytes.decode('utf-8')

def check_password(password, hashed_password):
    """Vérifie un mot de passe en clair avec son hash Bcrypt."""
    password_bytes = password.encode('utf-8')
    hashed_bytes = hashed_password.encode('utf-8')
    return bcrypt.checkpw(password_bytes, hashed_bytes)

def get_school_year_range(dt):
    """Retourne la date de début et de fin de l'année scolaire (1er Sep - 31 Août) contenant la date donnée."""
    if dt.month >= 9:
        start_year = dt.year
        end_year = dt.year + 1
    else:
        start_year = dt.year - 1
        end_year = dt.year
        
    start_date = date(start_year, 9, 1)
    end_date = date(end_year, 8, 31)
    
    return start_date, end_date

@st.cache_data(ttl=300)
def get_all_users():
    """Récupère tous les utilisateurs."""
    users_stream = db.collection(COL_USERS).stream()
    users_dict = {}
    for d in users_stream:
        user_data = d.to_dict()
        user_data.setdefault('house_id', 'INCONNU (Corriger Manuellement)')
        user_data.setdefault('must_change_password', False)
        user_data.setdefault('first_name', 'N/A')
        user_data.setdefault('last_name', 'N/A')
        user_data.setdefault('role', 'utilisateur')
        users_dict[d.id] = user_data
        
    return users_dict
    
@st.cache_data(ttl=300)
def get_all_houses():
    """Récupère toutes les maisons."""
    houses_stream = db.collection(COL_HOUSES).stream()
    return {d.id: d.to_dict() for d in houses_stream}

def get_house_name(house_id):
    """Récupère le nom d'une maison à partir de son ID (utilise le cache)"""
    return get_all_houses().get(house_id, {}).get('name', 'Maison Inconnue')

@st.cache_data(ttl=300)
def get_all_categories():
    """
    Récupère toutes les catégories de dépenses.
    Retourne un dictionnaire {category_id: category_name}.
    """
    categories_stream = db.collection(COL_CATEGORIES).stream()
    categories = {d.id: d.to_dict().get('name', d.id) for d in categories_stream}
    # S'assurer qu'il y a toujours une option si la BDD est vide
    if not categories:
        return {'autres': 'Autres (Veuillez définir des catégories)'}
    return categories

@st.cache_data(ttl=600) # Cache de 10 minutes pour les transactions
def get_house_transactions(house_id):
    """Récupère toutes les transactions pour une maison donnée."""
    if not house_id:
        return pd.DataFrame()
        
    try:
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
# --- Fonctions CRUD et Logique (Incluant les fonctions manquantes)
# -------------------------------------------------------------------

def delete_user(user_id):
    """Supprime un utilisateur et son enregistrement d'allocation."""
    try:
        # Supprimer l'utilisateur
        db.collection(COL_USERS).document(user_id).delete()
        
        # Supprimer son enregistrement d'allocation s'il existe
        allocation_doc = db.collection(COL_ALLOCATIONS).document(user_id)
        if allocation_doc.get().exists:
            allocation_doc.delete()
            
        st.toast(f"Utilisateur {user_id} et son allocation supprimés.", icon='🗑️')
        get_all_users.clear()
        return True
    except Exception as e:
        st.error(f"Erreur de suppression d'utilisateur : {e}")
        return False

def delete_house(house_id):
    """Supprime une maison (anciennement foyer)."""
    try:
        # 1. Mettre à jour les utilisateurs associés à 'INCONNU'
        # Pour éviter des erreurs si on tente de supprimer la maison sans avoir corrigé les utilisateurs
        users_to_update = db.collection(COL_USERS).where('house_id', '==', house_id).stream()
        batch = db.batch()
        for user_doc in users_to_update:
            batch.update(user_doc.reference, {'house_id': 'INCONNU (Corriger Manuellement)'})
        batch.commit()
        
        # 2. Supprimer la maison
        db.collection(COL_HOUSES).document(house_id).delete()
        
        st.toast(f"Maison {house_id} supprimée. Les utilisateurs associés ont été mis à jour.", icon='🗑️')
        get_all_houses.clear()
        get_all_users.clear() # Le cache utilisateur doit être effacé car des house_id ont changé
        return True
    except Exception as e:
        st.error(f"Erreur de suppression de maison : {e}")
        return False

def save_category(category_id, name):
    """Crée ou met à jour une catégorie de dépense."""
    try:
        db.collection(COL_CATEGORIES).document(category_id).set({
            'name': name,
            'updated_at': datetime.now().isoformat()
        })
        st.toast(f"Catégorie '{name}' enregistrée !", icon='✅')
        get_all_categories.clear()
        return True
    except Exception as e:
        st.error(f"Erreur lors de l'enregistrement de la catégorie : {e}")
        return False

def delete_category(category_id):
    """Supprime une catégorie de dépense."""
    try:
        # Vérification simple (peut être affinée pour vérifier si des transactions l'utilisent)
        db.collection(COL_CATEGORIES).document(category_id).delete()
        st.toast(f"Catégorie '{category_id}' supprimée.", icon='🗑️')
        get_all_categories.clear() 
        return True
    except Exception as e: 
        st.error(f"Erreur de suppression de catégorie : {e}")
        return False

def save_transaction(house_id, user_id, type, amount, nature, category_id, payment_method=None, notes=None):
    """Enregistre une nouvelle transaction dans Firestore. Maintenant avec category_id."""
    try:
        data = {
            'house_id': house_id, 
            'user_id': user_id, 
            'type': type, 
            'amount': round(float(amount), 2), 
            'nature': nature,
            'category_id': category_id, 
            'payment_method': payment_method, 
            'created_at': datetime.now().isoformat(),
            'status': 'validé' if type != 'depense_avance' else 'en_attente_remboursement', 
            'month_year': datetime.now().strftime('%Y-%m') 
        }
        doc_ref = db.collection(COL_TRANSACTIONS).add(data)
        st.toast("Transaction enregistrée !", icon='✅')
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
    """
    Définit ou met à jour l'allocation mensuelle d'un utilisateur (pour le mois en cours et les suivants).
    
    Cette fonction met à jour le taux dans COL_ALLOCATIONS (reporté) et met à jour/crée 
    la transaction de recette pour le mois en cours (effet immédiat sur le solde).
    """
    try:
        amount = round(float(amount), 2)
        
        # 1. Mettre à jour l'enregistrement d'allocation pour l'utilisateur (valeur reportée)
        db.collection(COL_ALLOCATIONS).document(user_id).set({'amount': amount, 'house_id': house_id, 'updated': datetime.now().isoformat()})
        
        # 2. Mettre à jour ou créer la transaction de 'recette_mensuelle' pour le mois en cours
        current_month = datetime.now().strftime('%Y-%m')
        user_name = st.session_state['user_data'].get('first_name', user_id)
        
        # Trouver la transaction d'allocation pour ce mois
        q = db.collection(COL_TRANSACTIONS).where('user_id', '==', user_id).where('month_year', '==', current_month).where('type', '==', 'recette_mensuelle').limit(1).stream()
        existing_tx = next(q, None)
        
        # La recette mensuelle n'a pas besoin de catégorie de dépense
        category_id_for_revenue = 'allocation_mensuelle' 
        
        if existing_tx:
            # Mettre à jour le montant de la transaction existante
            db.collection(COL_TRANSACTIONS).document(existing_tx.id).update({'amount': amount})
        else:
            # Créer la transaction si elle n'existe pas pour ce mois
            save_transaction(house_id, user_id, 'recette_mensuelle', amount, f"Allocation Mensuelle de {user_name} (Mois en cours)", category_id_for_revenue, payment_method='virement')
            
        st.toast(f"Allocation mensuelle mise à jour à {amount}€ pour ce mois et les suivants.", icon="💸")
        get_house_transactions.clear() 
        return True
    except Exception as e: st.error(f"Erreur lors de la mise à jour de l'allocation: {e}")

def calculate_balances(df, current_user_id):
    """Calcule le solde total de la maison et le solde personnel de l'utilisateur."""
    if df.empty:
        return 0.00, 0.00
    
    # Solde de la Maison (Recettes - Dépenses)
    house_revenues = df[df['type'].str.contains('recette')]['amount'].sum()
    house_expenses = df[df['type'].isin(['depense_commune', 'depense_avance', 'remboursement'])]['amount'].sum()
    house_balance = house_revenues - house_expenses
    
    # Solde Personnel (Avances non remboursées)
    user_advances_due = df[(df['user_id'] == current_user_id) & (df['type'] == 'depense_avance') & (df['status'] == 'en_attente_remboursement')]['amount'].sum()
    user_balance = user_advances_due 
    
    return round(house_balance, 2), round(user_balance, 2)
        
# -------------------------------------------------------------------
# --- Fonctions d'Extraction de Données (CHEF DE MAISON)
# -------------------------------------------------------------------

def filter_transactions_by_period(df, start_date=None, end_date=None):
    """Filtre un DataFrame de transactions par date."""
    if df.empty:
        return df
        
    df_filtered = df.copy()
    
    if 'created_at_dt' not in df_filtered.columns:
        df_filtered['created_at_dt'] = pd.to_datetime(df_filtered['created_at'])

    if start_date:
        df_filtered = df_filtered[df_filtered['created_at_dt'] >= pd.to_datetime(start_date)]
    
    if end_date:
        end_date_inclusive = pd.to_datetime(end_date) + timedelta(days=1) - timedelta(seconds=1)
        df_filtered = df_filtered[df_filtered['created_at_dt'] <= end_date_inclusive]
        
    return df_filtered.sort_values(by='created_at_dt', ascending=False)

def display_extraction_results(df_filtered, start_date_filter, end_date_filter, period_name, house_id):
    """Affiche les résultats de l'extraction avec séparation des recettes et dépenses."""
    
    st.subheader(f"Transactions de la Maison pour la période : {period_name} ({start_date_filter} au {end_date_filter})")
    
    if df_filtered.empty:
        st.warning("Aucune transaction trouvée pour cette période.")
        return
        
    # Identification des recettes et dépenses
    df_revenues = df_filtered[df_filtered['type'].str.contains('recette')]
    df_expenses = df_filtered[df_filtered['type'].str.contains('depense') | (df_filtered['type'] == 'remboursement')]
    
    total_revenues = df_revenues['amount'].sum()
    total_expenses = df_expenses['amount'].sum()
    net_balance = total_revenues - total_expenses
    
    # Affichage des métriques clés
    col_rev, col_exp, col_bal = st.columns(3)
    col_rev.metric("Total Recettes (€)", f"{total_revenues:,.2f} €", delta="Inclut les allocations et recettes exceptionnelles")
    col_exp.metric("Total Dépenses (€)", f"{total_expenses:,.2f} €", delta="Inclut les dépenses communes, avances et remboursements")
    col_bal.metric("Solde Net de la Période (€)", f"{net_balance:,.2f} €")
    
    st.markdown("---")
    
    # Agrégation des dépenses par catégorie
    st.subheader("Synthèse des Dépenses par Catégorie")
    df_depenses_par_cat = df_expenses.groupby('category_id')['amount'].sum().reset_index()
    # On met le nom de la colonne du montant pour l'affichage
    df_depenses_par_cat = df_depenses_par_cat.rename(columns={'category_id': 'Catégorie', 'amount': 'Total Dépensé (€)'})
    df_depenses_par_cat['Pourcentage (%)'] = (df_depenses_par_cat['Total Dépensé (€)'] / total_expenses * 100).round(2)
    df_depenses_par_cat['Total Dépensé (€)'] = df_depenses_par_cat['Total Dépensé (€)'].apply(lambda x: f"{x:,.2f} €")
    
    # Trier par montant décroissant
    st.dataframe(df_depenses_par_cat.sort_values(by='Pourcentage (%)', ascending=False), use_container_width=True, hide_index=True)
    
    st.markdown("---")
    
    st.subheader("Détail des Transactions")
    
    # Préparation du DataFrame pour l'affichage/export
    display_df = df_filtered.copy()
    display_df['Montant (€)'] = display_df['amount'].apply(lambda x: f"{x:,.2f}")
    display_df['Date'] = display_df['created_at_dt'].dt.strftime('%d/%m/%Y %H:%M')
    # Utiliser le mappage pour un affichage lisible
    display_df['Type'] = display_df['type'].map(TX_TYPE_MAP).fillna(display_df['type']).str.capitalize()
    
    export_df = display_df.rename(columns={
        'nature': 'Description',
        'category_id': 'Catégorie', 
        'user_id': 'Utilisateur ID',
        'payment_method': 'Méthode',
        'status': 'Statut',
        'house_id': 'Maison ID'
    })
    
    cols_to_display = ['Date', 'Description', 'Catégorie', 'Montant (€)', 'Type', 'Utilisateur ID', 'Méthode', 'Statut']
    
    st.dataframe(export_df[cols_to_display], use_container_width=True, hide_index=True)

    # Bouton d'export
    csv_export = export_df[cols_to_display].to_csv(index=False).encode('utf-8')
    st.download_button(
        label="Télécharger les données filtrées (CSV)",
        data=csv_export,
        file_name=f'transactions_{house_id}_{start_date_filter}_a_{end_date_filter}.csv',
        mime='text/csv',
        type="primary"
    )

def house_manager_extraction_interface(house_id):
    """Interface d'extraction de données pour le Chef de Maison."""
    st.header("📊 Extraction et Analyse des Transactions de la Maison")
    
    df_all_tx = get_house_transactions(house_id)
    
    if df_all_tx.empty:
        st.info("Aucune transaction n'a encore été enregistrée pour cette maison pour l'extraction.")
        return

    if 'created_at_dt' not in df_all_tx.columns:
        df_all_tx['created_at_dt'] = pd.to_datetime(df_all_tx['created_at'])

    min_date = df_all_tx['created_at_dt'].min().date()
    max_date = df_all_tx['created_at_dt'].max().date()
    
    st.subheader("Choisir la Période d'Analyse")
    
    filter_type = st.radio(
        "Type de Filtre", 
        ['Période Personnalisée', 'Par Mois', 'Par Trimestre (Scolaire)', 'Par Année Scolaire Entière'], 
        horizontal=True
    )
    
    start_date_filter = None
    end_date_filter = None
    period_label = filter_type
    
    # Logique de sélection de période (Inchangement)
    if filter_type == 'Période Personnalisée':
        col_start, col_end = st.columns(2)
        with col_start:
            start_date_filter = st.date_input("Date de Début", value=min_date, min_value=min_date, max_value=max_date)
        with col_end:
            end_date_filter = st.date_input("Date de Fin", value=max_date, min_value=min_date, max_value=max_date)
        
        if start_date_filter > end_date_filter:
            st.error("La date de début ne peut pas être postérieure à la date de fin.")
            return
        period_label = "Période Personnalisée"

    elif filter_type == 'Par Mois':
        df_all_tx['month_str'] = df_all_tx['created_at_dt'].dt.strftime('%Y-%m')
        unique_months = sorted(df_all_tx['month_str'].unique(), reverse=True)
        
        if not unique_months: st.info("Aucune transaction avec date enregistrée."); return

        selected_month_str = st.selectbox("Sélectionner un Mois (AAAA-MM)", unique_months)
        
        selected_month = datetime.strptime(selected_month_str, '%Y-%m')
        start_date_filter = selected_month.date()
        if selected_month.month == 12:
            end_date_filter = date(selected_month.year, 12, 31)
        else:
            end_date_filter = date(selected_month.year, selected_month.month + 1, 1) - timedelta(days=1)
        period_label = f"Mois de {selected_month_str}"
            
    elif filter_type == 'Par Trimestre (Scolaire)':
        def get_school_year_quarter(dt):
            if dt.month >= 9:
                school_year = f"{dt.year}-{dt.year + 1}"
                quarter_idx = 1
            else:
                school_year = f"{dt.year - 1}-{dt.year}"
                if dt.month >= 6: quarter_idx = 4
                elif dt.month >= 3: quarter_idx = 3
                else: quarter_idx = 2
            return school_year, quarter_idx

        df_temp = df_all_tx.copy()
        df_temp['school_info'] = df_temp['created_at_dt'].apply(get_school_year_quarter)
        df_temp['school_year_str'] = df_temp['school_info'].apply(lambda x: f"{x[0]} T{x[1]}")
        
        df_temp['sort_key'] = df_temp['school_info'].apply(lambda x: (int(x[0].split('-')[0]), x[1]))
        unique_options = sorted(df_temp['school_year_str'].unique(), key=lambda x: df_temp[df_temp['school_year_str'] == x]['sort_key'].iloc[0], reverse=True)
        
        if not unique_options: st.info("Aucune transaction avec date enregistrée."); return
             
        selected_quarter_str = st.selectbox("Sélectionner un Trimestre (Année Scolaire)", unique_options)
        
        sy_part, q_part = selected_quarter_str.split(' T')
        start_year = int(sy_part.split('-')[0])
        quarter_num = int(q_part)
        period_label = selected_quarter_str
        
        if quarter_num == 1: 
            start_date_filter = date(start_year, 9, 1); end_date_filter = date(start_year, 11, 30)
        elif quarter_num == 2: 
            start_date_filter = date(start_year, 12, 1)
            next_month = date(start_year + 1, 3, 1)
            end_date_filter = next_month - timedelta(days=1)
        elif quarter_num == 3: 
            start_date_filter = date(start_year + 1, 3, 1); end_date_filter = date(start_year + 1, 5, 31)
        elif quarter_num == 4: 
            start_date_filter = date(start_year + 1, 6, 1); end_date_filter = date(start_year + 1, 8, 31)

    elif filter_type == 'Par Année Scolaire Entière':
        all_school_years = []
        for dt in df_all_tx['created_at_dt'].dt.date.unique():
            sy_start, sy_end = get_school_year_range(dt)
            sy_str = f"{sy_start.year}-{sy_end.year}"
            if sy_str not in all_school_years:
                all_school_years.append(sy_str)

        all_school_years.sort(reverse=True)
        
        if not all_school_years: st.info("Aucune transaction avec date enregistrée."); return
        
        selected_sy_str = st.selectbox("Sélectionner une Année Scolaire", all_school_years)
        
        sy_start_year = int(selected_sy_str.split('-')[0])
        sy_end_year = int(selected_sy_str.split('-')[1])
        
        start_date_filter = date(sy_start_year, 9, 1)
        end_date_filter = date(sy_end_year, 8, 31)
        period_label = f"Année Scolaire {selected_sy_str}"


    # 3. Filtrage et affichage
    if start_date_filter and end_date_filter:
        df_filtered = filter_transactions_by_period(df_all_tx, start_date_filter, end_date_filter)
        
        st.markdown("---")
        display_extraction_results(df_filtered, start_date_filter, end_date_filter, period_label, house_id)


def user_dashboard(): 
    """Affiche le tableau de bord de l'utilisateur pour la gestion des dépenses et recettes."""
    user_data = st.session_state['user_data']
    house_id = st.session_state['house_id']
    user_id = st.session_state['user_id']
    house_name = get_house_name(house_id)
    
    st.title(f"🏠 Gestion pour {house_name}")
    st.header(f"Bonjour, {user_data.get('first_name', 'Utilisateur')}!")
    
    is_house_manager = st.session_state['role'] == 'chef_de_maison'
    is_user_or_manager = st.session_state['role'] in ['utilisateur', 'chef_de_maison']

    if is_house_manager:
        with st.expander("👑 Outils d'Extraction pour Chef de Maison", expanded=False):
            house_manager_extraction_interface(house_id)
        st.markdown("---")
        
    # --- Interface de Gestion d'Allocation pour Utilisateurs/Chefs de Maison ---
    if is_user_or_manager:
        # Récupérer l'allocation actuelle de l'utilisateur
        allocation_doc = db.collection(COL_ALLOCATIONS).document(user_id).get()
        current_allocation_amount = allocation_doc.to_dict().get('amount', 0.00) if allocation_doc.exists else 0.00

        with st.expander("💸 Ma Gestion d'Allocation Mensuelle", expanded=False):
            st.subheader(f"Allocation Mensuelle Actuelle : {current_allocation_amount:,.2f} €")
            st.info("Cette allocation sera reportée pour tous les mois suivants. Toute modification ajustera également la recette du mois en cours.")
            
            with st.form("user_allocation_form", clear_on_submit=False):
                new_allocation_amount = st.number_input(
                    "Définir/Modifier mon Allocation Mensuelle (€)", 
                    min_value=0.00, 
                    value=current_allocation_amount, 
                    format="%.2f", 
                    key="user_allocation_input"
                )
                
                if st.form_submit_button("Sauvegarder mon Allocation", type="primary"):
                    if new_allocation_amount >= 0:
                        set_monthly_allocation(user_id, house_id, new_allocation_amount)
                        st.rerun()
                    else:
                        st.error("Le montant de l'allocation doit être positif ou nul.")

    # --- Affichage des soldes (inchangé) ---
    df_transactions = get_house_transactions(house_id)
    house_balance, user_balance = calculate_balances(df_transactions, user_id)

    col_h_bal, col_u_bal = st.columns(2)
    
    with col_h_bal:
        st.metric(label="Solde de la Maison (Total)", 
                  value=f"{house_balance:,.2f} €", 
                  delta="Solde net (Recettes - Dépenses)",
                  delta_color="normal")
        
    with col_u_bal:
        st.metric(label="Mes Avances en Attente de Remboursement", 
                  value=f"{user_balance:,.2f} €", 
                  delta_color="off", 
                  help="Montant total des dépenses avancées non encore remboursées.")

    st.markdown("---")
    
    # Récupération des catégories pour le formulaire de dépense
    categories_map = get_all_categories()
    category_names = list(categories_map.values())
    
    # Utilisation d'un mapping inverse pour retrouver l'ID à partir du nom sélectionné
    name_to_id = {v: k for k, v in categories_map.items()}

    tab_depense, tab_recette = st.tabs(["💶 Enregistrer une Dépense", "💰 Enregistrer une Recette Exceptionnelle"])

    # --- TAB 1: ENREGISTRER UNE DÉPENSE ---
    with tab_depense:
        with st.form("new_expense_form", clear_on_submit=True):
            st.subheader("Détails de la Dépense")
            
            col1, col2 = st.columns(2)
            
            with col1:
                # La nature est maintenant le libellé de la transaction
                nature = st.text_input("Libellé de la Dépense (ex: Achat de lait)", key="nature_depense_input")
                # Sélection de la Catégorie
                selected_category_name = st.selectbox("Catégorie de Dépense", category_names, key="category_select")
                
            with col2:
                amount = st.number_input("Montant (€)", min_value=0.01, format="%.2f", key="amount_depense_input")
                
            # Colonne pour le financement
            col3, col4 = st.columns(2)

            with col3:
                funding_type = st.radio(
                    "Comment la dépense a-t-elle été payée ?", 
                    options=[
                        'Fonds de la Maison (CB Maison, Virement Maison)', 
                        'Fonds Personnel (Avance, Remboursement requis)'
                    ],
                    key="funding_type_radio"
                )
                
            with col4:
                # Détermine les options de paiement en fonction du choix
                if 'Fonds de la Maison' in funding_type:
                    tx_type = 'depense_commune'
                    payment_options = ['carte', 'virement', 'autre']
                    payment_method = st.selectbox("Méthode de Paiement de la Maison", payment_options, key="method_depense_foyer")
                    st.info("Cette dépense diminue directement le solde de la maison.")
                else:
                    tx_type = 'depense_avance'
                    payment_options = ['carte', 'chèque', 'liquide', 'virement'] # Choix utilisateur pour l'avance
                    payment_method = st.selectbox("Méthode de Paiement Personnel", payment_options, key="method_depense_perso")
                    st.warning("Ceci est une Avance de Fonds. Un remboursement par la maison est dû.")
            
            notes = st.text_area("Notes additionnelles (facultatif)", key="notes_depense_input")
            
            if st.form_submit_button("Enregistrer la Dépense", type="primary"):
                # Récupérer l'ID de la catégorie
                category_id_to_save = name_to_id.get(selected_category_name, 'non_categorise')

                if not nature or amount is None or amount <= 0:
                    st.error("Veuillez remplir le libellé et spécifier un montant valide.")
                else:
                    save_transaction(house_id, user_id, tx_type, amount, nature, category_id_to_save, payment_method, notes)
                    st.rerun() 

    # --- TAB 2: ENREGISTRER UNE RECETTE EXCEPTIONNELLE ---
    with tab_recette:
        with st.form("new_revenue_form", clear_on_submit=True):
            st.subheader("Détails de la Recette")
            
            col3, col4 = st.columns(2)
            
            with col3:
                nature_recette = st.text_input("Libellé de la Recette (ex: Don, Entrée d'argent non planifiée)", key="nature_recette_input")
                amount_recette = st.number_input("Montant (€)", min_value=0.01, format="%.2f", key="amount_recette_input")
                # La catégorie pour les recettes est 'recette' par défaut
                category_id_recette = 'recette_exceptionnelle'
                
            with col4:
                payment_method_recette = st.selectbox("Méthode de Réception", PAYMENT_METHODS, key="method_recette_input")
                st.info("Cette recette augmente le solde de la caisse commune.")
            
            notes_recette = st.text_area("Notes additionnelles (facultatif)", key="notes_recette_input")
            
            if st.form_submit_button("Enregistrer la Recette", type="primary"):
                if not nature_recette or amount_recette is None or amount_recette <= 0:
                    st.error("Veuillez remplir le libellé et spécifier un montant valide.")
                else:
                    save_transaction(house_id, user_id, 'recette_exceptionnelle', amount_recette, nature_recette, category_id_recette, payment_method_recette, notes_recette)
                    st.rerun() 
                    

    st.markdown("---")
    
    # 4. Affichage des Transactions
    st.subheader("Historique des Transactions Récentes")
    if df_transactions.empty:
        st.info("Aucune transaction enregistrée pour l'instant.")
    else:
        display_df = df_transactions.copy()
        display_df['Montant'] = display_df['amount'].apply(lambda x: f"{x:,.2f} €")
        display_df['Date'] = pd.to_datetime(display_df['created_at']).dt.strftime('%d/%m/%Y %H:%M')
        display_df['Type'] = display_df['type'].map(TX_TYPE_MAP).fillna(display_df['type']).str.capitalize()
        
        display_df = display_df.rename(columns={
            'nature': 'Description',
            'category_id': 'Catégorie', 
            'user_id': 'Par',
            'payment_method': 'Méthode',
            'status': 'Statut'
        })
        
        cols_to_display = ['Date', 'Description', 'Catégorie', 'Montant', 'Type', 'Par', 'Méthode', 'Statut', 'doc_id']
        st.dataframe(display_df[cols_to_display].head(10), use_container_width=True, hide_index=True)


def admin_interface():
    """Affiche l'interface Admin pour la gestion des utilisateurs, des maisons et des catégories."""
    st.title("👑 Panneau d'Administration")
    
    tab1, tab2, tab3, tab4 = st.tabs(["Gestion Utilisateurs", "Gestion Maisons", "Paramètres Allocation", "Gestion Catégories"])
    
    # --- TAB 1: GESTION UTILISATEURS ---
    with tab1:
        st.header("Utilisateurs Actuels")
        users = get_all_users() 
        
        if users:
            users_df = pd.DataFrame(users.values(), index=users.keys())
            st.dataframe(
                users_df[['first_name', 'last_name', 'role', 'house_id', 'must_change_password']], 
                use_container_width=True
            )
            
            st.markdown("---")
            st.subheader("Supprimer un Utilisateur")
            col_del, col_space = st.columns([1, 2])
            with col_del:
                user_to_delete = st.selectbox("ID Utilisateur à Supprimer", users.keys(), key="del_user_select")
                
                if st.button(f"Confirmer la Suppression de {user_to_delete}", key="confirm_del_user", type="secondary"):
                    delete_user(user_to_delete)
        else:
            st.info("Aucun utilisateur enregistré.")
            
        st.markdown("---")
        st.subheader("Ajouter un Nouvel Utilisateur")
        with st.form("new_user_form", clear_on_submit=True):
            col_u1, col_u2, col_u3 = st.columns(3)
            with col_u1:
                new_uid = st.text_input("ID Utilisateur (Login)") 
                first_name = st.text_input("Prénom")
            with col_u2:
                last_name = st.text_input("Nom")
                role = st.selectbox("Rôle", ROLES)
            with col_u3:
                title = st.selectbox("Titre", TITLES)
                available_houses = get_all_houses()
                # Remplacement "Foyer Associé" par "Maison Associée"
                house_id = st.selectbox("Maison Associée", available_houses.keys(), format_func=get_house_name, disabled=not available_houses)
                
            if st.form_submit_button("Créer l'Utilisateur", type="primary"):
                if not new_uid or not first_name or not last_name:
                    st.error("L'ID Utilisateur, le Prénom et le Nom sont obligatoires.")
                elif db.collection(COL_USERS).document(new_uid).get().exists:
                    st.error("Cet ID Utilisateur existe déjà.")
                elif not available_houses:
                    # Remplacement "Foyer" par "Maison"
                    st.error("Vous devez créer au moins une Maison avant d'ajouter un utilisateur.")
                else:
                    new_user_data = {
                        'first_name': first_name,
                        'last_name': last_name,
                        'title': title,
                        'role': role,
                        'house_id': house_id,
                        'password_hash': hash_password(DEFAULT_PASSWORD), 
                        'must_change_password': True, 
                        'created_at': datetime.now().isoformat()
                    }
                    try:
                        db.collection(COL_USERS).document(new_uid).set(new_user_data)
                        st.success(f"Utilisateur {new_uid} créé avec le mot de passe par défaut : {DEFAULT_PASSWORD}")
                        get_all_users.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erreur de création: {e}")

    # --- TAB 2: GESTION MAISONS ---
    with tab2:
        st.header("Maisons Actuelles")
        houses = get_all_houses()
        
        if houses:
            houses_df = pd.DataFrame(houses.values(), index=houses.keys())
            st.dataframe(houses_df, use_container_width=True)

            st.markdown("---")
            st.subheader("Supprimer une Maison")
            col_del, col_space = st.columns([1, 2])
            with col_del:
                # Remplacement "ID Foyer" par "ID Maison"
                house_to_delete = st.selectbox("ID Maison à Supprimer", houses.keys(), key="del_house_select")
                
                if st.button(f"Confirmer la Suppression de {house_to_delete}", key="confirm_del_house", type="secondary"):
                    delete_house(house_to_delete)
        else:
            st.info("Aucune maison enregistrée.")

        st.markdown("---")
        st.subheader("Ajouter une Nouvelle Maison")
        with st.form("new_house_form", clear_on_submit=True):
            # Remplacement "ID Foyer" par "ID Maison"
            house_id = st.text_input("ID Maison (Unique)")
            # Remplacement "Nom du Foyer" par "Nom de la Maison"
            house_name = st.text_input("Nom de la Maison (Ex: Maison Bleue)")
            
            if st.form_submit_button("Créer la Maison", type="primary"):
                if not house_id or not house_name:
                    st.error("L'ID et le Nom de la Maison sont obligatoires.")
                elif db.collection(COL_HOUSES).document(house_id).get().exists:
                    st.error("Cet ID de Maison existe déjà.")
                else:
                    try:
                        db.collection(COL_HOUSES).document(house_id).set({'name': house_name, 'created_at': datetime.now().isoformat()})
                        st.success(f"Maison '{house_name}' créée.")
                        get_all_houses.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erreur de création: {e}")

    # --- TAB 3: PARAMÈTRES ALLOCATION ---
    with tab3:
        st.header("Définir l'Allocation Mensuelle")
        st.info("Cette allocation sera utilisée pour générer ou mettre à jour la recette mensuelle de l'utilisateur. Elle sera reportée pour tous les mois suivants.")
        
        users = get_all_users()
        user_ids = list(users.keys())
        
        if user_ids:
            user_options = {uid: f"{users[uid].get('first_name', uid)} ({uid})" for uid in user_ids}
            selected_user_id = st.selectbox("Sélectionner l'Utilisateur", user_ids, format_func=lambda uid: user_options[uid], key="allocation_user_select")
            
            allocation_amount = st.number_input(
                f"Allocation (€) pour {users[selected_user_id].get('first_name')}", 
                min_value=0.00, 
                format="%.2f", 
                key="allocation_input"
            )
            
            if st.button("Mettre à jour l'Allocation", type="primary"):
                if selected_user_id and users.get(selected_user_id, {}).get('house_id'):
                    set_monthly_allocation(selected_user_id, users[selected_user_id]['house_id'], allocation_amount)
                    st.rerun()
                else:
                    # Remplacement "foyer" par "maison"
                    st.error("Veuillez vérifier que l'utilisateur a une maison associée.")
        else:
            st.warning("Aucun utilisateur à configurer. Créez un utilisateur d'abord.")
            
    # --- TAB 4: GESTION CATÉGORIES ---
    with tab4:
        st.header("Gestion des Catégories de Dépenses")
        categories = get_all_categories()
        
        st.subheader("Catégories Actuelles")
        if categories:
            # Filtrer les catégories système (comme 'autres' ou 'allocation_mensuelle') pour ne montrer que celles définies par l'utilisateur
            display_categories = {k: v for k, v in categories.items() if k not in ['autres', 'allocation_mensuelle', 'recette_exceptionnelle']}
            
            if display_categories:
                cat_df = pd.DataFrame(display_categories.values(), index=display_categories.keys(), columns=['Nom Affiché'])
                st.dataframe(cat_df, use_container_width=True)
                
                st.markdown("---")
                st.subheader("Supprimer une Catégorie")
                col_del_cat, col_space_cat = st.columns([1, 2])
                with col_del_cat:
                    cat_to_delete_id = st.selectbox("ID de Catégorie à Supprimer", display_categories.keys(), key="del_cat_select")
                    
                    if st.button(f"Confirmer la Suppression de '{display_categories[cat_to_delete_id]}'", key="confirm_del_cat", type="secondary"):
                        delete_category(cat_to_delete_id)
                        st.rerun()
            else:
                st.info("Aucune catégorie définie pour l'instant.")
        else:
            st.info("Aucune catégorie définie.")

        st.markdown("---")
        st.subheader("Ajouter/Modifier une Catégorie")
        with st.form("new_category_form", clear_on_submit=True):
            col_c1, col_c2 = st.columns(2)
            with col_c1:
                category_id = st.text_input("ID Catégorie (Clé unique, sans espaces ni accents)", key="cat_id_input")
            with col_c2:
                category_name = st.text_input("Nom Affiché de la Catégorie (ex: 'Frais de nourriture')", key="cat_name_input")
                
            if st.form_submit_button("Sauvegarder la Catégorie", type="primary"):
                if not category_id or not category_name:
                    st.error("L'ID et le Nom de la Catégorie sont obligatoires.")
                else:
                    save_category(category_id, category_name)
                    st.rerun()

# -------------------------------------------------------------------
# --- Logique d'Authentification et Flux Principal (Inchangement)
# -------------------------------------------------------------------

def password_reset_interface(user_id):
    """Interface pour forcer un changement de mot de passe à la première connexion."""
    st.title("🔒 Premier Mot de Passe: Changement Obligatoire")
    st.warning("Pour des raisons de sécurité, veuillez définir un nouveau mot de passe.")
    
    new_password = st.text_input("Nouveau Mot de Passe", type="password", key="new_pw_reset")
    confirm_password = st.text_input("Confirmer le Nouveau Mot de Passe", type="password", key="confirm_pw_reset")

    if st.button("Changer le Mot de Passe", type="primary"):
        if new_password != confirm_password:
            st.error("Les mots de passe ne correspondent pas.")
        elif len(new_password) < 6:
            st.error("Le mot de passe doit contenir au moins 6 caractères.")
        else:
            try:
                hashed_new_password = hash_password(new_password)
                
                db.collection(COL_USERS).document(user_id).update({
                    'password_hash': hashed_new_password,
                    'must_change_password': False 
                })
                
                st.success("Mot de passe mis à jour avec succès! Veuillez vous reconnecter.")
                st.session_state.clear()
                st.rerun()
                
            except Exception as e:
                st.error(f"Erreur lors de la mise à jour du mot de passe: {e}")


def authentication_and_main_flow():
    """Gère l'authentification et l'affichage de l'interface principale."""
    
    if 'logged_in' not in st.session_state:
        st.session_state['logged_in'] = False
        st.session_state['role'] = None
        st.session_state['user_id'] = None
        st.session_state['house_id'] = None
        st.session_state['user_data'] = {}
        st.session_state['must_change_password'] = False


    if not st.session_state['logged_in']:
        
        st.header("Connexion au Portail de Gestion")
        
        with st.form("login_form"):
            st.subheader("Identifiez-vous")
            username = st.text_input("Nom d'utilisateur (votre ID unique)", key="login_username_input")
            password = st.text_input("Mot de passe", type="password", key="login_password_input") 
            
            if st.form_submit_button("Se Connecter", type="primary"):
                try:
                    user_doc = db.collection(COL_USERS).document(username).get()
                    if user_doc.exists:
                        user_data = user_doc.to_dict()
                        hashed_pw = user_data.get('password_hash', '')
                        
                        if check_password(password, hashed_pw):
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
            
        st.caption(f"Note: Le mot de passe par défaut pour les nouveaux utilisateurs est : `{DEFAULT_PASSWORD}`")


    else:
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
            password_reset_interface(st.session_state['user_id'])
            
        else:
            if st.session_state['role'] == 'admin':
                admin_interface()
            else: 
                user_dashboard()

# -------------------------------------------------------------------
# --- Lancement de l'Application ---
# -------------------------------------------------------------------
if __name__ == '__main__':
    st.set_page_config(page_title="SM Mediadrive", layout="wide", initial_sidebar_state="expanded")
    authentication_and_main_flow()