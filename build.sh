#!/usr/bin/env bash

# Mise à jour des packages système et installation des dépendances pour bcrypt
# libffi-dev est nécessaire pour la compilation de bcrypt
sudo apt-get update
sudo apt-get install -y build-essential libssl-dev libffi-dev python3-dev

# Laisse Render exécuter l'installation des dépendances Python
# (Ceci est la commande par défaut de Render pour le build)
pip install -r requirements.txt