"""Admin Commands Archived - screen, filter"""

import asyncio
import logging
from datetime import datetime

from discord.ext import commands

import discord
from discord import app_commands

from ....scrapers.dexscreener_worker import (
    resolve_incomplete_tokens,
    resolve_single_token,
)
from ....scrapers.token_screener import (
    fetch_top_x_and_screen,
    filter_tokens_by_criteria,
    is_shutdown_requested,
)

logger = logging.getLogger(__name__)


class AdminCommandsArchived(commands.Cog):
    """Commandes d'administration archivées (screening, filtering)"""

    def __init__(self, bot: commands.Bot, scheduler=None):
        self.bot = bot
        self.scheduler = scheduler

    @app_commands.command(
        name="screen", description="Lance un Screen des Tokens par MarketCap"
    )
    @app_commands.describe(top_limit="Nombre de tokens à screen (défaut: 1000)")
    async def screen_tokens(
        self, interaction: discord.Interaction, top_limit: int | None = None
    ):
        """Lance le screening du Top X tokens par MarketCap."""
        if top_limit is None:
            from ....config import SCREENING_CONFIG

            top_limit = SCREENING_CONFIG.get("TOP_LIMIT", 1000)

        if top_limit > 2000:
            await interaction.response.send_message("❌ Limite maximale : 2000 tokens")
            return

        await interaction.response.send_message(
            f"🚀 Lancement du screening du Top {top_limit} tokens..."
        )
        await interaction.followup.send(
            "⏳ Cette opération peut prendre plusieurs minutes..."
        )

        try:
            stats = await asyncio.to_thread(fetch_top_x_and_screen, top_limit=top_limit)

            if is_shutdown_requested():
                logger.warning(
                    "🛑 Screening interrompu - Pas d'envoi de message final (shutdown en cours)"
                )
                return

            embed = discord.Embed(
                title=f"✅ Screening du Top {top_limit} terminé !",
                color=discord.Color.green(),
            )
            embed.add_field(name="📊 Tokens créés", value=stats["created"], inline=True)
            embed.add_field(
                name="🔄 Tokens mis à jour", value=stats["updated"], inline=True
            )
            embed.add_field(
                name="📉 Tokens déclassés", value=stats.get("demoted", 0), inline=True
            )
            embed.add_field(
                name="🔗 Dérivés consolidés",
                value=stats.get("derivatives_merged", 0),
                inline=True,
            )
            embed.add_field(
                name="🚫 Stablecoins exclus",
                value=stats.get("stablecoins_excluded", 0),
                inline=True,
            )
            embed.add_field(
                name="📋 Tokens INCOMPLETE",
                value=stats.get("incomplete", 0),
                inline=True,
            )
            embed.add_field(
                name="⏭️ Autres ignorés",
                value=stats["skipped"] - stats.get("stablecoins_excluded", 0),
                inline=True,
            )
            embed.add_field(name="❌ Erreurs", value=stats["errors"], inline=True)

            total_in_top = stats["created"] + stats["updated"]
            total_demoted = stats.get("demoted", 0)
            embed.add_field(
                name="📈 Résultat",
                value=f"**{total_in_top}** tokens actifs dans le Top {top_limit}\n**{total_demoted}** tokens sortis du Top",
                inline=False,
            )

            embed.set_footer(
                text="💡 Dérivés (WBTC→BTC, WETH→ETH) consolidés automatiquement | Stablecoins exclus"
            )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"❌ Erreur screen_tokens: {e}", exc_info=True)
            if not is_shutdown_requested():
                await interaction.followup.send(
                    f"❌ Une erreur est survenue lors du screening : {str(e)}"
                )

    @app_commands.command(name="filter", description="Filtre les tokens par critères")
    @app_commands.describe(
        rank="Rang MarketCap (format: 1-500 ou 100)",
        exchange="Plateforme(s) séparées par virgule (ex: MEXC,PancakeSwap)",
        chain="Chaîne(s) séparées par virgule (ex: ethereum,bsc)",
        status="Mode de surveillance (REGULAR, AGGRESSIVE, CROISIERE, PENDING, LISTED, INCOMPLETE)",
        rank_status="Statut classement (MONITORING, OUT_OF_RANK, IGNORED)",
    )
    async def filter_tokens_cmd(
        self,
        interaction: discord.Interaction,
        rank: str | None = None,
        exchange: str | None = None,
        chain: str | None = None,
        status: str | None = None,
        rank_status: str | None = None,
    ):
        """Filtre les tokens en base de données selon des critères."""
        if not any([rank, exchange, chain, status, rank_status]):
            await interaction.response.send_message(
                "❌ Vous devez spécifier au moins un critère de filtrage.\n"
                "Exemples: `/filter rank:1-500` ou `/filter exchange:MEXC`"
            )
            return

        await interaction.response.defer()

        try:
            min_rank = None
            max_rank = None
            exchanges = None
            chains = None

            if rank:
                if "-" in rank:
                    min_rank, max_rank = map(int, rank.split("-"))
                else:
                    min_rank = int(rank)
                    max_rank = int(rank)

            if exchange:
                exchanges = [e.strip() for e in exchange.split(",")]

            if chain:
                chains = [c.strip() for c in chain.split(",")]

            if status:
                status = status.upper()

            if rank_status:
                rank_status = rank_status.upper()

            tokens = await asyncio.to_thread(
                filter_tokens_by_criteria,
                min_rank=min_rank,
                max_rank=max_rank,
                exchanges=exchanges,
                chains=chains,
                status=status,
                rank_status=rank_status,
            )

            if not tokens:
                await interaction.followup.send(
                    "📭 Aucun token ne correspond aux critères."
                )
                return

            display_limit = 20
            total_count = len(tokens)

            embed = discord.Embed(
                title=f"🔍 Tokens filtrés ({total_count} résultats)",
                color=discord.Color.blue(),
            )

            criteria_parts = []
            if min_rank or max_rank:
                criteria_parts.append(f"Rang: {min_rank or 1}-{max_rank or '∞'}")
            if exchanges:
                criteria_parts.append(f"Plateformes: {', '.join(exchanges)}")
            if chains:
                criteria_parts.append(f"Chaînes: {', '.join(chains)}")
            if status:
                criteria_parts.append(f"Mode: {status}")
            if rank_status:
                criteria_parts.append(f"Classement: {rank_status}")

            embed.add_field(
                name="📋 Critères",
                value=" | ".join(criteria_parts) if criteria_parts else "Aucun",
                inline=False,
            )

            token_list = []
            for i, token in enumerate(tokens[:display_limit], 1):
                token_info = f"{i}. **{token.cashtag}** (#{token.rank or 'N/A'}) - {token.primary_price_source or 'N/A'}"
                token_list.append(token_info)

            embed.add_field(
                name=f"📊 Tokens (Top {min(display_limit, total_count)})",
                value="\n".join(token_list),
                inline=False,
            )

            if total_count > display_limit:
                embed.set_footer(
                    text=f"+ {total_count - display_limit} autres tokens non affichés"
                )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"❌ Erreur filter_tokens_cmd: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Une erreur est survenue : {str(e)}")


async def setup(bot: commands.Bot):
    """Setup cog with bot"""
    await bot.add_cog(AdminCommandsArchived(bot))
