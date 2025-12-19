"""
Script d'initialisation des comptes Twitter pour twscrape.
À exécuter une seule fois au setup initial.

Usage:
    python scripts/init_twscrape_accounts.py

Requirements:
    - twscrape installé (pip install twscrape)
    - Credentials Twitter dans .env
"""

import asyncio
import os

from dotenv import load_dotenv
from twscrape import API

# Charger les variables d'environnement
load_dotenv()


async def init_accounts():
    """
    Initialise les comptes Twitter pour twscrape.
    Créé une base de données locale ~/.twscrape/accounts.db
    """
    print("🚀 Initialisation des comptes Twitter pour twscrape...")

    api = API()  # Base de données locale ~/.twscrape/accounts.db

    # Récupération des credentials depuis .env
    account1_username = os.getenv("TWITTER_ACCOUNT_1_USERNAME")
    account1_password = os.getenv("TWITTER_ACCOUNT_1_PASSWORD")
    account1_email = os.getenv("TWITTER_ACCOUNT_1_EMAIL")

    account2_username = os.getenv("TWITTER_ACCOUNT_2_USERNAME")
    account2_password = os.getenv("TWITTER_ACCOUNT_2_PASSWORD")
    account2_email = os.getenv("TWITTER_ACCOUNT_2_EMAIL")

    # Vérification que les credentials existent
    if not account1_username or not account1_password:
        print("❌ Erreur : Credentials du compte 1 manquants dans .env")
        print(
            "   Ajoutez : TWITTER_ACCOUNT_1_USERNAME, TWITTER_ACCOUNT_1_PASSWORD, TWITTER_ACCOUNT_1_EMAIL"
        )
        return False

    if not account2_username or not account2_password:
        print("❌ Erreur : Credentials du compte 2 manquants dans .env")
        print(
            "   Ajoutez : TWITTER_ACCOUNT_2_USERNAME, TWITTER_ACCOUNT_2_PASSWORD, TWITTER_ACCOUNT_2_EMAIL"
        )
        return False

    try:
        # Ajout du compte 1
        print(f"📝 Ajout du compte 1 : {account1_username}...")
        await api.pool.add_account(
            username=account1_username,
            password=account1_password,
            email=account1_email,
            email_password="",  # Optionnel - pour vérification email automatique
        )
        print("   ✅ Compte 1 ajouté")

        # Ajout du compte 2
        print(f"📝 Ajout du compte 2 : {account2_username}...")
        await api.pool.add_account(
            username=account2_username,
            password=account2_password,
            email=account2_email,
            email_password="",
        )
        print("   ✅ Compte 2 ajouté")

        # Login de tous les comptes
        print("🔑 Connexion des comptes à Twitter...")
        await api.pool.login_all()

        # Affichage des comptes actifs
        accounts_info = await api.pool.accounts_info()
        active_count = len([acc for acc in accounts_info if acc.active])

        print("\n" + "=" * 50)
        print("✅ Comptes Twitter initialisés pour twscrape")
        print(f"   Comptes actifs : {active_count}/{len(accounts_info)}")
        print("=" * 50)

        # Affichage du détail des comptes
        print("\n📊 Détail des comptes :")
        for idx, acc in enumerate(accounts_info, 1):
            status = "🟢 ACTIF" if acc.active else "🔴 INACTIF"
            print(f"   Compte {idx} : @{acc.username} - {status}")

        print(
            "\n💡 Les comptes sont prêts à être utilisés par le worker de vérification."
        )
        print("   Base de données : ~/.twscrape/accounts.db")

        return True

    except Exception as e:
        print(f"\n❌ Erreur lors de l'initialisation : {e}")
        print("\nℹ️ Solutions possibles :")
        print("   1. Vérifiez que les credentials sont corrects dans .env")
        print("   2. Assurez-vous que les comptes ne sont pas locked/suspendus")
        print("   3. Tentez de vous connecter manuellement à Twitter avec ces comptes")
        print("   4. Si problème persiste, utilisez des cookies au lieu de password")
        return False


async def check_accounts():
    """
    Vérifie les comptes existants (si déjà initialisés)
    """
    api = API()

    try:
        accounts_info = await api.pool.accounts_info()

        if not accounts_info:
            print("❌ Aucun compte trouvé dans la base twscrape")
            print("   Exécutez d'abord ce script pour initialiser les comptes.")
            return

        print("📊 Comptes Twitter existants :")
        for idx, acc in enumerate(accounts_info, 1):
            status = "🟢 ACTIF" if acc.active else "🔴 INACTIF"
            print(f"   Compte {idx} : @{acc.username} - {status}")
            print(f"      Limits OK : {acc.limits_remaining}")

    except Exception as e:
        print(f"Erreur : {e}")


if __name__ == "__main__":
    print("=" * 50)
    print("   INITIALISATION TWSCRAPE")
    print("=" * 50 + "\n")

    # Choix : initialiser ou vérifier
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "check":
        # Mode vérification seulement
        asyncio.run(check_accounts())
    else:
        # Mode initialisation
        success = asyncio.run(init_accounts())

        if not success:
            exit(1)
