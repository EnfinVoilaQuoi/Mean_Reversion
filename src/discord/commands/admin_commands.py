"""Admin Commands - resolve, scheduler"""

import asyncio
import logging
from datetime import datetime

from discord.ext import commands

import discord
from discord import app_commands

from ...scrapers.dexscreener_worker import (
    resolve_incomplete_tokens,
    resolve_single_token,
)
from ...scrapers.token_screener import is_shutdown_requested

logger = logging.getLogger(__name__)


class AdminCommands(commands.Cog):
    """Commandes d'administration (resolve, scheduler)"""

    def __init__(self, bot: commands.Bot, scheduler=None):
        self.bot = bot
        self.scheduler = scheduler

    @app_commands.command(name="resolve", description="Force les Tokens INCOMPLETE")
    @app_commands.describe(
        symbol="Symbole du token à résoudre (optionnel - si vide: tous les INCOMPLETE)"
    )
    async def resolve_incomplete_cmd(
        self, interaction: discord.Interaction, symbol: str | None = None
    ):
        """Lance la résolution manuelle des tokens INCOMPLETE."""
        await interaction.response.defer()

        try:
            if symbol:
                # Résolution ciblée d'un token unique
                await interaction.followup.send(f"🔍 Résolution du token `{symbol}`...")
                result = await asyncio.to_thread(resolve_single_token, symbol)

                if is_shutdown_requested():
                    logger.warning(
                        "🛑 Résolution interrompue - Pas d'envoi de message final (shutdown en cours)"
                    )
                    return

                if result and result["success"]:
                    embed = discord.Embed(
                        title=f"✅ Token `{result['token']}` résolu !",
                        color=discord.Color.green(),
                    )
                    embed.add_field(
                        name="🔗 Source", value=result["source"], inline=True
                    )
                    embed.add_field(
                        name="⛓️ Chaîne", value=result["chain_id"], inline=True
                    )
                    embed.add_field(
                        name="📋 Contrat", value=f"`{result['contract']}`", inline=False
                    )
                    embed.add_field(
                        name="📊 Pair Address",
                        value=f"`{result['pair_address']}`",
                        inline=False,
                    )
                    embed.add_field(
                        name="💰 Volume 24h",
                        value=f"Primary: ${result['primary_volume_24h']:,.0f}\nTotal: ${result['total_volume_24h']:,.0f}",
                        inline=False,
                    )
                    embed.add_field(
                        name="📈 Statut",
                        value=f"{result['old_status']} → **{result['new_status']}**",
                        inline=True,
                    )
                    embed.set_footer(
                        text="💡 Utilisez /set_mode symbol:TURBO mode:CROISIERE pour lancer le monitoring"
                    )

                    await interaction.followup.send(embed=embed)
                else:
                    error_msg = (
                        result.get("error", "Unknown error")
                        if result
                        else "Unknown error"
                    )
                    await interaction.followup.send(
                        f"❌ Impossible de résoudre `{symbol}`\n**Erreur**: {error_msg}"
                    )

            else:
                # Résolution batch de tous les INCOMPLETE
                await interaction.followup.send(
                    "🔍 Lancement de la résolution des tokens INCOMPLETE..."
                )

                stats = await asyncio.to_thread(resolve_incomplete_tokens)

                if is_shutdown_requested():
                    logger.warning(
                        "🛑 Résolution interrompue - Pas d'envoi de message final (shutdown en cours)"
                    )
                    return

                embed = discord.Embed(
                    title="✅ Résolution des tokens INCOMPLETE terminée !",
                    color=discord.Color.blue(),
                )
                embed.add_field(
                    name="✅ Tokens résolus", value=stats["resolved"], inline=True
                )
                embed.add_field(name="❌ Échecs", value=stats["failed"], inline=True)
                embed.add_field(name="⏭️ Ignorés", value=stats["skipped"], inline=True)

                if stats["resolved"] > 0:
                    embed.add_field(
                        name="📝 Prochaine étape",
                        value=f"{stats['resolved']} token(s) passés à LISTED → Prêts pour activation manuelle",
                        inline=False,
                    )

                embed.set_footer(
                    text="💡 Utilisez /set_mode symbol:TOKEN mode:CROISIERE pour lancer le backfill et monitoring 1h"
                )

                await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"❌ Erreur resolve_incomplete_cmd: {e}", exc_info=True)
            if not is_shutdown_requested():
                await interaction.followup.send(
                    f"❌ Une erreur est survenue lors de la résolution : {str(e)}"
                )

    @app_commands.command(
        name="scheduler", description="Gère les tâches planifiées du scheduler"
    )
    @app_commands.describe(
        action="Action à effectuer (vide = liste, run = exécuter, remove = supprimer)",
        job_id="ID du job (requis pour run/remove)",
    )
    async def scheduler_status(
        self,
        interaction: discord.Interaction,
        action: str | None = None,
        job_id: str | None = None,
    ):
        """Affiche les tâches planifiées ou permet de les gérer."""
        try:
            if not self.scheduler:
                await interaction.response.send_message(
                    "❌ Le scheduler n'est pas disponible."
                )
                return

            # Action: Lister les jobs
            if action is None:
                jobs = self.scheduler.get_jobs()
                if not jobs:
                    await interaction.response.send_message(
                        "📭 Aucune tâche planifiée pour le moment."
                    )
                    return

                display_limit = 10
                total_jobs = len(jobs)

                embed = discord.Embed(
                    title=f"⏰ Tâches Planifiées ({total_jobs} total)",
                    color=discord.Color.green(),
                )

                for i, job in enumerate(jobs[:display_limit], 1):
                    next_run = (
                        job.next_run_time.strftime("%H:%M:%S")
                        if job.next_run_time
                        else "N/A"
                    )
                    job_name = job.name[:40] + "..." if len(job.name) > 40 else job.name

                    embed.add_field(
                        name=f"{i}. {job_name}",
                        value=f"ID: `{job.id}`\nProchaine: {next_run}",
                        inline=True,
                    )

                if total_jobs > display_limit:
                    embed.set_footer(
                        text=f"+ {total_jobs - display_limit} autre(s) tâche(s) non affichée(s)"
                    )

                await interaction.response.send_message(embed=embed)

            # Action: Exécuter un job
            elif action.lower() == "run":
                if not job_id:
                    await interaction.response.send_message(
                        "❌ Usage: `/scheduler run <job_id>`"
                    )
                    return

                job = self.scheduler.get_job(job_id)
                if not job:
                    await interaction.response.send_message(
                        f"❌ Job `{job_id}` non trouvé."
                    )
                    return

                await interaction.response.send_message(
                    f"⏳ Exécution forcée de `{job.name}`..."
                )
                job.modify(next_run_time=datetime.now())
                await interaction.followup.send(
                    f"✅ Job `{job.name}` planifié pour exécution immédiate !"
                )

            # Action: Supprimer un job
            elif action.lower() == "remove":
                if not job_id:
                    await interaction.response.send_message(
                        "❌ Usage: `/scheduler remove <job_id>`"
                    )
                    return

                job = self.scheduler.get_job(job_id)
                if not job:
                    await interaction.response.send_message(
                        f"❌ Job `{job_id}` non trouvé."
                    )
                    return

                job_name = job.name
                self.scheduler.remove_job(job_id)
                await interaction.response.send_message(
                    f"✅ Job `{job_name}` supprimé avec succès !"
                )

            else:
                await interaction.response.send_message(
                    "❌ Action invalide. Utilisez: `/scheduler`, `/scheduler run <job_id>`, ou `/scheduler remove <job_id>`"
                )

        except Exception as e:
            logger.error(f"❌ Erreur scheduler_status: {e}", exc_info=True)
            await interaction.response.send_message(
                f"❌ Une erreur est survenue : {str(e)}"
            )


async def setup(bot: commands.Bot):
    """Setup cog with bot"""
    await bot.add_cog(AdminCommands(bot))
