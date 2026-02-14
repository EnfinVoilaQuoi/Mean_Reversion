"""
Script indépendant pour supprimer une règle TwitterAPI.io par tag
Usage: python delete_webhook_rule.py JELLYJELLY
"""

import asyncio
import os
import sys

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


async def delete_rule(rule_id: str, tag: str, value: str):
    """Supprime une règle par son ID"""
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": TWITTERAPI_KEY,
    }

    delete_data = {
        "rule_id": rule_id,
        "tag": tag,
        "value": value,
    }

    async with httpx.AsyncClient() as client:
        response = await client.request(
            "DELETE",
            f"{TWITTERAPI_BASE_URL}/oapi/tweet_filter/delete_rule",
            headers=headers,
            json=delete_data,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


async def delete_rule_by_tag(tag_to_delete: str):
    """Trouve et supprime une règle par son tag"""
    print(f"Recherche de la regle avec tag: {tag_to_delete}")

    # Lister toutes les règles
    try:
        rules = await list_rules()
        print(f"{len(rules)} regle(s) trouvee(s)")

        # Trouver la règle avec le tag correspondant
        rule_to_delete = None
        for rule in rules:
            if rule.get("tag") == tag_to_delete:
                rule_to_delete = rule
                break

        if not rule_to_delete:
            print(f"[ERREUR] Aucune regle trouvee avec le tag '{tag_to_delete}'")
            print("\nRegles existantes :")
            for rule in rules:
                status = "[ACTIVE]" if rule.get("is_effect") == 1 else "[INACTIVE]"
                print(
                    f"   - {rule.get('tag')} (ID: {rule.get('rule_id')[:8]}...) {status}"
                )
            return False

        # Afficher les détails de la règle
        print(f"\nRegle trouvee !")
        print(f"   Tag       : {rule_to_delete.get('tag')}")
        print(f"   Rule ID   : {rule_to_delete.get('rule_id')}")
        print(f"   Value     : {rule_to_delete.get('value')}")
        print(f"   Interval  : {rule_to_delete.get('interval_seconds')}s")
        print(
            f"   Status    : {'[ACTIVE]' if rule_to_delete.get('is_effect') == 1 else '[INACTIVE]'}"
        )

        # Demander confirmation
        print(f"\n[ATTENTION] Etes-vous sur de vouloir supprimer cette regle ? (y/N) ", end="")
        confirmation = input().strip().lower()

        if confirmation != "y":
            print("[ANNULE] Suppression annulee")
            return False

        # Supprimer la règle
        print(f"\nSuppression en cours...")
        result = await delete_rule(
            rule_id=rule_to_delete.get("rule_id"),
            tag=rule_to_delete.get("tag"),
            value=rule_to_delete.get("value"),
        )

        print(f"[OK] Regle '{tag_to_delete}' supprimee avec succes !")
        print(f"   Reponse API : {result}")
        return True

    except httpx.HTTPStatusError as e:
        print(f"[ERREUR] Erreur API : {e.response.status_code}")
        print(f"   Details : {e.response.text}")
        return False
    except Exception as e:
        print(f"[ERREUR] Erreur : {str(e)}")
        return False


async def main():
    """Point d'entrée principal"""
    if not TWITTERAPI_KEY:
        print("[ERREUR] TWITTER_IO_API non configuree dans .env")
        return

    # Vérifier les arguments
    if len(sys.argv) < 2:
        print("Usage: python delete_webhook_rule.py <TAG>")
        print("\nExemples:")
        print("  python delete_webhook_rule.py JELLYJELLY")
        print("  python delete_webhook_rule.py monitoring_PEPE")
        return

    tag_to_delete = sys.argv[1]
    await delete_rule_by_tag(tag_to_delete)


if __name__ == "__main__":
    asyncio.run(main())
