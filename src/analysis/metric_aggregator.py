"""
Metric Aggregator - Orchestrateur de transformation des données brutes en métriques.
Gère la synchronisation temporelle et le backfill automatique.

Architecture:
    Stage 1: raw_tweets → social_metrics
    Stage 2: synchronisation price_metrics (backfill si gaps)
    Stage 3: macro_metrics (vérification + backfill FGI)
    Stage 4: social + price + macro → signal_metrics
    Stage 5: calcul z-scores historiques (via zscore_calc)

Modes:
    - FULL: Backfill complet (7 jours) pour nouveaux tokens
    - INCREMENTAL: Comble uniquement les gaps détectés
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from peewee import fn

from ..database.models import (
    MacroMetric,
    PriceMetric,
    SignalMetric,
    SocialMetric,
    Token,
)
from ..scrapers.geckoterminal_worker import fetch_ohlcv_history
from .macro_backfill_service import MacroBackfillService
from .price_metric_generator import ensure_price_zscores
from .signal_metric_generator import ensure_signal_metrics
from .social_metric_generator import count_social_metrics, ensure_social_metrics
from .zprice_calc import compute_price_zscore as calculate_z_price

# Import Z-Score calculators
from .zsocial_calc import compute_social_zscore as calculate_z_social
from .zvol_calc import compute_zvol as calculate_z_vol

logger = logging.getLogger(__name__)


class MetricAggregator:
    """
    Orchestrateur principal pour l'agrégation et le backfill des métriques.

    Responsabilités:
    - Coordonner les 5 stages du pipeline de données
    - Détecter et combler les gaps automatiquement
    - Assurer la synchronisation temporelle (social ↔ price ↔ macro)
    - Valider la prêt du pipeline pour calculs z-score temps réel
    """

    def __init__(self):
        """Initialise l'orchestrateur."""
        self.logger = logger
        self.macro_service = MacroBackfillService()

        # Seuils de validation
        self.MIN_SOCIAL_METRICS = 20  # Minimum pour baseline
        self.MIN_PRICE_COVERAGE = 0.90  # 90% des prix doivent avoir z-scores
        self.MIN_SIGNAL_COVERAGE = (
            0.80  # 80% des social_metrics doivent avoir signal_metrics
        )
        self.MIN_MACRO_COVERAGE = 0.80  # 80% FGI requis

    # ========================================================================
    # STAGE 1: SOCIAL METRICS
    # ========================================================================

    def stage_1_social_metrics(
        self, token: Token, interval_minutes: int = 60, force: bool = False
    ) -> dict[str, Any]:
        """
        Stage 1: Convertit raw_tweets → social_metrics.

        Appelle social_metric_generator.ensure_social_metrics() qui:
        - Détecte nouveaux raw_tweets
        - Agrège en fenêtres horaires (1h par défaut)
        - Calcule social_volume et social_density
        - Crée social_metrics (sans z-scores)

        Args:
            token: Token à traiter
            interval_minutes: Intervalle d'agrégation (60 = 1h)
            force: Force le recalcul (même créneaux existants)

        Returns:
            Dict contenant:
                - status: 'SUCCESS', 'NO_ACTION_NEEDED', ou 'FAILED'
                - message: Description du résultat
                - count_before: Nombre de métriques avant
                - count_after: Nombre de métriques après
        """
        if force:
            self.logger.info(f"📊 [Stage 1] Social Metrics: {token.cashtag} [FORCE]")
        else:
            self.logger.info(f"📊 [Stage 1] Social Metrics: {token.cashtag}")

        try:
            # Compter avant
            count_before = count_social_metrics(token, days_back=7)

            # Appeler le générateur
            success = ensure_social_metrics(
                token, interval_minutes=interval_minutes, force=force
            )

            # Compter après
            count_after = count_social_metrics(token, days_back=7)

            created = count_after - count_before

            if success:
                if created > 0:
                    self.logger.info(
                        f"  ✅ {created} social_metrics créés ({count_after} total)"
                    )
                    return {
                        "status": "SUCCESS",
                        "message": f"{created} social_metrics créés",
                        "count_before": count_before,
                        "count_after": count_after,
                        "created": created,
                    }
                self.logger.info("  ✅ Aucune nouvelle métrique (déjà à jour)")
                return {
                    "status": "NO_ACTION_NEEDED",
                    "message": "Social metrics déjà à jour",
                    "count_before": count_before,
                    "count_after": count_after,
                    "created": 0,
                }
            self.logger.warning("  ⚠️  Données insuffisantes pour baseline")
            return {
                "status": "PARTIAL",
                "message": "Données insuffisantes pour baseline",
                "count_before": count_before,
                "count_after": count_after,
                "created": created,
            }

        except Exception as e:
            self.logger.error(f"  ❌ Stage 1 échoué: {e}")
            return {"status": "FAILED", "message": str(e), "error": str(e)}

    # ========================================================================
    # STAGE 2: SYNCHRONISATION PRIX (CRITIQUE)
    # ========================================================================

    def detect_new_social_metrics_range(
        self, token: Token, hours_back: int = 24
    ) -> tuple[datetime, datetime] | None:
        """
        Détecte la plage temporelle des social_metrics récents.

        Cette fonction identifie les métriques sociales créées récemment
        pour déterminer quelle période de prix doit être synchronisée.

        Args:
            token: Token à analyser
            hours_back: Nombre d'heures à vérifier en arrière

        Returns:
            Tuple (start_time, end_time) ou None si aucune métrique récente
        """
        cutoff_time = datetime.now() - timedelta(hours=hours_back)

        try:
            recent_metrics = (
                SocialMetric.select(SocialMetric.timestamp)
                .where(
                    (SocialMetric.token == token)
                    & (SocialMetric.timestamp >= cutoff_time)
                )
                .order_by(SocialMetric.timestamp.asc())
            )

            if not recent_metrics.exists():
                return None

            # Déterminer plage min-max
            timestamps = [m.timestamp for m in recent_metrics]
            start_time = min(timestamps)
            end_time = max(timestamps)

            self.logger.debug(f"  📅 Plage social_metrics: {start_time} → {end_time}")
            return (start_time, end_time)

        except Exception as e:
            self.logger.error(f"  ❌ Erreur détection plage: {e}")
            return None

    def detect_price_metrics_gaps(
        self,
        token: Token,
        start_time: datetime,
        end_time: datetime,
        resolution: str = "1h",
    ) -> list[datetime]:
        """
        Détecte les gaps dans price_metrics pour une plage temporelle.

        Args:
            token: Token à vérifier
            start_time: Début de la plage
            end_time: Fin de la plage
            resolution: Résolution des bougies ('1h')

        Returns:
            List[datetime]: Liste des timestamps manquants
        """
        self.logger.debug(f"  🔍 Vérification price_metrics {start_time} → {end_time}")

        # Générer tous les timestamps attendus (horaires)
        expected_timestamps = []
        current = start_time.replace(minute=0, second=0, microsecond=0)
        end = end_time.replace(minute=0, second=0, microsecond=0)

        while current <= end:
            expected_timestamps.append(current)
            current += timedelta(hours=1)

        # Récupérer les timestamps existants dans DB
        try:
            existing_metrics = PriceMetric.select(PriceMetric.timestamp).where(
                (PriceMetric.token == token)
                & (PriceMetric.timestamp >= start_time)
                & (PriceMetric.timestamp <= end_time)
                & (PriceMetric.resolution == resolution)
            )

            existing_timestamps = {
                m.timestamp.replace(minute=0, second=0, microsecond=0)
                for m in existing_metrics
            }

        except Exception as e:
            self.logger.error(f"  ❌ Erreur lecture price_metrics: {e}")
            existing_timestamps = set()

        # Calculer les gaps
        gaps = [ts for ts in expected_timestamps if ts not in existing_timestamps]

        if gaps:
            self.logger.warning(f"  ⚠️  {len(gaps)} gap(s) détectés dans price_metrics")
        else:
            self.logger.info("  ✅ Price_metrics complet sur la plage")

        return gaps

    def stage_2_synchronize_price_metrics(
        self, token: Token, hours_back: int = 24
    ) -> dict[str, Any]:
        """
        Stage 2: Synchronise price_metrics avec social_metrics.

        CRITIQUE: Assure que price_metrics couvre la même période que social_metrics.

        Processus:
        1. Détecte la plage temporelle des nouveaux social_metrics
        2. Vérifie la coverage price_metrics sur cette plage
        3. Si gaps détectés → backfill automatique via GeckoTerminal
        4. Calcule z_score_price via price_metric_generator

        Args:
            token: Token à traiter
            hours_back: Nombre d'heures à vérifier (défaut: 24h)

        Returns:
            Dict contenant:
                - status: 'SUCCESS', 'NO_ACTION_NEEDED', 'PARTIAL', ou 'FAILED'
                - gaps_detected: int - Nombre de gaps détectés
                - gaps_filled: int - Nombre de gaps comblés
                - time_range: Tuple (start, end) ou None
        """
        self.logger.info(f"💰 [Stage 2] Synchronisation Prix: {token.cashtag}")

        try:
            # 1. Détecter la plage temporelle des nouveaux social_metrics
            social_range = self.detect_new_social_metrics_range(
                token, hours_back=hours_back
            )

            if social_range is None:
                self.logger.info(
                    "  ✅ Aucun nouveau social_metric, skip synchronisation"
                )
                return {
                    "status": "NO_ACTION_NEEDED",
                    "message": "Aucun nouveau social_metric",
                    "gaps_detected": 0,
                    "gaps_filled": 0,
                    "time_range": None,
                }

            start_time, end_time = social_range

            # 2. Détecter les gaps price_metrics sur cette plage
            price_gaps = self.detect_price_metrics_gaps(
                token, start_time=start_time, end_time=end_time, resolution="1h"
            )

            if not price_gaps:
                self.logger.info("  ✅ Price_metrics complet, calcul z-scores...")

                # Calculer z-scores même si pas de gaps (peut manquer)
                success = ensure_price_zscores(token, resolution="1h", lookback_days=7)

                return {
                    "status": "SUCCESS",
                    "message": "Price metrics déjà synchronisés",
                    "gaps_detected": 0,
                    "gaps_filled": 0,
                    "time_range": (start_time, end_time),
                    "zscores_calculated": success,
                }

            # 3. Backfill automatique des prix
            self.logger.warning(
                f"  ⚠️  {len(price_gaps)} gap(s) détectés, "
                f"backfill {start_time.date()} → {end_time.date()}..."
            )

            # Calculer le nombre de jours à backfill
            days_back = (end_time - start_time).days + 1

            # Appeler le backfill via GeckoTerminal
            backfill_success = fetch_ohlcv_history(
                token=token, days_back=days_back, resolution="1h", use_api_manager=True
            )

            if not backfill_success:
                self.logger.error("  ❌ Backfill prix échoué")
                return {
                    "status": "FAILED",
                    "message": "Backfill prix échoué",
                    "gaps_detected": len(price_gaps),
                    "gaps_filled": 0,
                    "time_range": (start_time, end_time),
                }

            # 4. Calculer z-scores prix
            self.logger.info("  🔢 Calcul z-scores prix...")
            zscore_success = ensure_price_zscores(
                token, resolution="1h", lookback_days=7
            )

            return {
                "status": "SUCCESS",
                "message": f"{len(price_gaps)} gap(s) comblés",
                "gaps_detected": len(price_gaps),
                "gaps_filled": len(price_gaps),  # Assume backfill complet
                "time_range": (start_time, end_time),
                "zscores_calculated": zscore_success,
            }

        except Exception as e:
            self.logger.error(f"  ❌ Stage 2 échoué: {e}")
            return {
                "status": "FAILED",
                "message": str(e),
                "error": str(e),
                "gaps_detected": 0,
                "gaps_filled": 0,
            }

    # ========================================================================
    # STAGE 3: MACRO METRICS
    # ========================================================================

    def stage_3_macro_metrics(self, days_back: int = 10) -> dict[str, Any]:
        """
        Stage 3: Vérifie et comble les gaps macro_metrics (FGI, Volume Global).

        Appelle macro_backfill_service qui:
        - Détecte les gaps FGI sur N jours
        - Backfill via alternative.me API si nécessaire
        - Note: Global Volume historique nécessite API payante CoinGecko

        Args:
            days_back: Nombre de jours à vérifier (défaut: 10)

        Returns:
            Dict contenant résultat du macro_backfill_service
        """
        self.logger.info(f"🌍 [Stage 3] Macro Metrics (derniers {days_back} jours)")

        try:
            result = self.macro_service.backfill_all_macro(days_back=days_back)

            gaps = result["gaps_detected"]
            stats = result["backfill_stats"]

            if result["status"] == "NO_ACTION_NEEDED":
                self.logger.info(
                    f"  ✅ Macro metrics complets (coverage: {gaps['fgi_coverage']}%)"
                )
            elif result["status"] == "SUCCESS":
                self.logger.info(
                    f"  ✅ {stats['created'] + stats['updated']} FGI comblés "
                    f"(coverage: {gaps['fgi_coverage']}%)"
                )
            elif result["status"] == "PARTIAL":
                self.logger.warning(
                    f"  ⚠️  Backfill partiel: {stats['failed']} échecs "
                    f"(coverage: {gaps['fgi_coverage']}%)"
                )
            else:
                self.logger.error("  ❌ Backfill macro échoué")

            return result

        except Exception as e:
            self.logger.error(f"  ❌ Stage 3 échoué: {e}")
            return {"status": "FAILED", "message": str(e), "error": str(e)}

    # ========================================================================
    # STAGE 4: SIGNAL METRICS
    # ========================================================================

    def stage_4_signal_metrics(
        self, token: Token, days_back: int = 7
    ) -> dict[str, Any]:
        """
        Stage 4: Agrège social_metrics + price_metrics + macro_metrics → signal_metrics.

        Appelle signal_metric_generator.ensure_signal_metrics() qui:
        - JOIN social_metrics + price_metrics (±1h window)
        - Ajoute références macro_metrics (FGI)
        - Crée signal_metrics (sans z-scores finaux)

        Args:
            token: Token à traiter
            days_back: Nombre de jours à vérifier

        Returns:
            Dict contenant:
                - status: 'SUCCESS', 'NO_ACTION_NEEDED', ou 'FAILED'
                - message: Description du résultat
        """
        self.logger.info(f"📈 [Stage 4] Signal Metrics: {token.cashtag}")

        try:
            # Compter signal_metrics avant
            count_before = (
                SignalMetric.select().where(SignalMetric.token == token).count()
            )

            # Appeler le générateur
            success = ensure_signal_metrics(token, days_back=days_back)

            # Compter après
            count_after = (
                SignalMetric.select().where(SignalMetric.token == token).count()
            )

            created = count_after - count_before

            if success:
                if created > 0:
                    self.logger.info(
                        f"  ✅ {created} signal_metrics créés ({count_after} total)"
                    )
                else:
                    self.logger.info("  ✅ Signal metrics déjà à jour")

                return {
                    "status": "SUCCESS",
                    "message": f"{created} signal_metrics créés"
                    if created > 0
                    else "Déjà à jour",
                    "count_before": count_before,
                    "count_after": count_after,
                    "created": created,
                }
            self.logger.warning("  ⚠️  Génération signal_metrics partielle")
            return {
                "status": "PARTIAL",
                "message": "Génération partielle",
                "count_before": count_before,
                "count_after": count_after,
                "created": created,
            }

        except Exception as e:
            self.logger.error(f"  ❌ Stage 4 échoué: {e}")
            return {"status": "FAILED", "message": str(e), "error": str(e)}

    # ========================================================================
    # STAGE 5: CALCUL Z-SCORES HISTORIQUES
    # ========================================================================

    def get_signal_metrics_without_zscores(
        self, token: Token, days_back: int = 7
    ) -> list[SignalMetric]:
        """
        Récupère les signal_metrics qui n'ont pas encore de z-scores calculés.

        Args:
            token: Token à vérifier
            days_back: Nombre de jours en arrière

        Returns:
            List[SignalMetric]: Métriques sans z-scores
        """
        cutoff_date = datetime.now() - timedelta(days=days_back)

        try:
            # Chercher les signal_metrics avec z_score_social NULL ou 0 OU fgi_correction NULL
            metrics = (
                SignalMetric.select()
                .where(
                    (SignalMetric.token == token)
                    & (SignalMetric.timestamp >= cutoff_date)
                    & (
                        (SignalMetric.z_score_social.is_null())
                        | (SignalMetric.z_score_social == 0)
                        | (SignalMetric.fgi_correction.is_null())
                    )
                )
                .order_by(SignalMetric.timestamp.asc())
            )

            return list(metrics)

        except Exception as e:
            self.logger.error(f"  ❌ Erreur récupération signal_metrics: {e}")
            return []

    def stage_5_calculate_zscores(
        self, token: Token, days_back: int = 7
    ) -> dict[str, Any]:
        """
        Stage 5: Calcule les z-scores historiques pour signal_metrics.

        Appelle zscore_calc + sous-modules (zsocial_calc, zprice_calc, zvol_calc)
        pour chaque signal_metric sans z-scores.

        Processus:
        - Pour chaque signal_metric :
          - calculate_z_social(token, timestamp)
          - calculate_z_price(token, timestamp)
          - calculate_z_vol(timestamp)
        - Mise à jour signal_metrics avec z-scores
        - Calcul divergence (z_social - z_price)

        Args:
            token: Token à traiter
            days_back: Nombre de jours en arrière

        Returns:
            Dict contenant:
                - status: 'SUCCESS', 'NO_ACTION_NEEDED', ou 'FAILED'
                - zscores_calculated: int - Nombre de z-scores calculés
                - failed: int - Nombre d'échecs
        """
        self.logger.info(f"🔢 [Stage 5] Calcul Z-Scores: {token.cashtag}")

        try:
            # Récupérer les signal_metrics sans z-scores
            metrics_to_process = self.get_signal_metrics_without_zscores(
                token, days_back=days_back
            )

            if not metrics_to_process:
                self.logger.info("  ✅ Tous les z-scores déjà calculés")
                return {
                    "status": "NO_ACTION_NEEDED",
                    "message": "Z-scores déjà calculés",
                    "zscores_calculated": 0,
                    "failed": 0,
                }

            self.logger.info(
                f"  🔄 Calcul z-scores pour {len(metrics_to_process)} métriques..."
            )

            # Calculer z-scores pour chaque métrique
            calculated = 0
            failed = 0

            for signal_metric in metrics_to_process:
                timestamp = signal_metric.timestamp

                try:
                    # 1. Récupérer les données nécessaires
                    current_price = 0.0
                    if signal_metric.price_metric:
                        current_price = signal_metric.price_metric.close or 0.0

                    # Récupérer Volume Global et FGI pour ce timestamp
                    # CORRECTION: Chercher la métrique macro pour le JOUR donné, pas le timestamp exact
                    macro_metric = (
                        MacroMetric.select()
                        .where(fn.date(MacroMetric.timestamp) == timestamp.date())
                        .first()
                    )

                    current_global_volume = 0.0
                    if macro_metric:
                        current_global_volume = macro_metric.global_volume_usd or 0.0

                    # 2. Calculer z-scores via modules existants
                    z_social_res = calculate_z_social(
                        token=token, reference_timestamp=timestamp, window_minutes=60
                    )

                    z_price_res = calculate_z_price(
                        token=token,
                        current_price=current_price,
                        reference_timestamp=timestamp,
                    )

                    z_vol_res = calculate_z_vol(
                        current_global_volume=current_global_volume,
                        reference_timestamp=timestamp,
                    )

                    # Mettre à jour signal_metric
                    # Note: Les fonctions retournent des dicts, on extrait la valeur
                    z_social_val = (
                        z_social_res["z_score_social"] if z_social_res else 0.0
                    )
                    z_activity_val = (
                        z_social_res.get("z_score_vs_activity") if z_social_res else None
                    )  # Dual Z-Score (nullable)
                    z_price_val = z_price_res["z_score_price"] if z_price_res else 0.0
                    z_vol_val = z_vol_res["z_vol"] if z_vol_res else 0.0

                    signal_metric.z_score_social = z_social_val
                    signal_metric.z_score_vs_activity = z_activity_val  # NOUVEAU
                    signal_metric.z_score_price = z_price_val
                    signal_metric.z_vol = z_vol_val

                    # Calculer divergence
                    if z_social_res and z_price_res:
                        signal_metric.divergence_score = z_social_val - z_price_val
                        signal_metric.signal_strength = abs(
                            signal_metric.divergence_score
                        )

                    # Ajouter FGI Correction
                    if macro_metric and macro_metric.fear_greed_index is not None:
                        signal_metric.fgi_correction = macro_metric.fear_greed_index

                    signal_metric.save()

                    # --- MISE À JOUR SOCIAL_METRIC (Pour cohérence) ---
                    if signal_metric.social_metric and z_social_res:
                        sm = signal_metric.social_metric
                        sm.z_score_raw = z_social_val
                        sm.z_score_final = z_social_val
                        sm.social_density = z_social_res.get(
                            "social_density", sm.social_density
                        )
                        sm.social_volume = z_social_res.get(
                            "social_volume", sm.social_volume
                        )
                        sm.save()
                        self.logger.debug(
                            f"    💾 SocialMetric mis à jour: Z={z_social_val:.2f}"
                        )

                    calculated += 1

                    self.logger.debug(
                        f"    ✅ {timestamp}: "
                        f"z_social={z_social_val:.2f}, z_price={z_price_val:.2f}, "
                        f"divergence={signal_metric.divergence_score:.2f}"
                    )

                except Exception as e:
                    failed += 1
                    self.logger.error(
                        f"    ❌ Échec calcul z-score pour {timestamp}: {e}"
                    )

            self.logger.info(
                f"  ✅ {calculated} z-scores calculés ({failed} échecs)"
                if failed > 0
                else f"  ✅ {calculated} z-scores calculés"
            )

            return {
                "status": "SUCCESS" if failed == 0 else "PARTIAL",
                "message": f"{calculated} z-scores calculés",
                "zscores_calculated": calculated,
                "failed": failed,
            }

        except Exception as e:
            self.logger.error(f"  ❌ Stage 5 échoué: {e}")
            return {
                "status": "FAILED",
                "message": str(e),
                "error": str(e),
                "zscores_calculated": 0,
                "failed": 0,
            }

    # ========================================================================
    # ORCHESTRATION COMPLÈTE
    # ========================================================================

    def orchestrate_token_full(
        self, token: Token, days_back: int = 7, force_social: bool = False
    ) -> dict[str, Any]:
        """
        Orchestration complète mode FULL (backfill complet).

        Utilisé pour nouveaux tokens ou backfill forcé.
        Target: 7 jours × 24h = 168 métriques

        Args:
            token: Token à traiter
            days_back: Nombre de jours à backfill (défaut: 7)
            force_social: Force le recalcul des social_metrics (même créneaux existants)

        Returns:
            Dict contenant:
                - status: 'SUCCESS', 'PARTIAL', ou 'FAILED'
                - stages: Dict avec résultats de chaque stage
                - validation: Dict avec validation finale
        """
        self.logger.info(
            f"🚀 === ORCHESTRATION FULL: {token.cashtag} ({days_back}j) "
            f"{'[FORCE SOCIAL]' if force_social else ''} ==="
        )

        results = {}

        # Stage 1: Social Metrics
        results["stage_1"] = self.stage_1_social_metrics(
            token, interval_minutes=60, force=force_social
        )

        # Stage 2: Synchronisation Prix (CRITIQUE)
        results["stage_2"] = self.stage_2_synchronize_price_metrics(
            token, hours_back=days_back * 24
        )

        # Stage 3: Macro Metrics
        results["stage_3"] = self.stage_3_macro_metrics(days_back=days_back)

        # Stage 4: Signal Metrics
        results["stage_4"] = self.stage_4_signal_metrics(token, days_back=days_back)

        # Stage 5: Z-Scores
        results["stage_5"] = self.stage_5_calculate_zscores(token, days_back=days_back)

        # Validation finale
        validation = self.validate_pipeline_readiness(token)

        # Déterminer statut global
        failed_stages = [k for k, v in results.items() if v.get("status") == "FAILED"]
        partial_stages = [k for k, v in results.items() if v.get("status") == "PARTIAL"]

        if failed_stages:
            status = "FAILED"
        elif partial_stages:
            status = "PARTIAL"
        else:
            status = "SUCCESS"

        self.logger.info(
            f"🎯 === ORCHESTRATION TERMINÉE: {status} "
            f"({'PRÊT' if validation['is_ready'] else 'NON PRÊT'}) ==="
        )

        return {
            "status": status,
            "stages": results,
            "validation": validation,
            "token": token.cashtag,
        }

    def orchestrate_token_incremental(
        self, token: Token, force_social: bool = False
    ) -> dict[str, Any]:
        """
        Orchestration mode INCREMENTAL (comble uniquement les gaps).

        Utilisé après mises à jour de données (twitter_worker, price_worker).
        Plus rapide car exécute uniquement les stages nécessaires.

        Args:
            token: Token à traiter
            force_social: Force le recalcul des social_metrics (même créneaux existants)

        Returns:
            Dict contenant:
                - status: 'SUCCESS', 'PARTIAL', ou 'FAILED'
                - stages_executed: int - Nombre de stages exécutés
                - results: Dict avec résultats des stages exécutés
        """
        self.logger.info(
            f"🔄 === ORCHESTRATION INCREMENTAL: {token.cashtag} "
            f"{'[FORCE SOCIAL]' if force_social else ''} ==="
        )

        # Détecte ce qui manque
        self.logger.info("  🔍 Appel detect_pipeline_gaps()...")
        gaps = self.detect_pipeline_gaps(token)
        self.logger.info(
            f"  🔍 Gaps détectés: social={gaps['social_metrics']['has_gaps']}, "
            f"price={gaps['price_zscores']['has_gaps']}, "
            f"signal={gaps['signal_metrics']['has_gaps']}"
        )

        results = {}
        stages_executed = 0

        # Exécute uniquement stages nécessaires
        self.logger.info(
            f"  🔍 Vérification stage_1: has_gaps={gaps['social_metrics']['has_gaps']}"
        )
        # Si force_social=True, exécuter même sans gaps
        if gaps["social_metrics"]["has_gaps"] or force_social:
            if force_social:
                self.logger.info("  ▶️  Exécution stage_1 (FORCÉ)...")
            else:
                self.logger.info("  ▶️  Exécution stage_1...")
            results["stage_1"] = self.stage_1_social_metrics(
                token, interval_minutes=60, force=force_social
            )
            stages_executed += 1
        else:
            self.logger.info("  ⏭️  Stage_1 skippé (pas de gaps)")

        if gaps["price_zscores"]["has_gaps"] or gaps["social_metrics"]["has_gaps"]:
            # Si nouveaux social_metrics, vérifier synchronisation prix
            results["stage_2"] = self.stage_2_synchronize_price_metrics(
                token, hours_back=24
            )
            stages_executed += 1

        # Stage 3 toujours vérifié (macro indépendant du token)
        stage_3_result = self.stage_3_macro_metrics(days_back=10)
        results["stage_3"] = stage_3_result
        stages_executed += 1

        # Déterminer si les stages 4 et 5 doivent tourner
        # C'est le cas si:
        # - Il y a des gaps dans les signal_metrics
        # - Stage 1 ou 2 ont tourné (nouvelles données social/prix)
        # - Stage 3 a créé/mis à jour des données macro (FGI)
        stage3_did_work = stage_3_result.get("status") in ["SUCCESS", "PARTIAL"] and (
            stage_3_result.get("backfill_stats", {}).get("created", 0) > 0
            or stage_3_result.get("backfill_stats", {}).get("updated", 0) > 0
        )

        if (
            gaps["signal_metrics"]["has_gaps"]
            or results.get("stage_1")
            or results.get("stage_2")
            or stage3_did_work
        ):
            if stage3_did_work:
                self.logger.info(
                    "  ▶️  Nouvelles données macro, MAJ des signal_metrics et z-scores requise."
                )

            # Si nouveaux social ou price, regénérer signals
            results["stage_4"] = self.stage_4_signal_metrics(token, days_back=7)
            stages_executed += 1

            # Et recalculer z-scores
            results["stage_5"] = self.stage_5_calculate_zscores(token, days_back=7)
            stages_executed += 1

        if stages_executed == 0:
            self.logger.info("✅ === Aucun gap détecté, pipeline à jour ===")
            return {
                "status": "NO_ACTION_NEEDED",
                "stages_executed": 0,
                "results": {},
                "token": token.cashtag,
            }

        # Déterminer statut
        failed_stages = [k for k, v in results.items() if v.get("status") == "FAILED"]
        partial_stages = [k for k, v in results.items() if v.get("status") == "PARTIAL"]

        if failed_stages:
            status = "FAILED"
        elif partial_stages:
            status = "PARTIAL"
        else:
            status = "SUCCESS"

        self.logger.info(
            f"🎯 === INCREMENTAL TERMINÉ: {status} ({stages_executed} stages) ==="
        )

        return {
            "status": status,
            "stages_executed": stages_executed,
            "results": results,
            "token": token.cashtag,
        }

    def orchestrate_all_tokens(self, mode: str = "incremental") -> dict[str, Any]:
        """
        Orchestre tous les tokens actifs (REGULAR, AGGRESSIVE, CROISIERE).

        Args:
            mode: 'incremental' (défaut) ou 'full'

        Returns:
            Dict contenant:
                - total_tokens: int
                - success_count: int
                - partial_count: int
                - failed_count: int
                - results: Dict[str, Dict] - Résultats par token
        """
        self.logger.info(f"🌟 === ORCHESTRATION ALL TOKENS (mode: {mode}) ===")

        # Récupérer tous les tokens actifs
        active_tokens = Token.select().where(
            Token.status.in_(["REGULAR", "AGGRESSIVE", "CROISIERE"])
        )

        total_tokens = active_tokens.count()
        self.logger.info(f"📋 {total_tokens} tokens actifs à traiter")

        results = {}
        success_count = 0
        partial_count = 0
        failed_count = 0

        for token in active_tokens:
            try:
                if mode == "full":
                    result = self.orchestrate_token_full(token, days_back=7)
                else:
                    result = self.orchestrate_token_incremental(token)

                results[token.cashtag] = result

                if (
                    result["status"] == "SUCCESS"
                    or result["status"] == "NO_ACTION_NEEDED"
                ):
                    success_count += 1
                elif result["status"] == "PARTIAL":
                    partial_count += 1
                else:
                    failed_count += 1

            except Exception as e:
                self.logger.error(f"❌ Erreur orchestration {token.cashtag}: {e}")
                results[token.cashtag] = {"status": "FAILED", "error": str(e)}
                failed_count += 1

        self.logger.info(
            f"🎯 === ALL TOKENS TERMINÉ: "
            f"{success_count} succès, {partial_count} partiels, {failed_count} échecs ==="
        )

        return {
            "total_tokens": total_tokens,
            "success_count": success_count,
            "partial_count": partial_count,
            "failed_count": failed_count,
            "results": results,
        }

    # ========================================================================
    # VALIDATION & DIAGNOSTICS
    # ========================================================================

    def detect_pipeline_gaps(self, token: Token) -> dict[str, Any]:
        """
        Analyse complète des gaps dans le pipeline.

        Args:
            token: Token à analyser

        Returns:
            Dict contenant pour chaque table:
                - count: Nombre d'entrées
                - has_gaps: bool
                - message: Description
        """
        # Social Metrics - Vérifier gaps temporels (dernières 24h)
        from ..database.models import RawTweet

        social_count = count_social_metrics(token, days_back=7)

        # Vérifier s'il y a des raw_tweets récents non agrégés
        cutoff_24h = datetime.now() - timedelta(hours=24)

        try:
            # Compter raw_tweets des dernières 24h
            recent_tweets_count = (
                RawTweet.select()
                .where((RawTweet.token == token) & (RawTweet.created_at >= cutoff_24h))
                .count()
            )

            # Compter social_metrics des dernières 24h
            recent_metrics_count = (
                SocialMetric.select()
                .where(
                    (SocialMetric.token == token)
                    & (SocialMetric.timestamp >= cutoff_24h)
                )
                .count()
            )

            # Si on a des tweets mais pas assez de métriques, il y a un gap
            # On s'attend à ~1 métrique par heure si des tweets existent
            hours_elapsed = (datetime.now() - cutoff_24h).total_seconds() / 3600
            expected_metrics = min(24, int(hours_elapsed))

            # DEBUG: Logs détaillés
            self.logger.info(
                f"  🔍 [DEBUG] Détection gaps social_metrics pour {token.cashtag}:"
            )
            self.logger.info(f"      - Cutoff 24h: {cutoff_24h}")
            self.logger.info(f"      - Raw tweets récents: {recent_tweets_count}")
            self.logger.info(f"      - Social metrics récents: {recent_metrics_count}")
            self.logger.info(f"      - Heures écoulées: {hours_elapsed:.1f}")
            self.logger.info(f"      - Métriques attendues: {expected_metrics}")
            self.logger.info(f"      - Total social_count (7j): {social_count}")
            self.logger.info(f"      - MIN_SOCIAL_METRICS: {self.MIN_SOCIAL_METRICS}")

            social_has_gaps = (social_count < self.MIN_SOCIAL_METRICS) or (
                recent_tweets_count > 0 and recent_metrics_count < expected_metrics
            )

            self.logger.info(f"      - Gap détecté? {social_has_gaps}")
            if social_has_gaps:
                if social_count < self.MIN_SOCIAL_METRICS:
                    self.logger.info(
                        f"        → Raison: social_count ({social_count}) < MIN ({self.MIN_SOCIAL_METRICS})"
                    )
                else:
                    self.logger.info(
                        f"        → Raison: {recent_tweets_count} tweets mais {recent_metrics_count}/{expected_metrics} métriques"
                    )

        except Exception as e:
            self.logger.warning(f"  ⚠️  Erreur détection gaps social_metrics: {e}")
            social_has_gaps = social_count < self.MIN_SOCIAL_METRICS

        # Price Z-Scores
        try:
            price_total = (
                PriceMetric.select()
                .where((PriceMetric.token == token) & (PriceMetric.resolution == "1h"))
                .count()
            )

            price_with_zscore = (
                PriceMetric.select()
                .where(
                    (PriceMetric.token == token)
                    & (PriceMetric.resolution == "1h")
                    & (PriceMetric.z_score_price.is_null(False))
                )
                .count()
            )

            price_coverage = (
                (price_with_zscore / price_total) if price_total > 0 else 0.0
            )
            price_has_gaps = price_coverage < self.MIN_PRICE_COVERAGE

        except Exception:
            price_total = 0
            price_with_zscore = 0
            price_coverage = 0.0
            price_has_gaps = True

        # Signal Metrics
        try:
            signal_count = (
                SignalMetric.select().where(SignalMetric.token == token).count()
            )

            signal_has_gaps = signal_count < (social_count * self.MIN_SIGNAL_COVERAGE)

        except Exception:
            signal_count = 0
            signal_has_gaps = True

        return {
            "social_metrics": {
                "count": social_count,
                "has_gaps": social_has_gaps,
                "message": f"{social_count}/{self.MIN_SOCIAL_METRICS} metrics",
            },
            "price_zscores": {
                "total": price_total,
                "with_zscore": price_with_zscore,
                "coverage_ratio": price_coverage,
                "has_gaps": price_has_gaps,
                "message": f"{price_coverage * 100:.1f}% coverage",
            },
            "signal_metrics": {
                "count": signal_count,
                "has_gaps": signal_has_gaps,
                "message": f"{signal_count}/{social_count} signal metrics",
            },
        }

    def validate_pipeline_readiness(self, token: Token) -> dict[str, Any]:
        """
        Vérifie si le pipeline est prêt pour calculs z-score temps réel.

        Args:
            token: Token à valider

        Returns:
            Dict contenant:
                - is_ready: bool
                - checks: Dict[str, bool] - Résultats des vérifications
                - blocking_issues: List[str] - Problèmes bloquants
        """
        gaps = self.detect_pipeline_gaps(token)

        checks = {
            "social_metrics": not gaps["social_metrics"]["has_gaps"],
            "price_coverage": not gaps["price_zscores"]["has_gaps"],
            "signal_metrics": not gaps["signal_metrics"]["has_gaps"],
        }

        blocking_issues = []
        if not checks["social_metrics"]:
            blocking_issues.append(
                f"Social metrics: {gaps['social_metrics']['message']}"
            )
        if not checks["price_coverage"]:
            blocking_issues.append(
                f"Price coverage: {gaps['price_zscores']['message']}"
            )
        if not checks["signal_metrics"]:
            blocking_issues.append(
                f"Signal metrics: {gaps['signal_metrics']['message']}"
            )

        is_ready = len(blocking_issues) == 0

        return {
            "is_ready": is_ready,
            "checks": checks,
            "blocking_issues": blocking_issues,
        }


# ============================================================================
# FONCTIONS HELPER POUR COMPATIBILITÉ
# ============================================================================


def aggregate_metrics_for_token(
    token: Token, mode: str = "incremental"
) -> dict[str, Any]:
    """
    Fonction helper pour agrégation (compatibilité).

    Args:
        token: Token à traiter
        mode: 'full' ou 'incremental'

    Returns:
        Dict contenant résultat de l'orchestration
    """
    aggregator = MetricAggregator()

    if mode == "full":
        return aggregator.orchestrate_token_full(token, days_back=7)
    return aggregator.orchestrate_token_incremental(token)


# ============================================================================
# TEST UNITAIRE
# ============================================================================

if __name__ == "__main__":
    """Test du MetricAggregator"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    print("🧪 Test MetricAggregator\n")

    # Récupérer un token de test
    try:
        test_token = Token.get(Token.symbol == "TURBO")
        print(f"Token de test: {test_token.cashtag}\n")

        # Initialiser aggregator
        aggregator = MetricAggregator()

        # Test 1: Détection de gaps
        print("=" * 60)
        print("TEST 1: Détection de gaps")
        print("=" * 60)
        gaps = aggregator.detect_pipeline_gaps(test_token)
        print("\nRésultat:")
        for table, info in gaps.items():
            print(f"  {table}: {info['message']} {'⚠️' if info['has_gaps'] else '✅'}")

        # Test 2: Validation pipeline
        print("\n" + "=" * 60)
        print("TEST 2: Validation pipeline")
        print("=" * 60)
        validation = aggregator.validate_pipeline_readiness(test_token)
        print(f"\nPrêt: {'✅ OUI' if validation['is_ready'] else '❌ NON'}")
        if validation["blocking_issues"]:
            print("Problèmes:")
            for issue in validation["blocking_issues"]:
                print(f"  - {issue}")

        # Test 3: Orchestration incrémentale
        print("\n" + "=" * 60)
        print("TEST 3: Orchestration incrémentale")
        print("=" * 60)
        result = aggregator.orchestrate_token_incremental(test_token)
        print(f"\nRésultat: {result['status']}")
        print(f"Stages exécutés: {result['stages_executed']}")

        print("\n✅ Tests terminés")

    except Token.DoesNotExist:
        print("❌ Token TURBO non trouvé en DB")
    except Exception as e:
        print(f"❌ Erreur test: {e}")
