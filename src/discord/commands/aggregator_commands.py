"""
Aggregator Commands - Commandes de contrôle du MetricAggregator.

Commandes disponibles:
- /aggregate <identifier> <mode> - Lance l'agrégation pour un token
- /aggregate_all <mode> - Lance l'agrégation pour tous les tokens actifs
- /pipeline_status <identifier> - Affiche l'état du pipeline

"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from discord.ext import commands

import discord
from discord import app_commands

from ...analysis.metric_aggregator import MetricAggregator
from ...database.models import Token
from .utils import resolve_token_identifier

logger = logging.getLogger(__name__)

# Thread pool dédié pour les opérations longues (évite de bloquer l'event loop)
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="aggregator")


class AggregatorCommands(commands.Cog):
    """Commandes de gestion du MetricAggregator"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.aggregator = MetricAggregator()

    @app_commands.command(
        name="aggregate", description="Lance l'agrégation des métriques pour un token"
    )
    @app_commands.describe(
        identifier="Symbol ($TURBO), cashtag ou nom du token",
        mode="Mode d'agrégation: 'full' (backfill 7j) ou 'incremental' (gaps uniquement)",
        force_social="Force le recalcul des social_metrics (même créneaux existants)",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(
                name="Incremental (gaps uniquement)", value="incremental"
            ),
            app_commands.Choice(name="Full (backfill 7 jours)", value="full"),
        ]
    )
    async def aggregate(
        self,
        interaction: discord.Interaction,
        identifier: str,
        mode: str = "incremental",
        force_social: bool = False,
    ):
        """
        Lance l'agrégation des métriques pour un token spécifique.

        Args:
            identifier: Symbol, cashtag ou nom du token
            mode: 'full' (backfill complet) ou 'incremental' (gaps uniquement)
        """
        # Tenter de defer l'interaction
        # Note: Discord peut retry la commande, causant diverses erreurs
        # On continue dans la plupart des cas car l'interaction a probablement déjà été traitée
        defer_success = False
        try:
            await interaction.response.defer()
            defer_success = True
            logger.info(f"✅ Interaction defer réussie pour /aggregate {identifier}")
        except discord.errors.HTTPException as e:
            # Erreur 40060 = already acknowledged (retry Discord, c'est OK)
            # Erreur 10062 = unknown interaction (retry Discord, c'est OK aussi)
            if e.code in [40060, 10062]:
                logger.warning(
                    f"⚠️ Erreur Discord {e.code} pour /aggregate {identifier} - on continue quand même (retry Discord)"
                )
                defer_success = (
                    True  # Continuer, l'interaction a probablement déjà été traitée
                )
            else:
                logger.error(f"❌ Erreur HTTP defer /aggregate: {e}")
        except discord.errors.NotFound:
            # Interaction expirée - mais si c'est un retry, l'interaction a peut-être déjà été traitée
            # On log mais on essaie quand même de continuer avec followup
            logger.warning(
                f"⚠️ Interaction NotFound pour /aggregate {identifier} - tentative de continuer avec followup"
            )
            defer_success = True  # Tenter quand même, on utilisera followup.send()
        except Exception as e:
            logger.error(f"❌ Erreur defer /aggregate: {e}", exc_info=True)

        if not defer_success:
            logger.error(
                f"❌ Impossible de defer l'interaction pour /aggregate {identifier}, abandon"
            )
            return

        try:
            # Résoudre l'identifiant
            token = resolve_token_identifier(identifier)
            if not token:
                await interaction.followup.send(
                    f"❌ Token non trouvé: `{identifier}`\n"
                    f"Vérifiez le symbol, cashtag ou utilisez `/list`"
                )
                return

            # Créer embed de démarrage
            force_text = " | 🔄 Force Social" if force_social else ""
            embed = discord.Embed(
                title=f"🔄 Agrégation Métriques: {token.cashtag}",
                description=f"Mode: **{mode.upper()}**{force_text}",
                color=discord.Color.blue(),
            )
            embed.add_field(name="Status", value="⏳ En cours...", inline=False)

            await interaction.followup.send(embed=embed)

            # Définir fonction de travail (s'exécute en arrière-plan)
            def aggregate_work():
                import sys

                print(
                    f"🔍 [THREAD] aggregate_work() DÉMARRE pour {token.cashtag} mode={mode} force_social={force_social}",
                    flush=True,
                )
                sys.stdout.flush()
                logger.info(
                    f"🔍 [THREAD] aggregate_work() DÉMARRE pour {token.cashtag} mode={mode} force_social={force_social}"
                )
                try:
                    if mode == "full":
                        print("🔍 [THREAD] Appel orchestrate_token_full...", flush=True)
                        result = self.aggregator.orchestrate_token_full(
                            token, days_back=7, force_social=force_social
                        )
                    else:
                        print(
                            "🔍 [THREAD] Appel orchestrate_token_incremental...",
                            flush=True,
                        )
                        result = self.aggregator.orchestrate_token_incremental(
                            token, force_social=force_social
                        )
                    print(
                        f"🔍 [THREAD] Résultat obtenu: {result.get('status', 'UNKNOWN')}",
                        flush=True,
                    )
                    sys.stdout.flush()
                    return result
                except Exception as e:
                    print(f"❌ [THREAD] Exception: {e}", flush=True)
                    sys.stdout.flush()
                    logger.error(f"Erreur agrégation thread: {e}", exc_info=True)
                    return {"status": "FAILED", "error": str(e)}

            # Exécuter dans le thread pool dédié (non-bloquant, évite contention GIL)
            print(f"🔍 [MAIN] Avant run_in_executor() pour {token.cashtag}", flush=True)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(_executor, aggregate_work)
            print(
                f"🔍 [MAIN] Après run_in_executor(), résultat: {result.get('status', 'UNKNOWN')}",
                flush=True,
            )

            # Créer embed de résultat
            result_embed = self._create_aggregate_result_embed(token, mode, result)
            await interaction.followup.send(embed=result_embed)

        except Exception as e:
            logger.error(f"Erreur /aggregate: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Erreur lors de l'agrégation: {str(e)}")

    @app_commands.command(
        name="aggregate_all",
        description="Lance l'agrégation pour tous les tokens actifs",
    )
    @app_commands.describe(
        mode="Mode d'agrégation: 'full' (backfill 7j) ou 'incremental' (gaps uniquement)"
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(
                name="Incremental (gaps uniquement)", value="incremental"
            ),
            app_commands.Choice(name="Full (backfill 7 jours)", value="full"),
        ]
    )
    async def aggregate_all(
        self, interaction: discord.Interaction, mode: str = "incremental"
    ):
        """
        Lance l'agrégation pour tous les tokens actifs.

        Args:
            mode: 'full' ou 'incremental'
        """
        try:
            await interaction.response.defer()
        except discord.errors.NotFound:
            logger.error("❌ Interaction expirée pour /aggregate_all")
            return
        except Exception as e:
            logger.error(f"❌ Erreur defer /aggregate_all: {e}")
            return

        try:
            # Compter les tokens actifs
            active_count = (
                Token.select()
                .where(Token.status.in_(["REGULAR", "AGGRESSIVE", "CROISIERE"]))
                .count()
            )

            if active_count == 0:
                await interaction.followup.send("📭 Aucun token actif à agréger.")
                return

            # Créer embed de démarrage
            embed = discord.Embed(
                title=f"🌟 Agrégation Globale: {active_count} Tokens",
                description=f"Mode: **{mode.upper()}**",
                color=discord.Color.blue(),
            )
            embed.add_field(name="Status", value="⏳ En cours...", inline=False)

            await interaction.followup.send(embed=embed)

            # Définir fonction de travail (s'exécute en arrière-plan)
            def aggregate_all_work():
                import sys

                print(
                    f"🔍 [THREAD] aggregate_all_work() DÉMARRE mode={mode}", flush=True
                )
                sys.stdout.flush()
                logger.info(f"🔍 [THREAD] aggregate_all_work() DÉMARRE mode={mode}")
                try:
                    print("🔍 [THREAD] Appel orchestrate_all_tokens...", flush=True)
                    result = self.aggregator.orchestrate_all_tokens(mode=mode)
                    print(
                        f"🔍 [THREAD] Résultat: {result.get('success_count', 0)} succès",
                        flush=True,
                    )
                    sys.stdout.flush()
                    return result
                except Exception as e:
                    print(f"❌ [THREAD] Exception: {e}", flush=True)
                    sys.stdout.flush()
                    logger.error(
                        f"Erreur agrégation globale thread: {e}", exc_info=True
                    )
                    return {
                        "total_tokens": 0,
                        "success_count": 0,
                        "partial_count": 0,
                        "failed_count": 1,
                        "error": str(e),
                    }

            # Exécuter dans le thread pool dédié (non-bloquant, évite contention GIL)
            print("🔍 [MAIN] Avant run_in_executor() aggregate_all", flush=True)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(_executor, aggregate_all_work)
            print("🔍 [MAIN] Après run_in_executor() aggregate_all", flush=True)

            # Créer embed de résultat
            result_embed = self._create_aggregate_all_result_embed(mode, result)
            await interaction.followup.send(embed=result_embed)

        except Exception as e:
            logger.error(f"Erreur /aggregate_all: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Erreur lors de l'agrégation globale: {str(e)}"
            )

    @app_commands.command(
        name="pipeline_status", description="Affiche l'état du pipeline de métriques"
    )
    @app_commands.describe(
        identifier="(Optionnel) Symbol, cashtag ou nom du token. Si omis, affiche vue globale"
    )
    async def pipeline_status(
        self, interaction: discord.Interaction, identifier: str | None = None
    ):
        """
        Affiche l'état du pipeline de métriques.

        Si identifier fourni: statut détaillé pour un token
        Sinon: vue d'ensemble de tous les tokens actifs
        """
        try:
            await interaction.response.defer()
        except discord.errors.NotFound:
            logger.error("❌ Interaction expirée pour /pipeline_status")
            return
        except Exception as e:
            logger.error(f"❌ Erreur defer /pipeline_status: {e}")
            return

        try:
            if identifier:
                # Statut pour un token spécifique
                token = resolve_token_identifier(identifier)
                if not token:
                    await interaction.followup.send(
                        f"❌ Token non trouvé: `{identifier}`\n"
                        f"Vérifiez le symbol, cashtag ou utilisez `/list`"
                    )
                    return

                embed = self._create_pipeline_status_embed(token)
                await interaction.followup.send(embed=embed)

            else:
                # Vue d'ensemble globale
                active_tokens = Token.select().where(
                    Token.status.in_(["REGULAR", "AGGRESSIVE", "CROISIERE"])
                )

                if active_tokens.count() == 0:
                    await interaction.followup.send(
                        "📭 Aucun token actif en surveillance."
                    )
                    return

                embed = self._create_pipeline_overview_embed(active_tokens)
                await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Erreur /pipeline_status: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Erreur lors de la vérification: {str(e)}"
            )

    # ========================================================================
    # MÉTHODES PRIVÉES - CRÉATION EMBEDS
    # ========================================================================

    def _create_aggregate_result_embed(
        self, token: Token, mode: str, result: dict
    ) -> discord.Embed:
        """Crée l'embed de résultat d'agrégation pour un token."""

        status = result.get("status", "UNKNOWN")

        # Couleur selon statut
        if status == "SUCCESS" or status == "NO_ACTION_NEEDED":
            color = discord.Color.green()
            icon = "✅"
        elif status == "PARTIAL":
            color = discord.Color.orange()
            icon = "⚠️"
        else:
            color = discord.Color.red()
            icon = "❌"

        embed = discord.Embed(
            title=f"{icon} Agrégation: {token.cashtag}",
            description=f"Mode: **{mode.upper()}** | Résultat: **{status}**",
            color=color,
        )

        # Ajouter résultats par stage (mode full)
        if "stages" in result:
            stages = result["stages"]

            for stage_name, stage_result in stages.items():
                stage_status = stage_result.get("status", "UNKNOWN")
                stage_icon = (
                    "✅"
                    if stage_status == "SUCCESS" or stage_status == "NO_ACTION_NEEDED"
                    else "⚠️"
                    if stage_status == "PARTIAL"
                    else "❌"
                )

                # Format des détails selon le stage
                details = []
                if stage_name == "stage_1" and "created" in stage_result:
                    details.append(f"Créés: {stage_result['created']}")
                    details.append(f"Total: {stage_result.get('count_after', 'N/A')}")
                elif stage_name == "stage_2" and "gaps_filled" in stage_result:
                    details.append(f"Gaps comblés: {stage_result['gaps_filled']}")
                elif stage_name == "stage_3" and "backfill_stats" in stage_result:
                    stats = stage_result["backfill_stats"]
                    details.append(f"FGI créés: {stats.get('created', 0)}")
                elif stage_name == "stage_4" and "created" in stage_result:
                    details.append(f"Créés: {stage_result['created']}")
                elif stage_name == "stage_5" and "zscores_calculated" in stage_result:
                    details.append(f"Calculés: {stage_result['zscores_calculated']}")

                stage_label = {
                    "stage_1": "Social Metrics",
                    "stage_2": "Prix (Sync)",
                    "stage_3": "Macro",
                    "stage_4": "Signal Metrics",
                    "stage_5": "Z-Scores",
                }.get(stage_name, stage_name)

                embed.add_field(
                    name=f"{stage_icon} {stage_label}",
                    value="\n".join(details)
                    if details
                    else stage_result.get("message", stage_status),
                    inline=True,
                )

        # Ajouter résultats mode incremental
        elif "stages_executed" in result:
            embed.add_field(
                name="Stages Exécutés",
                value=str(result["stages_executed"]),
                inline=True,
            )

            if "results" in result and result["results"]:
                for stage_name, stage_result in result["results"].items():
                    stage_status = stage_result.get("status", "UNKNOWN")
                    stage_icon = (
                        "✅" if stage_status in ["SUCCESS", "NO_ACTION_NEEDED"] else "⚠️"
                    )

                    embed.add_field(
                        name=f"{stage_icon} {stage_name}",
                        value=stage_result.get("message", stage_status),
                        inline=True,
                    )

        # Ajouter validation si disponible
        if "validation" in result:
            validation = result["validation"]
            is_ready = validation["is_ready"]

            ready_icon = "✅" if is_ready else "❌"
            ready_text = "PRÊT" if is_ready else "NON PRÊT"

            embed.add_field(
                name=f"{ready_icon} Pipeline", value=ready_text, inline=False
            )

            if validation["blocking_issues"]:
                embed.add_field(
                    name="Problèmes Bloquants",
                    value="\n".join(
                        f"• {issue}" for issue in validation["blocking_issues"]
                    ),
                    inline=False,
                )

        embed.timestamp = discord.utils.utcnow()
        return embed

    def _create_aggregate_all_result_embed(
        self, mode: str, result: dict
    ) -> discord.Embed:
        """Crée l'embed de résultat d'agrégation globale."""

        total = result.get("total_tokens", 0)
        success = result.get("success_count", 0)
        partial = result.get("partial_count", 0)
        failed = result.get("failed_count", 0)

        # Couleur selon taux de succès
        if failed == 0:
            color = discord.Color.green()
            icon = "✅"
        elif success + partial >= total // 2:
            color = discord.Color.orange()
            icon = "⚠️"
        else:
            color = discord.Color.red()
            icon = "❌"

        embed = discord.Embed(
            title=f"{icon} Agrégation Globale Terminée",
            description=f"Mode: **{mode.upper()}** | Total: **{total} tokens**",
            color=color,
        )

        embed.add_field(name="✅ Succès", value=str(success), inline=True)

        embed.add_field(name="⚠️ Partiels", value=str(partial), inline=True)

        embed.add_field(name="❌ Échecs", value=str(failed), inline=True)

        # Ajouter détails des échecs si présents
        if failed > 0 and "results" in result:
            failed_tokens = [
                token
                for token, res in result["results"].items()
                if res.get("status") == "FAILED"
            ]

            if failed_tokens:
                # Limiter à 10 pour éviter dépassement
                display_failed = failed_tokens[:10]
                remaining = len(failed_tokens) - 10

                failed_text = ", ".join(display_failed)
                if remaining > 0:
                    failed_text += f"\n... +{remaining} autres"

                embed.add_field(
                    name="❌ Tokens Échoués", value=failed_text, inline=False
                )

        embed.timestamp = discord.utils.utcnow()
        return embed

    def _create_pipeline_status_embed(self, token: Token) -> discord.Embed:
        """Crée l'embed de statut pipeline pour un token."""

        # Détecter gaps
        gaps = self.aggregator.detect_pipeline_gaps(token)

        # Valider pipeline
        validation = self.aggregator.validate_pipeline_readiness(token)

        # Couleur selon prêt
        color = (
            discord.Color.green() if validation["is_ready"] else discord.Color.orange()
        )

        embed = discord.Embed(title=f"📊 Pipeline Status: {token.cashtag}", color=color)

        # Social Metrics
        social = gaps["social_metrics"]
        social_icon = "✅" if not social["has_gaps"] else "⚠️"
        embed.add_field(
            name=f"{social_icon} Social Metrics", value=social["message"], inline=True
        )

        # Price Z-Scores
        price = gaps["price_zscores"]
        price_icon = "✅" if not price["has_gaps"] else "⚠️"
        embed.add_field(
            name=f"{price_icon} Price Z-Scores", value=price["message"], inline=True
        )

        # Signal Metrics
        signal = gaps["signal_metrics"]
        signal_icon = "✅" if not signal["has_gaps"] else "⚠️"
        embed.add_field(
            name=f"{signal_icon} Signal Metrics", value=signal["message"], inline=True
        )

        # Statut global
        ready_icon = "✅" if validation["is_ready"] else "❌"
        ready_text = "PRÊT" if validation["is_ready"] else "NON PRÊT"

        embed.add_field(name=f"{ready_icon} Pipeline", value=ready_text, inline=False)

        # Problèmes bloquants
        if validation["blocking_issues"]:
            embed.add_field(
                name="⚠️ Problèmes Bloquants",
                value="\n".join(
                    f"• {issue}" for issue in validation["blocking_issues"]
                ),
                inline=False,
            )

        embed.timestamp = discord.utils.utcnow()
        return embed

    def _create_pipeline_overview_embed(self, active_tokens) -> discord.Embed:
        """Crée l'embed de vue d'ensemble globale."""

        embed = discord.Embed(
            title="📊 Pipeline Overview: Tous Tokens Actifs", color=discord.Color.blue()
        )

        # Analyser chaque token
        ready_count = 0
        partial_count = 0
        not_ready_count = 0

        ready_tokens = []
        partial_tokens = []
        not_ready_tokens = []

        for token in active_tokens:
            validation = self.aggregator.validate_pipeline_readiness(token)

            if validation["is_ready"]:
                ready_count += 1
                ready_tokens.append(token.cashtag)
            elif any(validation["checks"].values()):
                partial_count += 1
                partial_tokens.append(token.cashtag)
            else:
                not_ready_count += 1
                not_ready_tokens.append(token.cashtag)

        # Résumé
        total = active_tokens.count()
        embed.add_field(name="📈 Total Tokens", value=str(total), inline=True)

        embed.add_field(
            name="✅ Prêts",
            value=f"{ready_count} ({ready_count / total * 100:.0f}%)"
            if total > 0
            else "0",
            inline=True,
        )

        embed.add_field(
            name="⚠️ Partiels",
            value=f"{partial_count} ({partial_count / total * 100:.0f}%)"
            if total > 0
            else "0",
            inline=True,
        )

        embed.add_field(
            name="❌ Non Prêts",
            value=f"{not_ready_count} ({not_ready_count / total * 100:.0f}%)"
            if total > 0
            else "0",
            inline=True,
        )

        # Détails tokens non prêts (limité à 10)
        if not_ready_tokens:
            display_not_ready = not_ready_tokens[:10]
            remaining = len(not_ready_tokens) - 10

            not_ready_text = ", ".join(display_not_ready)
            if remaining > 0:
                not_ready_text += f"\n... +{remaining} autres"

            embed.add_field(
                name="❌ Tokens Non Prêts", value=not_ready_text, inline=False
            )

        embed.timestamp = discord.utils.utcnow()
        return embed


async def setup(bot: commands.Bot):
    """Setup function pour charger le Cog"""
    await bot.add_cog(AggregatorCommands(bot))
