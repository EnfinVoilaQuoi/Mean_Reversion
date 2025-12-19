"""Info Commands - list, status, check_fgi, check_total_vol"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import cast

from discord.ext import commands

import discord
from discord import app_commands

from ...analysis.zscore_calc import check_data_coverage, compute_social_zscore
from ...config import MODE_INTERVALS
from ...database.models import MacroMetric, SignalMetric, Token
from ...scrapers.price_worker import get_current_metrics

logger = logging.getLogger(__name__)

# Thread pool dédié pour les opérations longues
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="info_cmd")


class InfoCommands(commands.Cog):
    """Commandes d'information et statistiques"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="list", description="Liste tous les tokens en surveillance"
    )
    async def list_tokens(self, interaction: discord.Interaction):
        """Liste tous les tokens en surveillance."""
        try:
            tokens = Token.select().order_by(Token.status.desc(), Token.symbol)

            if tokens.count() == 0:
                await interaction.response.send_message(
                    "📭 Aucun token en surveillance pour le moment."
                )
                return

            regular = []
            aggressive = []
            croisiere = []
            pending = []
            listed = []
            incomplete = []

            for token in tokens:
                token_info = f"{token.cashtag}"
                if token.status == "REGULAR":
                    regular.append(token_info)
                elif token.status == "AGGRESSIVE":
                    aggressive.append(token_info)
                elif token.status == "CROISIERE":
                    croisiere.append(token_info)
                elif token.status == "PENDING":
                    pending.append(token_info)
                elif token.status == "LISTED":
                    listed.append(token_info)
                elif token.status == "INCOMPLETE":
                    incomplete.append(token_info)

            embed = discord.Embed(
                title="📊 Tokens en Surveillance", color=discord.Color.gold()
            )

            max_display = 5

            if aggressive:
                display_list = aggressive[:max_display]
                remaining = len(aggressive) - max_display
                value = ", ".join(display_list)
                if remaining > 0:
                    value += f" ... +{remaining}"
                embed.add_field(
                    name=f"🔥 AGGRESSIVE (5 min) - {len(aggressive)}",
                    value=value or "Aucun",
                    inline=False,
                )

            if regular:
                display_list = regular[:max_display]
                remaining = len(regular) - max_display
                value = ", ".join(display_list)
                if remaining > 0:
                    value += f" ... +{remaining}"
                embed.add_field(
                    name=f"📈 REGULAR (15 min) - {len(regular)}",
                    value=value or "Aucun",
                    inline=False,
                )

            if croisiere:
                display_list = croisiere[:max_display]
                remaining = len(croisiere) - max_display
                value = ", ".join(display_list)
                if remaining > 0:
                    value += f" ... +{remaining}"
                embed.add_field(
                    name=f"🔵 CROISIERE (60 min) - {len(croisiere)}",
                    value=value or "Aucun",
                    inline=False,
                )

            if pending:
                display_list = pending[:max_display]
                remaining = len(pending) - max_display
                value = ", ".join(display_list)
                if remaining > 0:
                    value += f" ... +{remaining}"
                embed.add_field(
                    name=f"⏸️ PENDING (Pause) - {len(pending)}",
                    value=value or "Aucun",
                    inline=False,
                )

            if listed:
                display_list = listed[:max_display]
                remaining = len(listed) - max_display
                value = ", ".join(display_list)
                if remaining > 0:
                    value += f" ... +{remaining}"
                embed.add_field(
                    name=f"📋 LISTED (Prêts) - {len(listed)}",
                    value=value or "Aucun",
                    inline=False,
                )

            if incomplete:
                display_list = incomplete[:max_display]
                remaining = len(incomplete) - max_display
                value = ", ".join(display_list)
                if remaining > 0:
                    value += f" ... +{remaining}"
                embed.add_field(
                    name=f"⚠️ INCOMPLETE (Infos manquantes) - {len(incomplete)}",
                    value=value or "Aucun",
                    inline=False,
                )

            embed.set_footer(
                text=f"Total: {tokens.count()} token(s) | Limité à {max_display} par catégorie"
            )
            await interaction.response.send_message(embed=embed)

        except Exception as e:
            logger.error(f"❌ Erreur list_tokens: {e}", exc_info=True)
            await interaction.response.send_message("❌ Une erreur est survenue.")

    @app_commands.command(name="status", description="Info detaillé d'un Token")
    @app_commands.describe(symbol="symbol")
    async def token_status(
        self, interaction: discord.Interaction, symbol: str | None = None
    ):
        """Affiche les statistiques détaillées d'un token."""
        await interaction.response.defer()

        try:
            if not symbol:
                await interaction.followup.send("❌ Vous devez spécifier un symbole.")
                return

            symbol_clean = symbol.upper().replace("$", "")
            token = (
                Token.select()
                .where(
                    (Token.symbol == symbol_clean)
                    | (Token.cashtag == f"${symbol_clean}")
                )
                .first()
            )

            if not token:
                await interaction.followup.send(
                    f"❌ Token **{symbol_clean}** non trouvé."
                )
                return

            # Extraire la paire valide pour DexScreener
            # Ignorer les pairs CEX (MEXC:..., BINANCE:...) et CEX chain_id
            active_pair = None
            active_chain = None

            # Préférer primary_pair_address (v2)
            if token.primary_pair_address and ":" not in token.primary_pair_address:
                active_pair = token.primary_pair_address
                active_chain = token.primary_chain_id or token.chain_id

            # Fallback sur pair_address (legacy)
            if (
                not active_pair
                and token.pair_address
                and ":" not in token.pair_address
                and token.pair_address != "CEX"
            ):
                active_pair = token.pair_address
                active_chain = (
                    token.chain_id
                    if token.chain_id and token.chain_id.upper() != "CEX"
                    else None
                )

            metrics_data = (
                get_current_metrics(active_chain, active_pair)
                if (active_pair and active_chain)
                else None
            )

            # Architecture 3NF: Utiliser SignalMetric uniquement
            last_signal = (
                SignalMetric.select()
                .where(SignalMetric.token == token)
                .order_by(SignalMetric.timestamp.desc())
                .first()
            )

            # Compter les métriques SignalMetric
            metrics_count = (
                SignalMetric.select().where(SignalMetric.token == token).count()
            )

            contract_complete = all(
                [
                    token.cashtag,
                    token.contract,
                    token.primary_pair_address or token.pair_address,
                    token.primary_price_source,
                ]
            )

            if contract_complete:
                contract_status = "✅ Complet"
                color = discord.Color.green()
            else:
                contract_status = "⚠️ Incomplet"
                color = discord.Color.orange()

            embed = discord.Embed(title=f"📊 Status de {token.cashtag}", color=color)

            embed.add_field(name="Symbole", value=token.symbol, inline=True)
            embed.add_field(name="Mode", value=token.status, inline=True)
            embed.add_field(
                name="Chaîne",
                value=token.primary_chain_id or token.chain_id or "N/A",
                inline=True,
            )

            if token.rank:
                embed.add_field(
                    name="📊 Rank MarketCap", value=f"#{token.rank}", inline=True
                )

            if token.twitter_verified is not None or token.twitter_blue is not None:
                if token.twitter_blue:
                    if token.twitter_blue_type == "Business":
                        verification_status = "✅ Verified Business"
                    elif token.twitter_blue_type == "Government":
                        verification_status = "✅ Verified Government"
                    else:
                        verification_status = "🔵 Twitter Blue"
                elif token.twitter_verified:
                    verification_status = "✅ Legacy Verified"
                else:
                    verification_status = "❌ Not Verified"

                embed.add_field(
                    name="🐦 Vérification Twitter",
                    value=verification_status,
                    inline=True,
                )

                if token.twitter_verification_checked_at:
                    embed.add_field(
                        name="🕐 Dernière Vérif.",
                        value=token.twitter_verification_checked_at.strftime(
                            "%Y-%m-%d"
                        ),
                        inline=True,
                    )

            # Ajouter la couverture des données sociales (7 jours)
            try:
                coverage_result = check_data_coverage(
                    token, days_back=7, coverage_threshold=0.80
                )
                coverage_percent = coverage_result["social_coverage_percent"]
                coverage_emoji = "✅" if coverage_result["is_sufficient"] else "⚠️"
                embed.add_field(
                    name="📊 Couverture Données (7j)",
                    value=f"{coverage_emoji} {coverage_percent:.1f}%",
                    inline=True,
                )
            except Exception as e:
                logger.warning(f"Erreur calcul couverture pour {token.cashtag}: {e}")

            embed.add_field(
                name="📝 Données Contrat",
                value=f"{contract_status}\n"
                f"Cashtag: {'✅' if token.cashtag else '❌'}\n"
                f"Adresse: {'✅' if token.contract else '❌'}\n"
                f"Paire: {'✅' if (token.primary_pair_address or token.pair_address) else '❌'}\n"
                f"Exchange: {'✅' if token.primary_price_source else '❌'}",
                inline=False,
            )

            if metrics_data:
                embed.add_field(
                    name="💰 Prix",
                    value=f"${metrics_data['price_usd']:.8f}",
                    inline=True,
                )
                embed.add_field(
                    name="📊 Vol 1h",
                    value=f"${metrics_data['volume_h1']:,.0f}",
                    inline=True,
                )
                embed.add_field(
                    name="💧 Liquidité",
                    value=f"${metrics_data['liquidity_usd']:,.0f}",
                    inline=True,
                )

                now = datetime.now()
                embed.add_field(
                    name="📈 Volume 24h",
                    value=f"${metrics_data['volume_h24']:,.0f}\n🕐 {now.strftime('%Y-%m-%d %H:%M:%S')}",
                    inline=False,
                )

            # Afficher les Z-Scores (SignalMetric uniquement - Architecture 3NF)
            if last_signal:
                embed.add_field(
                    name="📈 Z-Score Social",
                    value=f"{last_signal.z_score_social:.2f}",
                    inline=True,
                )
                embed.add_field(
                    name="💹 Z-Score Prix",
                    value=f"{last_signal.z_score_price:.2f}"
                    if last_signal.z_score_price
                    else "N/A",
                    inline=True,
                )
                embed.add_field(
                    name="⚖️ Divergence",
                    value=f"{last_signal.divergence_score:+.2f}",
                    inline=True,
                )
                embed.add_field(
                    name="🕐 Dernière Analyse",
                    value=last_signal.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    inline=True,
                )

            embed.add_field(
                name="📚 Métriques stockées",
                value=f"{metrics_count} snapshots",
                inline=False,
            )

            if token.twitter_link:
                embed.add_field(
                    name="🐦 Twitter", value=token.twitter_link, inline=False
                )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"❌ Erreur token_status: {e}", exc_info=True)
            await interaction.followup.send("❌ Une erreur est survenue.")

    @app_commands.command(name="check_fgi", description="Vérifie le Fear & Greed Index")
    async def check_fgi(self, interaction: discord.Interaction):
        """Vérifie la dernière occurrence du Fear & Greed Index."""
        try:
            latest_metric = (
                MacroMetric.select()
                .where(MacroMetric.fear_greed_index.is_null(False))
                .order_by(MacroMetric.timestamp.desc())
                .first()
            )

            if not latest_metric:
                await interaction.response.send_message(
                    "❌ Aucune donnée FGI disponible dans la base de données."
                )
                return

            fgi = latest_metric.fear_greed_index
            if fgi >= 75:
                sentiment = "Extreme Greed 🔥"
                color = discord.Color.red()
            elif fgi >= 56:
                sentiment = "Greed 📈"
                color = discord.Color.orange()
            elif fgi >= 45:
                sentiment = "Neutral ⚖️"
                color = discord.Color.gold()
            elif fgi >= 25:
                sentiment = "Fear 😰"
                color = discord.Color.blue()
            else:
                sentiment = "Extreme Fear 🥶"
                color = discord.Color.dark_blue()

            time_ago = datetime.now() - latest_metric.timestamp
            hours = int(time_ago.total_seconds() / 3600)
            minutes = int((time_ago.total_seconds() % 3600) / 60)

            embed = discord.Embed(
                title="😐 Fear & Greed Index",
                description=f"**{fgi}/100** - {sentiment}",
                color=color,
            )
            embed.add_field(
                name="📅 Dernière mise à jour",
                value=latest_metric.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                inline=True,
            )
            embed.add_field(
                name="⏰ Il y a", value=f"{hours}h {minutes}min", inline=True
            )
            embed.add_field(name="📡 Source", value="Alternative.me API", inline=False)
            embed.add_field(name="📂 Fichier", value="macro_worker.py", inline=True)
            embed.set_footer(
                text="💡 Le FGI est mis à jour automatiquement toutes les jours"
            )

            await interaction.response.send_message(embed=embed)

        except Exception as e:
            logger.error(f"❌ Erreur check_fgi: {e}", exc_info=True)
            await interaction.response.send_message(
                f"❌ Une erreur est survenue : {str(e)}"
            )

    @app_commands.command(
        name="check_total_vol", description="Vérifie le Volume Global Total 24h"
    )
    async def check_total_vol(self, interaction: discord.Interaction):
        """Vérifie la dernière occurrence du Volume Global Total."""
        try:
            latest_metric = (
                MacroMetric.select()
                .where(MacroMetric.global_volume_usd.is_null(False))
                .order_by(MacroMetric.timestamp.desc())
                .first()
            )

            if not latest_metric:
                await interaction.response.send_message(
                    "❌ Aucune donnée de Volume Global disponible dans la base de données."
                )
                return

            volume = latest_metric.global_volume_usd

            time_ago = datetime.now() - latest_metric.timestamp
            hours = int(time_ago.total_seconds() / 3600)
            minutes = int((time_ago.total_seconds() % 3600) / 60)

            embed = discord.Embed(
                title="📊 Volume Global Crypto (24h)",
                description=f"**${volume:,.0f} USD**",
                color=discord.Color.blue(),
            )
            embed.add_field(
                name="📅 Dernière mise à jour",
                value=latest_metric.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                inline=True,
            )
            embed.add_field(
                name="⏰ Il y a", value=f"{hours}h {minutes}min", inline=True
            )
            embed.add_field(
                name="📡 Source", value="CoinGecko API (/global)", inline=False
            )
            embed.add_field(name="📂 Fichier", value="macro_worker.py", inline=True)
            embed.set_footer(
                text="💡 Le Volume Global est mis à jour automatiquement toutes les jours"
            )

            await interaction.response.send_message(embed=embed)

        except Exception as e:
            logger.error(f"❌ Erreur check_total_vol: {e}", exc_info=True)
            await interaction.response.send_message(
                f"❌ Une erreur est survenue : {str(e)}"
            )

    @app_commands.command(name="zscore", description="Calcule le Z-Score pour un token")
    @app_commands.describe(symbol="Symbole ou cashtag du Token")
    async def calculate_zscore(self, interaction: discord.Interaction, symbol: str):
        """Calcule et affiche le Z-Score social/prix pour un token."""
        logger.info(
            f"🎯 Commande /zscore reçue - Interaction ID: {interaction.id}, Symbol: {symbol}"
        )

        try:
            await interaction.response.defer()
            logger.info(f"✅ Defer réussi pour interaction {interaction.id}")
        except discord.errors.NotFound:
            logger.error(
                f"❌ Interaction {interaction.id} déjà invalide au moment du defer - COMMANDE ANNULÉE"
            )
            return
        except Exception as e:
            logger.error(
                f"❌ Erreur lors du defer pour interaction {interaction.id}: {e} - COMMANDE ANNULÉE"
            )
            return

        try:
            symbol_clean = symbol.upper().replace("$", "")
            token = (
                Token.select()
                .where(
                    (Token.symbol == symbol_clean)
                    | (Token.cashtag == f"${symbol_clean}")
                )
                .first()
            )

            if not token:
                await interaction.followup.send(
                    f"❌ Token **{symbol_clean}** non trouvé."
                )
                return

            # Récupérer les métriques actuelles
            # Priorité: primary_pair_address (v2) > pair_address (legacy)
            active_pair = token.primary_pair_address or token.pair_address
            # Priorité: primary_chain_id (v2) > chain_id (legacy)
            active_chain = token.primary_chain_id or token.chain_id

            logger.info(f"📊 Z-Score pour {token.cashtag}")
            logger.debug(f"   🔗 Pair: {active_pair}")
            logger.debug(f"   ⛓️  Chain: {active_chain}")

            if not active_pair or not active_chain or active_chain.upper() == "CEX":
                logger.warning(
                    f"❌ Données incomplètes: pair={active_pair}, chain={active_chain}"
                )
                await interaction.followup.send(
                    f"❌ Données incomplètes pour **{token.cashtag}**\n"
                    f"Pair Address: `{active_pair or 'N/A'}`\n"
                    f"Chain: `{active_chain or 'N/A'}`\n\n"
                    f"Utilisez `/resolve symbol:{symbol_clean}` pour les mettre à jour."
                )
                return

            metrics_data = get_current_metrics(active_chain, active_pair)

            if not metrics_data:
                logger.warning(
                    f"❌ Impossible de récupérer les métriques: {active_chain}/{active_pair}"
                )
                await interaction.followup.send(
                    f"❌ Impossible de récupérer les métriques de prix pour **{token.cashtag}**.\n"
                    f"Vérifiez que la paire est correcte: `{active_pair}` sur `{active_chain}`"
                )
                return

            # Vérifier et synchroniser le pipeline avant calcul
            logger.info(f"🔄 Vérification pipeline pour {token.cashtag}...")
            try:
                from ...analysis.metric_aggregator import MetricAggregator

                aggregator = MetricAggregator()

                # Orchestration incrémentale (comble les gaps si nécessaire)
                # Exécuter dans le thread pool dédié pour ne pas bloquer le bot
                loop = asyncio.get_event_loop()
                agg_result = await loop.run_in_executor(
                    _executor,
                    lambda: aggregator.orchestrate_token_incremental(
                        cast(Token, token)
                    ),
                )

                if agg_result["status"] in ["SUCCESS", "NO_ACTION_NEEDED"]:
                    logger.info(f"✅ Pipeline prêt: {agg_result['status']}")
                elif agg_result["status"] == "PARTIAL":
                    logger.warning(
                        f"⚠️  Pipeline partiellement prêt: {agg_result['status']}"
                    )
                else:
                    logger.error(f"❌ Problème pipeline: {agg_result['status']}")

            except Exception as e:
                logger.error(f"❌ Erreur vérification pipeline (non-bloquant): {e}")
                # Continuer malgré l'erreur (calcul z-score tentera avec données existantes)

            # Calculer le Z-Score avec la fenêtre appropriée selon le mode du token
            window_minutes = int(
                cast(int, MODE_INTERVALS.get(token.status, 15))
            )  # Défaut: 15 min
            logger.info(
                f"📊 Calcul Z-Score pour {token.cashtag}... (mode: {token.status}, fenêtre: {window_minutes}min)"
            )
            result = compute_social_zscore(
                token=token,
                price=metrics_data["price_usd"],
                trading_volume_h1=metrics_data["volume_h1"],
                trading_volume_h24=metrics_data["volume_h24"],
                liquidity_usd=metrics_data["liquidity_usd"],
                fgi_correction=None,
                window_minutes=window_minutes,
            )

            if not result:
                await interaction.followup.send(
                    f"❌ Erreur lors du calcul du Z-Score pour **{token.cashtag}**."
                )
                return

            # Préparer l'embed
            z_social = result["z_score_social"]
            z_price = result["z_score_price"]
            divergence = result["divergence_score"]
            tweet_count = result["tweet_count"]

            # Déterminer la couleur selon les z-scores
            if abs(z_social) > 2.5:
                color = discord.Color.red()
            elif abs(z_social) > 1.5:
                color = discord.Color.orange()
            else:
                color = discord.Color.green()

            embed = discord.Embed(title=f"📊 Z-Score pour {token.cashtag}", color=color)

            # Z-Scores
            embed.add_field(
                name="📱 Z-Score Social", value=f"**{z_social:+.2f}**", inline=True
            )
            embed.add_field(
                name="💹 Z-Score Prix", value=f"**{z_price:+.2f}**", inline=True
            )
            embed.add_field(
                name="⚖️ Divergence", value=f"**{divergence:+.2f}**", inline=True
            )

            # Métriques sociales
            embed.add_field(
                name="📈 Métriques Sociales",
                value=f"Tweets ({window_minutes}min): {tweet_count}\n"
                f"Densité: {result['social_density']:.2f}\n"
                f"Volume: {result['social_volume']:.0f}",
                inline=False,
            )

            # Métriques de prix
            embed.add_field(
                name="💰 Métriques Prix",
                value=f"Prix: ${metrics_data['price_usd']:.8f}\n"
                f"Vol 1h: ${metrics_data['volume_h1']:,.0f}\n"
                f"Vol 24h: ${metrics_data['volume_h24']:,.0f}\n"
                f"Liquidité: ${metrics_data['liquidity_usd']:,.0f}",
                inline=False,
            )

            embed.add_field(
                name="🕐 Timestamp",
                value=result["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                inline=True,
            )

            # Envoyer la réponse avec gestion d'erreur spécifique
            logger.debug(
                f"📤 Tentative d'envoi de la réponse pour {token.cashtag} (interaction {interaction.id})"
            )
            try:
                await interaction.followup.send(embed=embed)
                logger.info(f"✅ Réponse Z-Score envoyée pour {token.cashtag}")
            except discord.errors.NotFound as e:
                logger.error(
                    f"❌ Interaction {interaction.id} expirée pour {token.cashtag}, "
                    f"impossible d'envoyer la réponse. Erreur: {e}"
                )
            except discord.errors.HTTPException as e:
                logger.error(f"❌ Erreur HTTP Discord lors de l'envoi: {e}")
            except Exception as e:
                # Catch toute autre exception pour éviter qu'elle remonte au bloc externe
                logger.error(
                    f"❌ Erreur inattendue lors de l'envoi Discord: {e}", exc_info=True
                )

            # Sortir proprement sans passer par le bloc except externe
            return

        except Exception as e:
            logger.error(f"❌ Erreur calculate_zscore: {e}", exc_info=True)
            try:
                await interaction.followup.send(
                    f"❌ Une erreur est survenue : {str(e)}"
                )
            except (discord.errors.NotFound, discord.errors.HTTPException) as err:
                logger.error(f"❌ Impossible d'envoyer le message d'erreur: {err}")


async def setup(bot: commands.Bot):
    """Setup cog with bot"""
    await bot.add_cog(InfoCommands(bot))
