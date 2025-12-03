#!/usr/bin/env bash

# Ce script est exécuté par Render pour lancer l'application

# 1. Configuration de l'environnement Streamlit (pour la sécurité et le port)
# Ces variables sont cruciales pour le déploiement sur un serveur cloud comme Render.
export STREAMLIT_SERVER_PORT="$PORT"
export STREAMLIT_SERVER_ENABLE_WEBSOCKETS=true
export STREAMLIT_SERVER_ENABLE_CORS=false
export STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION=false

# 2. Exécution de l'application Streamlit avec le port dynamique de Render
streamlit run app.py