# 🚨🚨 MOT DE PASSE EN CLAIR INTÉGRÉ 🚨🚨
# Le mot de passe choisi est 'florent1234'.
password = "florent1234" 

if not password:
    print("ERREUR : Le mot de passe est vide.")
    return None

print(f"Mot de passe en clair à hacher : '{password}'")

# Encodage, hachage et décodage
password_bytes = password.encode('utf-8')
hashed_password_bytes = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
hashed_password_str = hashed_password_bytes.decode('utf-8')

print("\n" + "=" * 60)
print("COPIEZ CETTE CHAÎNE INTÉGRALEMENT ET COLLEZ-LA DANS FIREBASE")
print("CLÉ 'password' du document admin (RÔLE : admin) dans la collection smmd_users.")
print("=" * 60)
print(f"\n{hashed_password_str}\n")
print("=" * 60)

return hashed_password_str
