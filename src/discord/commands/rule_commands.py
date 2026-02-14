"""TwitterAPI.io Rule Management Commands"""

import logging
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from ...database.models import Token, TwitterAPIRule
from ...twitter.twitterapi_client import (
    TwitterAPIClient,
    build_filter_query,
    build_webhook_url,
)

logger = logging.getLogger(__name__)


class RuleCommands(commands.Cog):
    """Commandes de gestion des règles TwitterAPI.io"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.twitter_client = TwitterAPIClient()

    # Créer un groupe de commandes /webhook
    webhook_group = app_commands.Group(
        name="webhook", description="Gestion des règles TwitterAPI.io"
    )

    @webhook_group.command(
        name="create",
        description="Crée et active une règle de monitoring pour un token",
    )
    @app_commands.describe(
        symbol="Symbole du token (ex: PEPE, JELLYJELLY)",
        interval_seconds="Fréquence de vérification en secondes (défaut: 300 = 5min)",
    )
    async def webhook_create(
        self,
        interaction: discord.Interaction,
        symbol: str,
        interval_seconds: int = 300,
    ):
        """
        Crée et active une règle de monitoring en temps réel pour un token.

        Exemple:
            /webhook create PEPE           → Monitoring standard (5min)
            /webhook create JELLYJELLY 60  → Monitoring rapide (60s)
        """
        await interaction.response.defer()

        try:
            # 1. Vérifier que le token existe en DB
            token = Token.get(Token.symbol == symbol.upper())
        except Token.DoesNotExist:
            await interaction.followup.send(
                f"❌ Token **{symbol}** non trouvé en base de données.\n"
                f"💡 Utilisez `/add_token {symbol}` d'abord."
            )
            return

        # 2. Vérifier qu'il n'existe pas déjà une règle active
        existing_rule = (
            TwitterAPIRule.select()
            .where(
                (TwitterAPIRule.token == token) & (TwitterAPIRule.is_active == True)
            )
            .first()
        )

        if existing_rule:
            await interaction.followup.send(
                f"⚠️ Une règle active existe déjà pour **{token.cashtag}** !\n"
                f"Rule ID: `{existing_rule.rule_id}`\n"
                f"Interval: {existing_rule.interval_seconds}s\n\n"
                f"💡 Utilisez `/webhook stop {symbol}` pour désactiver ou `/webhook del {symbol}` pour supprimer."
            )
            return

        # 3. Construire la requête de filtrage standard : $SYMBOL -is:retweet
        filter_value = build_filter_query(token)

        # 4. Construire l'URL du webhook
        webhook_url = build_webhook_url(token.symbol)

        # 5. Créer le tag de la règle
        tag = f"monitoring_{token.symbol}"

        # 6. Créer et activer la règle via TwitterAPI.io
        await interaction.followup.send(
            f"⏳ Création de la règle pour **{token.cashtag}**...\n"
            f"Requête: `{filter_value}`\n"
            f"Interval: {interval_seconds}s"
        )

        try:
            result = await self.twitter_client.create_and_activate_rule(
                tag=tag,
                filter_value=filter_value,
                webhook_url=webhook_url,
                interval_seconds=interval_seconds,
            )

            # 7. Enregistrer la règle en DB
            rule = TwitterAPIRule.create(
                token=token,
                rule_id=result["rule_id"],
                tag=tag,
                filter_value=filter_value,
                webhook_url=webhook_url,
                interval_seconds=interval_seconds,
                is_active=True,
                activated_at=datetime.now(),
            )

            # 8. Créer un embed de confirmation
            embed = discord.Embed(
                title="✅ Règle webhook créée et activée !",
                color=discord.Color.green(),
            )
            embed.add_field(name="Token", value=token.cashtag, inline=True)
            embed.add_field(name="Rule ID", value=result["rule_id"], inline=True)
            embed.add_field(
                name="Interval", value=f"{interval_seconds}s", inline=True
            )
            embed.add_field(name="Requête", value=f"`{filter_value}`", inline=False)
            embed.add_field(
                name="Statut",
                value="🟢 **ACTIVE** - Les tweets seront envoyés en temps réel",
                inline=False,
            )

            await interaction.followup.send(embed=embed)
            logger.info(
                f"✅ Règle créée: {tag} | Rule ID: {result['rule_id']} | Token: {token.cashtag}"
            )

        except Exception as e:
            logger.error(f"❌ Erreur création règle: {str(e)}", exc_info=True)
            await interaction.followup.send(
                f"❌ Erreur lors de la création de la règle:\n```{str(e)}```"
            )

    @webhook_group.command(
        name="list", description="Liste toutes les règles webhook actives"
    )
    async def webhook_list(self, interaction: discord.Interaction):
        """
        Affiche toutes les règles de monitoring actives et leurs statistiques.
        """
        await interaction.response.defer()

        try:
            # 1. Récupérer les règles depuis la DB
            rules = list(
                TwitterAPIRule.select()
                .join(Token)
                .where(TwitterAPIRule.is_active == True)
                .order_by(Token.symbol)
            )

            if not rules:
                await interaction.followup.send(
                    "📭 Aucune règle active.\n💡 Utilisez `/webhook create <SYMBOL>` pour en créer une."
                )
                return

            # 2. Créer l'embed
            embed = discord.Embed(
                title=f"📋 Règles webhook actives ({len(rules)})",
                color=discord.Color.blue(),
            )

            for rule in rules:
                status_emoji = "🟢" if rule.is_active else "🔴"
                last_trigger = (
                    rule.last_triggered_at.strftime("%Y-%m-%d %H:%M")
                    if rule.last_triggered_at
                    else "Jamais"
                )

                field_value = (
                    f"**Rule ID:** `{rule.rule_id}`\n"
                    f"**Interval:** {rule.interval_seconds}s\n"
                    f"**Tweets reçus:** {rule.total_tweets_received}\n"
                    f"**Dernier trigger:** {last_trigger}\n"
                    f"**Requête:** `{rule.filter_value[:100]}...`"
                    if len(rule.filter_value) > 100
                    else f"**Requête:** `{rule.filter_value}`"
                )

                embed.add_field(
                    name=f"{status_emoji} {rule.token.cashtag}",
                    value=field_value,
                    inline=False,
                )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"❌ Erreur listing règles: {str(e)}", exc_info=True)
            await interaction.followup.send(
                f"❌ Erreur lors de la récupération des règles:\n```{str(e)}```"
            )

    @webhook_group.command(name="stop", description="Désactive une règle webhook")
    @app_commands.describe(symbol="Symbole du token (ex: PEPE)")
    async def webhook_stop(self, interaction: discord.Interaction, symbol: str):
        """
        Désactive une règle webhook (arrête la réception de tweets).
        """
        await interaction.response.defer()

        try:
            # 1. Trouver le token
            token = Token.get(Token.symbol == symbol.upper())
        except Token.DoesNotExist:
            await interaction.followup.send(f"❌ Token **{symbol}** non trouvé.")
            return

        # 2. Trouver la règle active
        rule = (
            TwitterAPIRule.select()
            .where(
                (TwitterAPIRule.token == token) & (TwitterAPIRule.is_active == True)
            )
            .first()
        )

        if not rule:
            await interaction.followup.send(
                f"⚠️ Aucune règle active pour **{token.cashtag}**."
            )
            return

        try:
            # 3. Désactiver la règle via TwitterAPI.io
            await self.twitter_client.deactivate_rule(
                rule_id=rule.rule_id, tag=rule.tag, filter_value=rule.filter_value
            )

            # 4. Mettre à jour la DB
            rule.is_active = False
            rule.deactivated_at = datetime.now()
            rule.save()

            await interaction.followup.send(
                f"⏸️ Règle webhook stoppée pour **{token.cashtag}**.\n"
                f"Tweets reçus au total: {rule.total_tweets_received}"
            )
            logger.info(f"⏸️ Règle stoppée: {rule.tag} | Token: {token.cashtag}")

        except Exception as e:
            logger.error(f"❌ Erreur stop règle: {str(e)}", exc_info=True)
            await interaction.followup.send(
                f"❌ Erreur lors de l'arrêt:\n```{str(e)}```"
            )

    @webhook_group.command(name="del", description="Supprime définitivement une règle")
    @app_commands.describe(symbol="Symbole du token (ex: PEPE)")
    async def webhook_del(self, interaction: discord.Interaction, symbol: str):
        """
        Supprime définitivement une règle (action irréversible).
        """
        await interaction.response.defer()

        try:
            # 1. Trouver le token
            token = Token.get(Token.symbol == symbol.upper())
        except Token.DoesNotExist:
            await interaction.followup.send(f"❌ Token **{symbol}** non trouvé.")
            return

        # 2. Trouver la règle (active ou inactive)
        rule = TwitterAPIRule.select().where(TwitterAPIRule.token == token).first()

        if not rule:
            await interaction.followup.send(
                f"⚠️ Aucune règle trouvée pour **{token.cashtag}**."
            )
            return

        try:
            # 3. Supprimer la règle via TwitterAPI.io (passer tous les paramètres)
            await self.twitter_client.delete_rule(
                rule_id=rule.rule_id,
                tag=rule.tag,
                filter_value=rule.filter_value,
            )

            # 4. Supprimer de la DB
            rule_tag = rule.tag
            total_tweets = rule.total_tweets_received
            rule.delete_instance()

            await interaction.followup.send(
                f"🗑️ Règle webhook supprimée pour **{token.cashtag}**.\n"
                f"Tweets reçus au total: {total_tweets}"
            )
            logger.info(f"🗑️ Règle supprimée: {rule_tag} | Token: {token.cashtag}")

        except Exception as e:
            logger.error(f"❌ Erreur suppression règle: {str(e)}", exc_info=True)
            await interaction.followup.send(
                f"❌ Erreur lors de la suppression:\n```{str(e)}```"
            )


async def setup(bot: commands.Bot):
    """Setup function pour charger le cog"""
    await bot.add_cog(RuleCommands(bot))
