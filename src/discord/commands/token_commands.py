"""Token Management Commands - add_token, add_pair, set_mode, reset_token, exclude_token"""

import logging

from discord.ext import commands

import discord
from discord import app_commands

from ...database.models import (
    PriceMetric,
    ProjectFollowers,
    RawTweet,
    SignalLog,
    SocialMetric,
    Token,
)
from ...scrapers.price_worker import get_pair_metadata, get_token_metadata
from ...utils.token_utils import (
    find_token_by_symbol_or_address,
    get_token_info_for_monitoring,
)

logger = logging.getLogger(__name__)


class TokenCommands(commands.Cog):
    """Commandes de gestion des tokens"""

    def __init__(
        self,
        bot: commands.Bot,
        scheduler=None,
        update_job_func=None,
        add_token_backfill_func=None,
    ):
        self.bot = bot
        self.scheduler = scheduler
        self.update_monitoring_job_func = update_job_func
        self.add_token_and_start_backfill_func = add_token_backfill_func

    @app_commands.command(
        name="add_token", description="Ajoute un token à la surveillance"
    )
    @app_commands.describe(
        identifier="Adresse de contrat, symbol", chain_id="Chain (optionnel)"
    )
    async def add_token(
        self,
        interaction: discord.Interaction,
        identifier: str,
        chain_id: str | None = None,
    ):
        """
        Ajoute un token à la surveillance (mode automatique ou manuel).

        Mode Auto (recherche DB - sans chain) :
            /add_token WAVES              → Cherche symbol dans DB
            /add_token $WAVES             → Cherche cashtag dans DB
            /add_token 0x1234...          → Cherche contract dans DB

        Mode Manuel (DexScreener direct - avec chain) :
            /add_token 0x1234... ethereum → Recherche DexScreener
            /add_token 0xabc... bsc       → Recherche DexScreener
        """
        if chain_id is None:
            # MODE AUTOMATIQUE
            await interaction.response.send_message(
                f"🔍 Recherche de **{identifier}** dans la base de données..."
            )

            try:
                db_token = find_token_by_symbol_or_address(identifier)

                if not db_token:
                    await interaction.followup.send(
                        f"❌ Token **{identifier}** non trouvé dans la base de données.\n"
                        f"💡 Lancez `/screen` pour mettre à jour la DB ou utilisez `/add_token {identifier} <chain>` pour le mode manuel."
                    )
                    return

                if db_token.status in ["REGULAR", "AGGRESSIVE", "CROISIERE", "PENDING"]:
                    await interaction.followup.send(
                        f"❌ **{db_token.cashtag}** est déjà en surveillance (Mode: {db_token.status}).\n"
                        f"💡 Utilisez `/set_mode {db_token.cashtag} <mode>` pour changer le mode."
                    )
                    return

                token_info = get_token_info_for_monitoring(db_token)

                if not token_info:
                    await interaction.followup.send(
                        f"❌ **{db_token.cashtag}** n'a pas de paire recommandée définie.\n"
                        f"💡 Utilisez le mode manuel : `/add_token {db_token.contract} <chain>`"
                    )
                    return

                embed = discord.Embed(
                    title="✅ Token trouvé dans la base de données !",
                    color=discord.Color.blue(),
                )
                embed.add_field(name="Token", value=token_info["cashtag"], inline=True)
                embed.add_field(
                    name="Rang",
                    value=f"#{token_info['rank']}" if token_info["rank"] else "N/A",
                    inline=True,
                )
                embed.add_field(
                    name="Source Prix",
                    value=token_info["primary_price_source"] or "N/A",
                    inline=True,
                )
                embed.add_field(
                    name="Chaîne", value=token_info["chain_id"], inline=True
                )
                embed.add_field(
                    name="Paire",
                    value=token_info["pair_address"][:10] + "...",
                    inline=True,
                )
                embed.add_field(
                    name="Statut", value=token_info["rank_status"], inline=True
                )

                await interaction.followup.send(embed=embed)
                await interaction.followup.send(
                    "⏳ Lancement du backfill et mise en surveillance..."
                )

                db_token.chain_id = token_info["chain_id"]
                db_token.pair_address = token_info["pair_address"]
                db_token.save()

                if self.add_token_and_start_backfill_func:
                    success = self.add_token_and_start_backfill_func(db_token)
                    if not success:
                        await interaction.followup.send(
                            "❌ Erreur lors de la planification du backfill."
                        )
                        return
                else:
                    await interaction.followup.send(
                        "❌ Fonction de backfill non disponible."
                    )
                    return

                await interaction.followup.send(
                    f"✅ **{db_token.cashtag}** ajouté. Backfill en cours (statut temporaire : PENDING)."
                )

            except Exception as e:
                logger.error(f"❌ Erreur add_token (mode auto): {e}", exc_info=True)
                await interaction.followup.send(
                    f"❌ Une erreur inattendue est survenue : {str(e)}"
                )

        else:
            # MODE MANUEL
            token_address = identifier
            await interaction.response.send_message(
                f"🔍 Recherche manuelle sur DexScreener : {token_address[:10]}... ({chain_id})..."
            )

            try:
                metadata = get_token_metadata(token_address, chain_id)

                if not metadata:
                    await interaction.followup.send(
                        "❌ Impossible de récupérer les données DexScreener pour cette adresse."
                    )
                    return

                existing = (
                    Token.select()
                    .where(Token.contract == token_address.lower())
                    .first()
                )
                if existing:
                    await interaction.followup.send(
                        f"❌ Ce token existe déjà : **{existing.cashtag}** (Status: {existing.status})"
                    )
                    return

                if metadata.get("error"):
                    await interaction.followup.send(f"⚠️ {metadata['error']}")

                token = Token.create(
                    contract=token_address.lower(),
                    symbol=metadata["symbol"],
                    cashtag=metadata["cashtag"],
                    chain_id=chain_id,
                    pair_address=metadata.get("pair_address"),
                    twitter_link=metadata.get("twitter_link"),
                    telegram_link=metadata.get("telegram_link"),
                    status="PENDING",
                )

                await interaction.followup.send(
                    f"⏳ Lancement du backfill Twitter (7 jours) pour **{token.cashtag}**..."
                )

                if self.add_token_and_start_backfill_func:
                    success = self.add_token_and_start_backfill_func(token)
                    if not success:
                        await interaction.followup.send(
                            "❌ Erreur lors de la planification du backfill."
                        )
                        return
                else:
                    await interaction.followup.send(
                        "❌ Fonction de backfill non disponible."
                    )
                    return

                embed = discord.Embed(
                    title=f"✨ {token.cashtag} ajouté avec succès (mode manuel) !",
                    color=discord.Color.green(),
                )
                embed.add_field(name="Symbole", value=token.symbol, inline=True)
                embed.add_field(name="Chaîne", value=chain_id, inline=True)
                embed.add_field(
                    name="Statut", value="PENDING (temporaire)", inline=True
                )
                embed.add_field(
                    name="Adresse", value=token_address[:10] + "...", inline=True
                )
                embed.add_field(
                    name="Twitter",
                    value=metadata.get("twitter_link") or "❌ Non trouvé",
                    inline=False,
                )
                embed.add_field(
                    name="📌 Info",
                    value="Backfill 7j en cours → Passage automatique en REGULAR (15 min) après complétion.",
                    inline=False,
                )

                await interaction.followup.send(embed=embed)

            except Exception as e:
                logger.error(f"❌ Erreur add_token (mode manuel): {e}", exc_info=True)
                await interaction.followup.send(
                    f"❌ Une erreur inattendue est survenue : {str(e)}"
                )

    @app_commands.command(
        name="add_pair", description="Ajoute une paire à la surveillance"
    )
    @app_commands.describe(
        identifier="Pair(adresse), pair(symbol)", chain_id="Chaîne (optionnel)"
    )
    async def add_pair(
        self,
        interaction: discord.Interaction,
        identifier: str,
        chain_id: str | None = None,
    ):
        """
        Ajoute un token via sa paire (mode automatique ou manuel).

        Mode Auto (recherche DB - sans chain) :
            /add_pair WAVESUSDT               → Cherche symbol dans DB
            /add_pair 0x1234...               → Cherche contract dans DB

        Mode Manuel (DexScreener /pairs/ - avec chain) :
            /add_pair 0x1cf459... bsc     → Recherche paire DexScreener
            /add_pair 0xabc... ethereum   → Recherche paire DexScreener
        """
        if chain_id is None:
            # MODE AUTOMATIQUE
            await interaction.response.send_message(
                f"🔍 Recherche de **{identifier}** dans la base de données..."
            )

            try:
                db_token = find_token_by_symbol_or_address(identifier)

                if not db_token:
                    await interaction.followup.send(
                        f"❌ Token **{identifier}** non trouvé dans la base de données.\n"
                        f"💡 Lancez `/screen` pour mettre à jour la DB ou utilisez `/add_pair {identifier} <chain>` pour le mode manuel."
                    )
                    return

                if db_token.status in ["REGULAR", "AGGRESSIVE", "CROISIERE", "PENDING"]:
                    await interaction.followup.send(
                        f"❌ **{db_token.cashtag}** est déjà en surveillance (Mode: {db_token.status}).\n"
                        f"💡 Utilisez `/set_mode {db_token.cashtag} <mode>` pour changer le mode."
                    )
                    return

                token_info = get_token_info_for_monitoring(db_token)

                if not token_info:
                    await interaction.followup.send(
                        f"❌ **{db_token.cashtag}** n'a pas de paire recommandée définie.\n"
                        f"💡 Utilisez le mode manuel : `/add_pair <pair_address> <chain>`"
                    )
                    return

                embed = discord.Embed(
                    title="✅ Token trouvé dans la base de données !",
                    color=discord.Color.blue(),
                )
                embed.add_field(name="Token", value=token_info["cashtag"], inline=True)
                embed.add_field(
                    name="Rang",
                    value=f"#{token_info['rank']}" if token_info["rank"] else "N/A",
                    inline=True,
                )
                embed.add_field(
                    name="Source Prix",
                    value=token_info["primary_price_source"] or "N/A",
                    inline=True,
                )
                embed.add_field(
                    name="Chaîne", value=token_info["chain_id"], inline=True
                )
                embed.add_field(
                    name="Paire",
                    value=token_info["pair_address"][:10] + "...",
                    inline=True,
                )
                embed.add_field(
                    name="Statut", value=token_info["rank_status"], inline=True
                )

                await interaction.followup.send(embed=embed)
                await interaction.followup.send(
                    "⏳ Lancement du backfill et mise en surveillance..."
                )

                db_token.chain_id = token_info["chain_id"]
                db_token.pair_address = token_info["pair_address"]
                db_token.save()

                if self.add_token_and_start_backfill_func:
                    success = self.add_token_and_start_backfill_func(db_token)
                    if not success:
                        await interaction.followup.send(
                            "❌ Erreur lors de la planification du backfill."
                        )
                        return
                else:
                    await interaction.followup.send(
                        "❌ Fonction de backfill non disponible."
                    )
                    return

                await interaction.followup.send(
                    f"✅ **{db_token.cashtag}** ajouté. Backfill en cours (statut temporaire : PENDING)."
                )

            except Exception as e:
                logger.error(f"❌ Erreur add_pair (mode auto): {e}", exc_info=True)
                await interaction.followup.send(
                    f"❌ Une erreur inattendue est survenue : {str(e)}"
                )

        else:
            # MODE MANUEL
            pair_address = identifier
            await interaction.response.send_message(
                f"🔍 Recherche manuelle DexScreener (paire) : {pair_address[:10]}... ({chain_id})..."
            )

            try:
                metadata = get_pair_metadata(pair_address, chain_id)

                if not metadata:
                    await interaction.followup.send(
                        "❌ Impossible de récupérer les données de la paire DexScreener."
                    )
                    return

                token_address = metadata.get("token_address")
                if not token_address:
                    await interaction.followup.send(
                        "❌ Adresse token introuvable dans les métadonnées."
                    )
                    return

                existing = (
                    Token.select()
                    .where(Token.contract == token_address.lower())
                    .first()
                )
                if existing:
                    await interaction.followup.send(
                        f"❌ Ce token existe déjà : **{existing.cashtag}** (Status: {existing.status})"
                    )
                    return

                if metadata.get("error"):
                    await interaction.followup.send(f"⚠️ {metadata['error']}")

                token = Token.create(
                    contract=token_address.lower(),
                    symbol=metadata["symbol"],
                    cashtag=metadata["cashtag"],
                    chain_id=chain_id,
                    pair_address=metadata.get("pair_address"),
                    twitter_link=metadata.get("twitter_link"),
                    telegram_link=metadata.get("telegram_link"),
                    status="PENDING",
                )

                await interaction.followup.send(
                    f"⏳ Lancement du backfill Twitter (7 jours) pour **{token.cashtag}**..."
                )

                if self.add_token_and_start_backfill_func:
                    success = self.add_token_and_start_backfill_func(token)
                    if not success:
                        await interaction.followup.send(
                            "❌ Erreur lors de la planification du backfill."
                        )
                        return
                else:
                    await interaction.followup.send(
                        "❌ Fonction de backfill non disponible."
                    )
                    return

                embed = discord.Embed(
                    title=f"✨ {token.cashtag} ajouté via paire (mode manuel) !",
                    color=discord.Color.green(),
                )
                embed.add_field(name="Symbole", value=token.symbol, inline=True)
                embed.add_field(name="Chaîne", value=chain_id, inline=True)
                embed.add_field(
                    name="Statut", value="PENDING (temporaire)", inline=True
                )
                embed.add_field(
                    name="Adresse Token", value=token_address[:10] + "...", inline=True
                )
                embed.add_field(
                    name="Adresse Pair", value=pair_address[:10] + "...", inline=True
                )
                embed.add_field(
                    name="Twitter",
                    value=metadata.get("twitter_link") or "❌ Non trouvé",
                    inline=False,
                )
                embed.add_field(
                    name="📌 Info",
                    value="Backfill 7j en cours → Passage automatique en REGULAR (15 min) après complétion.",
                    inline=False,
                )

                await interaction.followup.send(embed=embed)

            except Exception as e:
                logger.error(f"❌ Erreur add_pair (mode manuel): {e}", exc_info=True)
                await interaction.followup.send(
                    f"❌ Une erreur inattendue est survenue : {str(e)}"
                )

    @app_commands.command(
        name="set_mode", description="Applique un mode de surveillance à un token"
    )
    @app_commands.describe(
        identifier="Symbole ou cashtag du token (ex: PEPE, $PEPE)",
        mode="Mode de surveillance (regular, aggressive, croisiere, pending)",
    )
    async def set_mode(
        self, interaction: discord.Interaction, identifier: str, mode: str
    ):
        """Change le mode de surveillance d'un token."""
        mode = mode.upper()
        valid_modes = ["REGULAR", "AGGRESSIVE", "CROISIERE", "PENDING"]

        if mode not in valid_modes:
            await interaction.response.send_message(
                f"❌ Mode invalide. Utilisez: {' / '.join(valid_modes)}."
            )
            return

        try:
            symbol_clean = identifier.upper().replace("$", "")
            token = (
                Token.select()
                .where(
                    (Token.symbol == symbol_clean)
                    | (Token.cashtag == f"${symbol_clean}")
                )
                .first()
            )

            if not token:
                await interaction.response.send_message(
                    f"❌ Token **{symbol_clean}** non trouvé dans la base de données."
                )
                return

            old_status = token.status

            if self.update_monitoring_job_func:
                self.update_monitoring_job_func(token, mode)
            else:
                await interaction.response.send_message(
                    "❌ Erreur: Fonction de mise à jour non disponible."
                )
                return

            interval_map = {
                "AGGRESSIVE": "5 min",
                "REGULAR": "15 min",
                "CROISIERE": "60 min",
                "PENDING": "PAUSE",
            }

            embed = discord.Embed(
                title=f"✅ Mode de {token.cashtag} mis à jour",
                color=discord.Color.blue(),
            )
            embed.add_field(name="Ancien mode", value=old_status, inline=True)
            embed.add_field(name="Nouveau mode", value=mode, inline=True)
            embed.add_field(
                name="Fréquence", value=interval_map.get(mode, "N/A"), inline=True
            )

            await interaction.response.send_message(embed=embed)

        except Exception as e:
            logger.error(f"❌ Erreur set_mode: {e}", exc_info=True)
            await interaction.response.send_message(
                f"❌ Une erreur inattendue est survenue : {str(e)}"
            )

    @app_commands.command(
        name="reset_token", description="Réinitialise un token (passe en INCOMPLETE)"
    )
    @app_commands.describe(symbol="Symbole du token à réinitialiser")
    async def reset_token(self, interaction: discord.Interaction, symbol: str):
        """Réinitialise toutes les données d'un token et le passe en INCOMPLETE."""
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
                await interaction.response.send_message(
                    f"❌ Token **{symbol_clean}** non trouvé dans la base de données."
                )
                return

            await interaction.response.send_message(
                f"⚠️ **ATTENTION** : Cette action va réinitialiser toutes les données de **{token.cashtag}**.\n"
                f"📝 Le token sera marqué INCOMPLETE et toutes ses données de monitoring seront perdues.\n"
                f"💡 Utilisez `/confirm_reset {symbol_clean}` pour confirmer."
            )

        except Exception as e:
            logger.error(f"❌ Erreur reset_token: {e}", exc_info=True)
            await interaction.response.send_message(
                f"❌ Une erreur est survenue : {str(e)}"
            )

    @app_commands.command(
        name="confirm_reset", description="Confirme la réinitialisation d'un token"
    )
    @app_commands.describe(symbol="Symbole du token à réinitialiser")
    async def confirm_reset_token(self, interaction: discord.Interaction, symbol: str):
        """Confirme la réinitialisation d'un token."""
        await interaction.response.defer()

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

            await interaction.followup.send(
                f"⏳ Réinitialisation de **{token.cashtag}** en cours..."
            )

            if self.scheduler:
                job_id = f"monitor_reg_{token.id}"
                if self.scheduler.get_job(job_id):
                    self.scheduler.remove_job(job_id)
                    logger.info(f"🧹 Job de monitoring {job_id} supprimé")

            deleted_stats = {
                "social_metrics": SocialMetric.delete()
                .where(SocialMetric.token == token)
                .execute(),
                "raw_tweets": RawTweet.delete()
                .where(RawTweet.token == token)
                .execute(),
                "price_metrics": PriceMetric.delete()
                .where(PriceMetric.token == token)
                .execute(),
                "signal_logs": SignalLog.delete()
                .where(SignalLog.token == token)
                .execute(),
                "followers": ProjectFollowers.delete()
                .where(ProjectFollowers.token == token)
                .execute(),
            }

            token.status = "INCOMPLETE"
            token.chain_id = None
            token.pair_address = None
            token.primary_price_source = None
            token.primary_chain_id = None
            token.primary_pair_address = None
            token.primary_volume_24h = None
            token.total_volume_24h = None
            token.contract_addresses = None
            token.official_followers = None
            token.save()

            embed = discord.Embed(
                title=f"✅ Token {token.cashtag} réinitialisé",
                color=discord.Color.orange(),
            )
            embed.add_field(
                name="📊 Social Metrics supprimés",
                value=str(deleted_stats["social_metrics"]),
                inline=True,
            )
            embed.add_field(
                name="🐦 Tweets supprimés",
                value=str(deleted_stats["raw_tweets"]),
                inline=True,
            )
            embed.add_field(
                name="💰 Prix supprimés",
                value=str(deleted_stats["price_metrics"]),
                inline=True,
            )
            embed.add_field(
                name="🚨 Signaux supprimés",
                value=str(deleted_stats["signal_logs"]),
                inline=True,
            )
            embed.add_field(
                name="👥 Followers supprimés",
                value=str(deleted_stats["followers"]),
                inline=True,
            )
            embed.add_field(name="📝 Nouveau statut", value="INCOMPLETE", inline=True)
            embed.set_footer(
                text="💡 Utilisez /resolve pour récupérer les infos de base du token"
            )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"❌ Erreur confirm_reset_token: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Une erreur est survenue : {str(e)}")


async def setup(bot: commands.Bot):
    """Setup cog with bot"""
    await bot.add_cog(TokenCommands(bot))
