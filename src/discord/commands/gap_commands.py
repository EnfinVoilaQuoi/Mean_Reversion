"""Gap Commands - Manage manually scraped gaps"""

import contextlib
import logging
from datetime import datetime

from discord.ext import commands

import discord
from discord import app_commands

from ...database.models import ScrapedGap, Token

logger = logging.getLogger(__name__)


class GapCommands(commands.Cog):
    """Commandes de gestion des gaps scrapés"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="gap_complete", description="Marquer un gap comme complètement scrapé"
    )
    @app_commands.describe(
        symbol="Symbole ou cashtag du Token",
        since_date="Date de début (YYYY-MM-DD)",
        until_date="Date de fin (YYYY-MM-DD)",
        tweet_count="Nombre de tweets (optionnel, défaut: 0)",
    )
    async def mark_gap_complete(
        self,
        interaction: discord.Interaction,
        symbol: str,
        since_date: str,
        until_date: str,
        tweet_count: int = 0,
    ):
        """
        Marque un gap comme complètement scrapé.
        Empêche le système de rescraper cette période.
        """
        await interaction.response.defer()

        try:
            # Valider les formats de dates
            try:
                datetime.strptime(since_date, "%Y-%m-%d")
                datetime.strptime(until_date, "%Y-%m-%d")
            except ValueError:
                await interaction.followup.send(
                    "❌ Format de date invalide. Utilisez YYYY-MM-DD\n"
                    "Exemple: `/gap_complete symbol:TURBO since_date:2025-11-20 until_date:2025-11-25`"
                )
                return

            # Chercher le token
            token = None
            symbol_upper = symbol.upper()

            # Chercher par cashtag
            with contextlib.suppress(Token.DoesNotExist):
                token = Token.get(Token.cashtag == symbol_upper)

            # Chercher par symbol
            if not token:
                with contextlib.suppress(Token.DoesNotExist):
                    token = Token.get(Token.symbol == symbol_upper)

            if not token:
                await interaction.followup.send(
                    f"❌ Token `{symbol}` non trouvé en base de données.\n"
                    f"Utilisez `/add_token` pour l'ajouter d'abord."
                )
                return

            # Vérifier si le gap existe déjà
            existing = None
            with contextlib.suppress(ScrapedGap.DoesNotExist):
                existing = ScrapedGap.get(
                    (ScrapedGap.token == token)
                    & (ScrapedGap.since_date == since_date)
                    & (ScrapedGap.until_date == until_date)
                )

            if existing:
                await interaction.followup.send(
                    f"⚠️  Ce gap est déjà enregistré comme scrapé !\n"
                    f"**{token.cashtag}**: {since_date} → {until_date}\n"
                    f"Enregistré le: {existing.scraped_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"Tweets: {existing.tweet_count}"
                )
                return

            # Créer le ScrapedGap
            gap = ScrapedGap.create(
                token=token,
                since_date=since_date,
                until_date=until_date,
                tweet_count=tweet_count,
                scraped_at=datetime.now(),
            )

            embed = discord.Embed(
                title="✅ Gap marqué comme scrapé", color=discord.Color.green()
            )
            embed.add_field(name="🔹 Token", value=f"`{token.cashtag}`", inline=True)
            embed.add_field(
                name="📅 Période", value=f"{since_date} → {until_date}", inline=False
            )
            embed.add_field(name="📊 Tweets", value=str(tweet_count), inline=True)
            embed.add_field(
                name="⏰ Enregistré",
                value=gap.scraped_at.strftime("%H:%M:%S"),
                inline=True,
            )
            embed.set_footer(text="Ce gap ne sera plus proposé au scraping")

            await interaction.followup.send(embed=embed)
            logger.info(
                f"✅ Gap marqué comme scrapé: {token.cashtag} "
                f"({since_date} → {until_date}), {tweet_count} tweets"
            )

        except Exception as e:
            logger.error(f"❌ Erreur mark_gap_complete: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Une erreur est survenue : {str(e)}")

    @app_commands.command(
        name="gap_list", description="Lister les gaps scrapés et détectés pour un token"
    )
    @app_commands.describe(symbol="Symbole ou cashtag du Token")
    async def list_scraped_gaps(self, interaction: discord.Interaction, symbol: str):
        """Affiche tous les gaps (scrapés + détectés) pour un token."""
        await interaction.response.defer()

        try:
            # Chercher le token
            token = None
            symbol_upper = symbol.upper()

            with contextlib.suppress(Token.DoesNotExist):
                token = Token.get(Token.cashtag == symbol_upper)

            if not token:
                with contextlib.suppress(Token.DoesNotExist):
                    token = Token.get(Token.symbol == symbol_upper)

            if not token:
                await interaction.followup.send(f"❌ Token `{symbol}` non trouvé.")
                return

            # Récupérer les gaps scrapés (manuels)
            scraped_gaps = list(
                ScrapedGap.select()
                .where(ScrapedGap.token == token)
                .order_by(ScrapedGap.since_date)
            )

            # Récupérer les gaps détectés (automatiques avec précision horaire)
            from ...analysis.gap_detector import detect_gaps

            detected_hourly_gaps = detect_gaps(token, lookback_days=7)

            # Convertir les gaps avec l'heure
            detected_gaps = []
            for gap_start, gap_end in detected_hourly_gaps:
                detected_gaps.append(
                    {
                        "since_date": gap_start.strftime("%Y-%m-%d"),
                        "until_date": gap_end.strftime("%Y-%m-%d"),
                        "since_time": gap_start.strftime("%H:%M"),
                        "until_time": gap_end.strftime("%H:%M"),
                        "tweet_count": None,  # Les gaps détectés n'ont pas de compte de tweets
                        "is_detected": True,
                    }
                )

            # Combiner et trier
            all_gaps = []

            # Ajouter les gaps scrapés
            for gap in scraped_gaps:
                all_gaps.append(
                    {
                        "since_date": gap.since_date,
                        "until_date": gap.until_date,
                        "since_time": None,  # Les gaps scrapés n'ont pas l'info horaire
                        "until_time": None,
                        "tweet_count": gap.tweet_count,
                        "is_detected": False,
                    }
                )

            # Ajouter les gaps détectés (qui ne sont pas déjà scrapés)
            scraped_set = {(g["since_date"], g["until_date"]) for g in all_gaps}
            for gap in detected_gaps:
                if (gap["since_date"], gap["until_date"]) not in scraped_set:
                    all_gaps.append(gap)

            # Trier par date
            all_gaps.sort(key=lambda x: x["since_date"])

            if not all_gaps:
                await interaction.followup.send(
                    f"✨ Couverture complète pour **{token.cashtag}** (pas de gaps détectés)"
                )
                return

            embed = discord.Embed(
                title=f"📋 Tous les Gaps - {token.cashtag}", color=discord.Color.blue()
            )

            display_limit = 15
            total_tweets = sum(
                gap["tweet_count"] or 0 for gap in all_gaps if not gap["is_detected"]
            )

            # Séparer les gaps scrapés et détectés
            scraped_list = []
            detected_list = []

            for i, gap in enumerate(all_gaps[:display_limit], 1):
                if gap["is_detected"]:
                    # Format: 2025-11-28 14:00 → 2025-11-29 09:00
                    since_time = f" {gap['since_time']}" if gap["since_time"] else ""
                    until_time = f" {gap['until_time']}" if gap["until_time"] else ""
                    date_range = f"{gap['since_date']}{since_time} → {gap['until_date']}{until_time}"
                    detected_list.append(f"{i}. {date_range}")
                else:
                    # Format: 2025-11-28 → 2025-11-29 (200 tweets)
                    date_range = f"{gap['since_date']} → {gap['until_date']}"
                    tweets_info = (
                        f" ({gap['tweet_count']} tweets)" if gap["tweet_count"] else ""
                    )
                    scraped_list.append(f"{i}. {date_range}{tweets_info}")

            # Afficher les gaps scrapés
            if scraped_list:
                embed.add_field(
                    name="✅ Gaps Scrapés (Manuels)",
                    value="\n".join(scraped_list) or "Aucun",
                    inline=False,
                )

            # Afficher les gaps détectés
            if detected_list:
                embed.add_field(
                    name="⚠️ Gaps Détectés (À Scraper)",
                    value="\n".join(detected_list) or "Aucun",
                    inline=False,
                )

            # Statistiques
            # Calculer les heures de gap détecté
            total_gap_hours: float = 0.0
            for gap_start, gap_end in detected_hourly_gaps:
                gap_hours = (gap_end - gap_start).total_seconds() / 3600
                total_gap_hours += gap_hours

            lookback_hours = 7 * 24  # 7 jours
            coverage_pct = (
                ((lookback_hours - total_gap_hours) / lookback_hours * 100)
                if lookback_hours > 0
                else 0
            )

            stats_text = f"**Total gaps**: {len(all_gaps)}\n"
            stats_text += f"**Gaps scrapés**: {len(scraped_gaps)}\n"
            stats_text += f"**Gaps détectés**: {len(detected_gaps)}\n"
            stats_text += f"**Tweets scrapés**: {total_tweets}\n"
            stats_text += (
                f"**Heures manquantes**: {total_gap_hours:.1f}h / {lookback_hours}h\n"
            )
            stats_text += f"**Couverture**: {coverage_pct:.1f}%"

            embed.add_field(name="📊 Statistiques", value=stats_text, inline=False)

            if len(all_gaps) > display_limit:
                embed.set_footer(
                    text=f"+ {len(all_gaps) - display_limit} autre(s) gap(s) non affiché(s)"
                )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"❌ Erreur list_scraped_gaps: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Une erreur est survenue : {str(e)}")

    @app_commands.command(
        name="gap_delete", description="Supprimer un gap de la liste des gaps scrapés"
    )
    @app_commands.describe(
        symbol="Symbole ou cashtag du Token",
        since_date="Date de début (YYYY-MM-DD)",
        until_date="Date de fin (YYYY-MM-DD)",
    )
    async def delete_gap(
        self,
        interaction: discord.Interaction,
        symbol: str,
        since_date: str,
        until_date: str,
    ):
        """Supprime un gap de la liste des gaps scrapés (relance le scraping)."""
        await interaction.response.defer()

        try:
            # Chercher le token
            token = None
            symbol_upper = symbol.upper()

            with contextlib.suppress(Token.DoesNotExist):
                token = Token.get(Token.cashtag == symbol_upper)

            if not token:
                with contextlib.suppress(Token.DoesNotExist):
                    token = Token.get(Token.symbol == symbol_upper)

            if not token:
                await interaction.followup.send(f"❌ Token `{symbol}` non trouvé.")
                return

            # Chercher le gap
            gap = None
            try:
                gap = ScrapedGap.get(
                    (ScrapedGap.token == token)
                    & (ScrapedGap.since_date == since_date)
                    & (ScrapedGap.until_date == until_date)
                )
            except ScrapedGap.DoesNotExist:
                await interaction.followup.send(
                    f"❌ Gap non trouvé pour **{token.cashtag}**\n"
                    f"Période: {since_date} → {until_date}"
                )
                return

            # Supprimer
            gap.delete_instance()

            embed = discord.Embed(title="✅ Gap supprimé", color=discord.Color.orange())
            embed.add_field(name="🔹 Token", value=f"`{token.cashtag}`", inline=True)
            embed.add_field(
                name="📅 Période", value=f"{since_date} → {until_date}", inline=False
            )
            embed.set_footer(text="Ce gap sera à nouveau proposé au scraping")

            await interaction.followup.send(embed=embed)
            logger.info(
                f"🗑️  Gap supprimé: {token.cashtag} ({since_date} → {until_date})"
            )

        except Exception as e:
            logger.error(f"❌ Erreur delete_gap: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Une erreur est survenue : {str(e)}")


async def setup(bot: commands.Bot):
    """Setup cog with bot"""
    await bot.add_cog(GapCommands(bot))
