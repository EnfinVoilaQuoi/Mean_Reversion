"""
Worker de surveillance des badges de vérification Twitter.
Utilise twscrape pour accéder à l'API GraphQL officielle de Twitter.

Fonctionnalités :
- Vérification batch des comptes Twitter des tokens
- Détection de perte de vérification (alerte Discord)
- Support async/await pour performance optimale
- Comportement "human-like" avec délais aléatoires

Usage:
    # Vérification quotidienne (appelée par scheduler)
    from src.scrapers.twitter_verification_worker import daily_verification_check
    daily_verification_check()

    # Vérification manuelle d'un token
    from src.scrapers.twitter_verification_worker import check_verification_batch
    from src.database.models import Token
    token = Token.get(Token.symbol == 'BTC')
    check_verification_batch([token])
"""

import asyncio
import logging
import random
from datetime import datetime

try:
    from twscrape import API  # type: ignore[import-untyped]
except ImportError as err:
    raise ImportError("twscrape n'est pas installé. Exécutez: pip install twscrape") from err

from src.config import VERIFICATION_CONFIG
from src.database.models import Token

logger = logging.getLogger(__name__)

# Instance globale de l'API twscrape (réutilise le pool de comptes)
_api_instance = None


def get_twscrape_api() -> API:
    """
    Récupère l'instance singleton de l'API twscrape.

    Returns:
        API: Instance de l'API twscrape avec pool de comptes configuré
    """
    global _api_instance
    if _api_instance is None:
        _api_instance = API()  # Charge les comptes depuis ~/.twscrape/accounts.db
        logger.info("Instance twscrape API créée")
    return _api_instance


async def check_single_user_verification(
    api: API, username: str
) -> dict[str, bool | None]:
    """
    Vérifie le statut de vérification d'un utilisateur Twitter.

    Args:
        api: Instance de l'API twscrape
        username: Username Twitter (sans @)

    Returns:
        Dict avec 'verified' (legacy), 'blue' (Twitter Blue), 'blue_type'
        Retourne None pour chaque champ en cas d'erreur
    """
    try:
        user = await api.user_by_login(username)

        return {
            "verified": user.verified,  # Legacy verification (pre-2023)
            "blue": user.blue,  # Twitter Blue / X Premium
            "blue_type": user.blueType,  # Type de badge: "Blue", "Business", "Government"
        }

    except Exception as e:
        logger.error(f"Erreur lors de la vérification de @{username}: {e}")
        return {"verified": None, "blue": None, "blue_type": None}


async def check_verification_batch_async(tokens: list[Token]) -> dict[str, int]:
    """
    Vérifie le statut de vérification pour un batch de tokens (async).

    Cette fonction utilise un seul client twscrape pour tous les tokens,
    avec rotation automatique des comptes par twscrape pour le rate limiting.
    Comportement "human-like" avec délais aléatoires.

    Args:
        tokens: Liste des tokens à vérifier

    Returns:
        Dict avec les statistiques de la vérification:
            - checked: Nombre de tokens vérifiés
            - still_verified: Nombre toujours vérifiés
            - verification_lost: Nombre ayant perdu la vérification
            - new_verified: Nombre ayant obtenu la vérification
            - errors: Nombre d'erreurs rencontrées
    """
    stats = {
        "checked": 0,
        "still_verified": 0,
        "verification_lost": 0,
        "new_verified": 0,
        "errors": 0,
    }

    api = get_twscrape_api()

    for token in tokens:
        if not token.twitter_handle:
            logger.warning(f"{token.cashtag} n'a pas de twitter_handle, skip")
            continue

        logger.info(f"Vérification de {token.cashtag} (@{token.twitter_handle})...")

        # Récupération du statut de vérification
        verification_status = await check_single_user_verification(
            api, token.twitter_handle
        )

        if (
            verification_status["verified"] is None
            and verification_status["blue"] is None
        ):
            stats["errors"] += 1
            logger.error(
                f"Impossible de récupérer le statut de @{token.twitter_handle}"
            )
            continue

        # Détection de changement de vérification
        was_verified = token.twitter_verified is True or token.twitter_blue is True
        is_verified = (
            verification_status["verified"] is True
            or verification_status["blue"] is True
        )

        if was_verified and not is_verified:
            # 🚨 PERTE DE VÉRIFICATION DÉTECTÉE
            logger.warning(f"⚠️ {token.cashtag} a PERDU sa vérification Twitter!")
            token.twitter_verification_lost_at = datetime.now()
            stats["verification_lost"] += 1

            # Envoyer alerte Discord
            send_verification_loss_alert_sync(token)

        elif not was_verified and is_verified:
            # 🎉 Nouvelle vérification obtenue
            logger.info(f"✅ {token.cashtag} a OBTENU une vérification Twitter!")
            stats["new_verified"] += 1

        elif is_verified:
            # ✅ Toujours vérifié
            stats["still_verified"] += 1
            logger.debug(f"{token.cashtag} est toujours vérifié")

        # Mise à jour du statut dans la base de données
        token.twitter_verified = verification_status["verified"]
        token.twitter_blue = verification_status["blue"]
        token.twitter_blue_type = verification_status["blue_type"]
        token.twitter_verification_checked_at = datetime.now()
        token.save()

        stats["checked"] += 1

        # Délai aléatoire "human-like" pour éviter détection bot
        if VERIFICATION_CONFIG.get("HUMAN_LIKE_BEHAVIOR", True):
            base_delay = VERIFICATION_CONFIG.get("RATE_LIMIT_DELAY", 1.5)
            jitter = random.uniform(0.5, 2.0)  # Variation aléatoire entre 50% et 200%
            delay = base_delay * jitter
            logger.debug(f"Délai : {delay:.2f}s")
            await asyncio.sleep(delay)
        else:
            # Délai fixe si comportement human-like désactivé
            await asyncio.sleep(VERIFICATION_CONFIG.get("RATE_LIMIT_DELAY", 1.5))

    return stats


def check_verification_batch(tokens: list[Token]) -> dict[str, int]:
    """
    Wrapper synchrone pour la fonction async (utilisé par le scheduler).

    Args:
        tokens: Liste des tokens à vérifier

    Returns:
        Dict avec les statistiques de la vérification
    """
    return asyncio.run(check_verification_batch_async(tokens))


def get_tokens_to_check() -> list[Token]:
    """
    Récupère les tokens à vérifier selon les critères définis.

    Critères :
    - rank_status = 'MONITORING' (dans le top 1000 actif)
    - twitter_verified = True OU twitter_blue = True (déjà vérifiés)
    - twitter_handle existe (non null)

    Returns:
        Liste des tokens à vérifier
    """
    try:
        query = Token.select().where(
            (Token.rank_status == "MONITORING")
            & (Token.twitter_handle.is_null(False))
            & (Token.twitter_verified | Token.twitter_blue)
        )

        tokens = list(query)
        logger.info(f"Tokens à vérifier : {len(tokens)}")

        return tokens

    except Exception as e:
        logger.error(f"Erreur lors de la récupération des tokens : {e}")
        return []


def send_verification_loss_alert_sync(token: Token):
    """
    Envoie une alerte Discord lorsqu'un token perd sa vérification.

    Cette fonction est synchrone et utilise le bot Discord global.

    Args:
        token: Le token ayant perdu sa vérification
    """
    try:
        import discord
        from src.config import DISCORD_CONFIG
        from src.discord.discord_bot import bot

        alerts_channel_id = DISCORD_CONFIG.get("ALERT_CHANNEL_ID")

        if not bot or not alerts_channel_id:
            logger.warning("Bot Discord non configuré, alerte non envoyée")
            return

        # Créer l'embed d'alerte
        embed = discord.Embed(
            title=f"⚠️ Vérification Twitter Perdue - {token.cashtag}",
            description=f"Le compte Twitter **@{token.twitter_handle}** a perdu son badge de vérification.",
            color=discord.Color.orange(),
            timestamp=datetime.now(),
        )

        # Informations du token
        embed.add_field(name="Token", value=token.symbol, inline=True)
        embed.add_field(
            name="Rank", value=f"#{token.rank}" if token.rank else "N/A", inline=True
        )
        embed.add_field(
            name="Source", value=token.primary_price_source or "N/A", inline=True
        )

        # Avertissement
        embed.add_field(
            name="⚠️ Avertissement",
            value="La perte de vérification Twitter est souvent un signal de risque :\n"
            "• Compte compromis\n"
            "• Changement d'équipe projet\n"
            "• Abandon du projet\n"
            "• Impersonation détectée",
            inline=False,
        )

        # Lien Twitter
        if token.twitter_link:
            embed.add_field(name="🐦 Twitter", value=token.twitter_link, inline=False)

        # Envoyer de manière asynchrone via le bot
        channel_id = (
            int(alerts_channel_id)
            if isinstance(alerts_channel_id, (int, str))
            else alerts_channel_id
        )
        channel = bot.get_channel(channel_id)
        if channel and hasattr(channel, "send"):
            # Utiliser create_task pour l'envoyer de manière asynchrone
            asyncio.create_task(channel.send(embed=embed))
            logger.info(f"Alerte Discord envoyée pour {token.cashtag}")
        else:
            logger.warning(
                f"Channel Discord {alerts_channel_id} non trouvé ou type invalide"
            )

    except Exception as e:
        logger.error(f"Erreur lors de l'envoi de l'alerte Discord : {e}", exc_info=True)


def daily_verification_check():
    """
    Job quotidien de vérification des badges Twitter.

    Cette fonction est appelée par le scheduler APScheduler.
    Elle vérifie tous les tokens ayant déjà une vérification.

    Returns:
        Dict avec les statistiques de la vérification
    """
    logger.info("🔍 Démarrage de la vérification quotidienne des badges Twitter...")

    try:
        # Récupérer les tokens à vérifier
        tokens = get_tokens_to_check()

        if not tokens:
            logger.info("Aucun token à vérifier (aucun token vérifié trouvé)")
            return {"checked": 0, "message": "Aucun token à vérifier"}

        # Vérifier le batch
        logger.info(f"Vérification de {len(tokens)} tokens...")
        stats = check_verification_batch(tokens)

        # Logging des résultats
        logger.info(f"✅ Vérification terminée : {stats}")

        # Si des pertes de vérification sont détectées, log warning
        if stats["verification_lost"] > 0:
            logger.warning(
                f"⚠️ {stats['verification_lost']} token(s) ont perdu leur vérification!"
            )

        if stats["new_verified"] > 0:
            logger.info(
                f"🎉 {stats['new_verified']} token(s) ont obtenu une vérification!"
            )

        return stats

    except Exception as e:
        logger.error(
            f"❌ Erreur lors de la vérification quotidienne : {e}", exc_info=True
        )
        return {"error": str(e), "checked": 0}


# Test unitaire si exécuté directement
if __name__ == "__main__":
    # Configuration du logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    print("=" * 60)
    print("   TEST DU WORKER DE VÉRIFICATION TWITTER")
    print("=" * 60)

    print("\n🔍 Récupération des tokens à vérifier...")
    tokens = get_tokens_to_check()

    if not tokens:
        print("❌ Aucun token trouvé avec vérification")
        print("\n💡 Pour tester, ajoutez manuellement une vérification à un token:")
        print("   UPDATE tokens SET twitter_verified = 1 WHERE symbol = 'BTC';")
        exit(1)

    print(f"✅ {len(tokens)} token(s) trouvé(s)")

    # Test avec les 3 premiers tokens
    test_tokens = tokens[: min(3, len(tokens))]

    print(f"\n🧪 Test de vérification sur {len(test_tokens)} token(s):")
    for token in test_tokens:
        print(f"   • {token.cashtag} (@{token.twitter_handle})")

    print("\n🚀 Lancement de la vérification...")
    stats = check_verification_batch(test_tokens)

    print("\n📊 Résultats :")
    print(f"   ✅ Vérifiés : {stats['checked']}")
    print(f"   🔵 Toujours vérifiés : {stats['still_verified']}")
    print(f"   ⚠️ Pertes : {stats['verification_lost']}")
    print(f"   🆕 Nouvelles vérifications : {stats['new_verified']}")
    print(f"   ❌ Erreurs : {stats['errors']}")

    print("\n" + "=" * 60)
    print("✅ Test terminé")
    print("=" * 60)
