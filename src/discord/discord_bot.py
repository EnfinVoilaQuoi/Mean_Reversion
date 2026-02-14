"""
Bot Discord - Orchestrateur principal des commandes interactives
Utilise le pattern Cogs pour une meilleure modularité
"""

import asyncio
import logging

from discord.ext import commands

import discord
from discord import app_commands

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION DU BOT
# ============================================================================

intents = discord.Intents.default()
intents.message_content = True


class MyBot(commands.Bot):
    async def setup_hook(self):
        # Les cogs seront chargés dans load_cogs() appelé depuis le setup_hook global
        # Ne pas faire sync() ici, le faire après le chargement des cogs
        pass


bot = MyBot(command_prefix=commands.when_mentioned_or(""), intents=intents)

# Références globales pour l'injection de dépendances
_scheduler = None
_update_monitoring_job_func = None
_add_token_and_start_backfill_func = None


def set_scheduler(scheduler, update_job_func, add_token_backfill_func):
    """
    Injecte les références depuis main.py.
    À appeler avant de démarrer le bot.

    Args:
        scheduler: Instance APScheduler
        update_job_func: Fonction update_monitoring_job
        add_token_backfill_func: Fonction add_token_and_start_backfill
    """
    global _scheduler, _update_monitoring_job_func, _add_token_and_start_backfill_func
    _scheduler = scheduler
    _update_monitoring_job_func = update_job_func
    _add_token_and_start_backfill_func = add_token_backfill_func
    logger.info("✅ Scheduler et fonctions injectées dans discord_bot.py")


# ============================================================================
# ÉVÉNEMENTS DU BOT
# ============================================================================


@bot.event
async def on_ready():
    """Se déclenche lorsque le bot est prêt."""
    logger.info(f"✅ Bot Discord connecté en tant que {bot.user}")
    print(f"🤖 Bot {bot.user.name if bot.user else 'Unknown'} est prêt sur Discord!")
    print("   Préfixe des commandes : /")
    print(
        "   Commandes disponibles : /add_token, /add_pair, /set_mode, /list, /status, /screen, /resolve, /filter"
    )
    print(
        "                          /scheduler, /check_fgi, /check_total_vol, /zscore, /reset_token, /exclude_token,"
    )
    print("                          /backfill, /backfill_manual, /check_verification,")
    print(
        "                          /twscrape_status, /twscrape_reset, /twscrape_test,"
    )
    print("                          /gap_complete, /gap_list, /gap_delete,")
    print("                          /aggregate, /aggregate_all, /pipeline_status,")
    print("                          /webhook list, /webhook create, /webhook stop, /webhook del")
    print(
        "\n⏳ ATTENDEZ 20-30 secondes avant d'utiliser les commandes (synchronisation Discord)"
    )


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    """Gère les erreurs globales des commandes Discord."""
    if isinstance(
        error, RuntimeError
    ) and "cannot schedule new futures after shutdown" in str(error):
        logger.warning("🛑 Commande Discord interrompue (shutdown en cours)")
        return

    if isinstance(error, (asyncio.CancelledError, ConnectionError)):
        logger.warning("🛑 Opération annulée (shutdown en cours)")
        return

    logger.error(f"❌ Erreur commande Discord: {error}", exc_info=True)


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
):
    """Gère les erreurs des slash commands."""
    if isinstance(
        error, RuntimeError
    ) and "cannot schedule new futures after shutdown" in str(error):
        logger.warning("🛑 Slash command interrompue (shutdown en cours)")
        return

    if isinstance(error, (asyncio.CancelledError, ConnectionError)):
        logger.warning("🛑 Opération annulée (shutdown en cours)")
        return

    # Récupérer le nom de la commande si possible
    command_name = getattr(interaction.command, "name", "unknown")
    logger.error(
        f"❌ Erreur slash command '{command_name}' (Interaction {interaction.id}): {error}",
        exc_info=True,
    )

    # Essayer d'envoyer un message d'erreur
    try:
        # Si l'interaction a déjà été defer, utiliser followup
        if interaction.response.is_done():
            logger.debug(
                f"Envoi message d'erreur via followup pour '{command_name}' (interaction {interaction.id})"
            )
            await interaction.followup.send(
                f"❌ Une erreur est survenue : {str(error)}", ephemeral=True
            )
        else:
            logger.debug(
                f"Envoi message d'erreur via response pour '{command_name}' (interaction {interaction.id})"
            )
            await interaction.response.send_message(
                f"❌ Une erreur est survenue : {str(error)}", ephemeral=True
            )
    except (
        discord.errors.InteractionResponded,
        discord.errors.NotFound,
        discord.errors.HTTPException,
    ) as e:
        # Interaction déjà répondue, expirée, ou autre erreur Discord - ne rien faire
        logger.debug(
            f"Impossible d'envoyer le message d'erreur pour '{command_name}': {e}"
        )


# ============================================================================
# CHARGEMENT DES COGS
# ============================================================================


async def load_cogs():
    """Charge tous les cogs avec leurs dépendances"""
    from .commands.admin_commands import AdminCommands
    from .commands.aggregator_commands import AggregatorCommands
    from .commands.backfill_commands import BackfillCommands
    from .commands.gap_commands import GapCommands
    from .commands.info_commands import InfoCommands
    from .commands.rule_commands import RuleCommands
    from .commands.token_commands import TokenCommands
    from .commands.twscrape_commands import TwscrapeCommands
    # from .commands.verification_commands import VerificationCommands  # ARCHIVÉ

    # Instancier les cogs avec leurs dépendances
    cogs = [
        InfoCommands(bot),
        # VerificationCommands(bot),  # ARCHIVÉ
        BackfillCommands(bot),
        AdminCommands(bot, _scheduler),
        TokenCommands(
            bot,
            _scheduler,
            _update_monitoring_job_func,
            _add_token_and_start_backfill_func,
        ),
        TwscrapeCommands(bot),
        GapCommands(bot),
        AggregatorCommands(bot),
        RuleCommands(bot),  # Gestion des règles TwitterAPI.io
    ]

    for cog in cogs:
        try:
            await bot.add_cog(cog)
            logger.info(f"✅ Cog chargé: {cog.__class__.__name__}")
        except Exception as e:
            logger.error(
                f"❌ Erreur chargement cog {cog.__class__.__name__}: {e}", exc_info=True
            )


@bot.event
async def setup_hook():
    """Exécuté avant on_ready"""
    await load_cogs()
    # Synchroniser les slash commands APRÈS le chargement des cogs
    await bot.tree.sync()
    logger.info("✅ Slash commands synchronisées avec tous les cogs")


# ============================================================================
# FONCTION DE DÉMARRAGE EXPORTÉE
# ============================================================================


def run_discord_bot(token: str):
    """
    Démarre le bot Discord (fonction bloquante).

    Args:
        token (str): Token Discord du bot
    """
    try:
        logger.info("🚀 Démarrage du bot Discord...")
        bot.run(token)
    except Exception as e:
        logger.error(f"❌ Erreur fatale du bot Discord: {e}", exc_info=True)
        raise
