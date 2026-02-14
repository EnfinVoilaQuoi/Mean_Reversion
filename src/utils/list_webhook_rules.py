"""
Script indépendant pour lister toutes les règles TwitterAPI.io
Usage: python list_webhook_rules.py
"""

import asyncio
import os

import httpx
from dotenv import load_dotenv

# Charger les variables d'environnement
load_dotenv()

TWITTERAPI_BASE_URL = "https://api.twitterapi.io"
TWITTERAPI_KEY = os.getenv("TWITTER_IO_API")


async def list_rules():
    """Liste toutes les règles actives"""
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": TWITTERAPI_KEY,
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{TWITTERAPI_BASE_URL}/oapi/tweet_filter/get_rules",
            headers=headers,
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        # L'API retourne {"status": "success", "rules": [...]}
        return data.get("rules", [])


async def main():
    """Point d'entrée principal"""
    if not TWITTERAPI_KEY:
        print("[ERREUR] TWITTER_IO_API non configuree dans .env")
        return

    try:
        print("Recuperation des regles TwitterAPI.io...\n")
        rules = await list_rules()

        if not rules:
            print("Aucune regle configuree")
            return

        print(f"{len(rules)} regle(s) trouvee(s)\n")
        print("=" * 80)

        for i, rule in enumerate(rules, 1):
            status = "[ACTIVE]" if rule.get("is_effect") == 1 else "[INACTIVE]"
            webhook_url = rule.get("webhook_url", "Non configure")

            print(f"\n#{i} - {rule.get('tag')} {status}")
            print(f"   Rule ID    : {rule.get('rule_id')}")
            print(f"   Value      : {rule.get('value')}")
            print(f"   Interval   : {rule.get('interval_seconds')}s")
            print(f"   Webhook URL: {webhook_url}")

        print("\n" + "=" * 80)

    except httpx.HTTPStatusError as e:
        print(f"[ERREUR] Erreur API : {e.response.status_code}")
        print(f"   Details : {e.response.text}")
    except Exception as e:
        print(f"[ERREUR] Erreur : {str(e)}")


if __name__ == "__main__":
    asyncio.run(main())
