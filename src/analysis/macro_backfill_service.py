"""
Macro Backfill Service - Service de backfill pour les données macro-économiques.
Gère la détection et le comblement des gaps dans les métriques FGI et Volume Global.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Any

from ..database.models import MacroMetric
from ..utils.api_manager import api_manager
from ..utils.api_schemas import FearGreedResponse

logger = logging.getLogger(__name__)

# API FGI avec support de l'historique
API_FGI_HISTORICAL = "https://api.alternative.me/fng/"


class MacroBackfillService:
    """
    Service de backfill pour les données macro-économiques.

    Fonctionnalités:
    - Détection de gaps dans MacroMetric (FGI, Volume Global)
    - Backfill FGI via alternative.me API
    - Reporting de coverage
    """

    def __init__(self):
        """Initialise le service de backfill macro."""
        self.logger = logger

    # ========================================================================
    # GAP DETECTION
    # ========================================================================

    def detect_macro_gaps(self, days_back: int = 10) -> dict[str, Any]:
        """
        Détecte les gaps dans les données macro sur les N derniers jours.

        Args:
            days_back: Nombre de jours à analyser en arrière

        Returns:
            Dict contenant:
                - fgi_gaps: List[datetime.date] - Dates manquantes pour FGI
                - fgi_coverage: float - Pourcentage de coverage FGI (0-100)
                - total_days: int - Nombre total de jours analysés
                - days_with_fgi: int - Nombre de jours avec FGI
        """
        self.logger.info(f"🔍 Détection gaps macro (derniers {days_back} jours)...")

        # Définir la plage d'analyse
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days_back - 1)

        # Générer la liste de tous les jours attendus
        all_dates = []
        current_date = start_date
        while current_date <= end_date:
            all_dates.append(current_date)
            current_date += timedelta(days=1)

        # Récupérer les jours avec FGI dans la DB
        try:
            macro_entries = (
                MacroMetric.select(MacroMetric.timestamp, MacroMetric.fear_greed_index)
                .where(
                    (
                        MacroMetric.timestamp
                        >= datetime.combine(start_date, datetime.min.time())
                    )
                    & (
                        MacroMetric.timestamp
                        <= datetime.combine(end_date, datetime.max.time())
                    )
                )
                .order_by(MacroMetric.timestamp.asc())
            )

            # Extraire les dates avec FGI non-null
            dates_with_fgi = set()
            for entry in macro_entries:
                if entry.fear_greed_index is not None:
                    dates_with_fgi.add(entry.timestamp.date())

        except Exception as e:
            self.logger.error(
                f"❌ Erreur lors de la récupération des macro_metrics: {e}"
            )
            dates_with_fgi = set()

        # Calculer les gaps
        fgi_gaps = [date for date in all_dates if date not in dates_with_fgi]

        # Calculer la coverage
        fgi_coverage = (
            (len(dates_with_fgi) / len(all_dates)) * 100 if all_dates else 0.0
        )

        result = {
            "fgi_gaps": fgi_gaps,
            "fgi_coverage": round(fgi_coverage, 1),
            "total_days": len(all_dates),
            "days_with_fgi": len(dates_with_fgi),
        }

        if fgi_gaps:
            self.logger.warning(
                f"⚠️  {len(fgi_gaps)} jour(s) manquant(s) FGI "
                f"(coverage: {result['fgi_coverage']}%)"
            )
        else:
            self.logger.info(f"✅ FGI complet sur {days_back} jours (100%)")

        return result

    # ========================================================================
    # FGI BACKFILL
    # ========================================================================

    def fetch_historical_fgi(self, days: int = 10) -> list[dict[str, Any]] | None:
        """
        Récupère les données FGI historiques depuis alternative.me API.

        Args:
            days: Nombre de jours d'historique à récupérer (max ~365)

        Returns:
            List[Dict] contenant [{timestamp: datetime, value: int, classification: str}]
            ou None si erreur
        """
        self.logger.info(f"📡 Récupération FGI historique (limite: {days} jours)...")

        try:
            # Appel API avec paramètre limit
            url = f"{API_FGI_HISTORICAL}?limit={days}"

            validated_data = api_manager.get_validated_json(
                url=url, service="alternative_me", schema=FearGreedResponse, timeout=10
            )

            if validated_data is None:
                self.logger.error(
                    "❌ Erreur API FGI historique: Aucune donnée retournée"
                )
                return None

            # Convertir les données Pydantic en format exploitable
            historical_data = []
            for entry in validated_data.data:
                # L'API retourne timestamp en secondes Unix
                timestamp = datetime.fromtimestamp(int(entry.timestamp))

                historical_data.append(
                    {
                        "timestamp": timestamp,
                        "value": entry.value_int,
                        "classification": entry.value_classification,
                    }
                )

            self.logger.info(f"✅ {len(historical_data)} entrées FGI récupérées")
            return historical_data

        except Exception as e:
            self.logger.error(f"❌ Erreur lors de la récupération FGI historique: {e}")
            return None

    def backfill_fgi(self, missing_dates: list[date]) -> dict[str, int]:
        """
        Comble les gaps FGI en récupérant les données manquantes via API.

        Args:
            missing_dates: Liste des dates pour lesquelles le FGI manque

        Returns:
            Dict contenant:
                - created: int - Nombre d'entrées créées
                - updated: int - Nombre d'entrées mises à jour
                - skipped: int - Nombre d'entrées ignorées
                - failed: int - Nombre d'échecs
        """
        if not missing_dates:
            self.logger.info("✅ Aucun gap FGI à combler")
            return {"created": 0, "updated": 0, "skipped": 0, "failed": 0}

        self.logger.info(f"🔄 Backfill FGI pour {len(missing_dates)} jour(s)...")

        # Déterminer la plage nécessaire (du plus ancien au plus récent)
        oldest_date = min(missing_dates)
        days_back = (datetime.now().date() - oldest_date).days + 1

        # Limiter à 365 jours (limite API)
        if days_back > 365:
            self.logger.warning(
                f"⚠️  Limite API: {days_back} jours demandés, limité à 365"
            )
            days_back = 365

        # Récupérer les données historiques
        historical_data = self.fetch_historical_fgi(days=days_back)

        if historical_data is None:
            self.logger.error("❌ Échec de récupération des données FGI")
            return {
                "created": 0,
                "updated": 0,
                "skipped": 0,
                "failed": len(missing_dates),
            }

        # Créer un index des dates manquantes pour lookup rapide
        missing_dates_set = set(missing_dates)

        # Parcourir les données et combler les gaps
        stats = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}

        for entry in historical_data:
            entry_date = entry["timestamp"].date()

            # Vérifier si cette date est dans les gaps à combler
            if entry_date not in missing_dates_set:
                continue

            try:
                # Vérifier si une entrée existe déjà pour ce jour
                existing_entry = (
                    MacroMetric.select()
                    .where(
                        (
                            MacroMetric.timestamp
                            >= datetime.combine(entry_date, datetime.min.time())
                        )
                        & (
                            MacroMetric.timestamp
                            <= datetime.combine(entry_date, datetime.max.time())
                        )
                    )
                    .first()
                )

                if existing_entry:
                    # Mettre à jour si FGI est null
                    if existing_entry.fear_greed_index is None:
                        existing_entry.fear_greed_index = entry["value"]
                        existing_entry.save()
                        stats["updated"] += 1
                        self.logger.debug(
                            f"  ✅ FGI mis à jour: {entry_date} = {entry['value']}"
                        )
                    else:
                        stats["skipped"] += 1
                        self.logger.debug(f"  ⏭️  FGI déjà présent: {entry_date}")
                else:
                    # Créer nouvelle entrée
                    MacroMetric.create(
                        timestamp=entry["timestamp"],
                        fear_greed_index=entry["value"],
                        global_volume_usd=None,  # Pas d'historique volume via API gratuite
                    )
                    stats["created"] += 1
                    self.logger.debug(f"  ✅ FGI créé: {entry_date} = {entry['value']}")

            except Exception as e:
                stats["failed"] += 1
                self.logger.error(f"  ❌ Échec backfill {entry_date}: {e}")

        # Log final
        self.logger.info(
            f"✅ Backfill FGI terminé: "
            f"{stats['created']} créés, "
            f"{stats['updated']} mis à jour, "
            f"{stats['skipped']} ignorés, "
            f"{stats['failed']} échecs"
        )

        return stats

    # ========================================================================
    # ORCHESTRATION COMPLÈTE
    # ========================================================================

    def backfill_all_macro(self, days_back: int = 10) -> dict[str, Any]:
        """
        Orchestration complète du backfill macro.

        1. Détecte les gaps FGI
        2. Backfill automatique si gaps détectés
        3. Retourne rapport complet

        Args:
            days_back: Nombre de jours à analyser/backfill

        Returns:
            Dict contenant:
                - gaps_detected: Dict - Résultat de detect_macro_gaps()
                - backfill_stats: Dict - Résultat de backfill_fgi()
                - status: str - 'SUCCESS', 'PARTIAL', ou 'FAILED'
        """
        self.logger.info(f"🚀 Orchestration backfill macro ({days_back} jours)...")

        # Étape 1: Détection gaps
        gaps = self.detect_macro_gaps(days_back=days_back)

        # Étape 2: Backfill si nécessaire
        backfill_stats = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}

        if gaps["fgi_gaps"]:
            backfill_stats = self.backfill_fgi(missing_dates=gaps["fgi_gaps"])
        else:
            self.logger.info("✅ Aucun gap détecté, backfill non nécessaire")

        # Déterminer le statut final
        if backfill_stats["failed"] == 0:
            if backfill_stats["created"] + backfill_stats["updated"] > 0:
                status = "SUCCESS"
            else:
                status = "NO_ACTION_NEEDED"
        elif backfill_stats["created"] + backfill_stats["updated"] > 0:
            status = "PARTIAL"
        else:
            status = "FAILED"

        result = {
            "gaps_detected": gaps,
            "backfill_stats": backfill_stats,
            "status": status,
        }

        self.logger.info(f"✅ Orchestration macro terminée: {status}")
        return result


# ============================================================================
# FONCTION HELPER POUR COMPATIBILITÉ
# ============================================================================


def backfill_macro_metrics(days_back: int = 10) -> dict[str, Any]:
    """
    Fonction helper pour backfill macro (compatibilité).

    Args:
        days_back: Nombre de jours à backfill

    Returns:
        Dict contenant résultat du backfill
    """
    service = MacroBackfillService()
    return service.backfill_all_macro(days_back=days_back)


# ============================================================================
# TEST UNITAIRE
# ============================================================================

if __name__ == "__main__":
    """Test du service de backfill macro"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    print("🧪 Test MacroBackfillService\n")

    # Initialiser le service
    service = MacroBackfillService()

    # Test 1: Détection de gaps
    print("=" * 60)
    print("TEST 1: Détection de gaps (10 jours)")
    print("=" * 60)
    gaps = service.detect_macro_gaps(days_back=10)
    print("\nRésultat:")
    print(f"  - Total jours: {gaps['total_days']}")
    print(f"  - Jours avec FGI: {gaps['days_with_fgi']}")
    print(f"  - Coverage: {gaps['fgi_coverage']}%")
    print(f"  - Gaps détectés: {len(gaps['fgi_gaps'])}")

    # Test 2: Backfill complet
    print("\n" + "=" * 60)
    print("TEST 2: Backfill complet")
    print("=" * 60)
    result = service.backfill_all_macro(days_back=10)
    print("\nRésultat:")
    print(f"  - Status: {result['status']}")
    print(f"  - Créés: {result['backfill_stats']['created']}")
    print(f"  - Mis à jour: {result['backfill_stats']['updated']}")
    print(f"  - Ignorés: {result['backfill_stats']['skipped']}")
    print(f"  - Échecs: {result['backfill_stats']['failed']}")

    print("\n✅ Tests terminés")
