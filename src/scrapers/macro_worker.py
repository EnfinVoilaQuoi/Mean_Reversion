"""
Macro Worker - Gère les données macro-économiques (FGI et Volume Global 24h).
Implémente une logique de vérification de l'horodatage pour optimiser les appels API.
Utilise api_manager pour le rate limiting.
"""

import logging
from datetime import datetime
from typing import TypedDict

from peewee import fn

# Import de la nouvelle classe de modèle et de la fonction utilitaire
from ..database.models import MacroMetric, get_latest_fgi_for_day

# Import de l'API Manager pour rate limiting et validation
from ..utils.api_manager import api_manager
from ..utils.api_schemas import CoinGeckoGlobalResponse, FearGreedResponse

# Configuration du logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Définition des APIs externes (Endpoints publics et gratuits)
API_FGI = "https://api.alternative.me/fng/"
API_GLOBAL_VOLUME = "https://api.coingecko.com/api/v3/global"


# ============================================================================
# FONCTIONS D'APPEL API
# ============================================================================


def fetch_fgi_from_api() -> int | None:
    """
    Appelle l'API FGI (alternative.me) et retourne la valeur.
    Utilise api_manager avec validation Pydantic.
    """
    try:
        # Utilisation de l'API Manager avec validation Pydantic
        validated_data = api_manager.get_validated_json(
            url=API_FGI, service="alternative_me", schema=FearGreedResponse, timeout=5
        )

        if validated_data is None:
            logger.error(
                "    - ❌ Erreur API FGI: Aucune donnée retournée ou validation échouée"
            )
            return None

        # Accès simplifié grâce à Pydantic
        fgi_value = validated_data.data[0].value_int
        logger.info(f"    - ✅ FGI (API) : {fgi_value}")
        return fgi_value  # type: ignore[no-any-return]

    except (IndexError, AttributeError) as e:
        logger.error(f"    - ❌ Erreur d'accès aux données FGI: {e}")
    except Exception as e:
        logger.error(f"    - ❌ Erreur inattendue FGI: {e}")
    return None


class MacroCache(TypedDict):
    global_volume: float | None
    last_updated: datetime | None
    cache_duration_minutes: int


# Cache en mémoire pour le volume global afin d'éviter le rate limiting
_cache: MacroCache = {
    "global_volume": None,
    "last_updated": None,
    "cache_duration_minutes": 5,
}


def fetch_global_volume_from_api() -> float | None:
    """
    Appelle l'API CoinGecko pour le volume global et retourne la valeur.
    Implémente un cache simple pour éviter le rate limiting.
    """
    now = datetime.now()

    # Vérifier le cache
    last_updated = _cache.get("last_updated")
    global_volume = _cache.get("global_volume")

    if last_updated is not None and global_volume is not None:
        cache_age_seconds = (now - last_updated).total_seconds()
        if cache_age_seconds < (_cache["cache_duration_minutes"] * 60):
            logger.info(f"    - ✅ Volume Global (Cache) : ${global_volume:,.0f}")
            return global_volume

    logger.info("    - ⏳ Volume Global (API) : Appel API en cours...")
    try:
        # Utilisation de l'API Manager avec validation Pydantic
        validated_data = api_manager.get_validated_json(
            url=API_GLOBAL_VOLUME,
            service="coingecko_free",  # Endpoint /global est public
            schema=CoinGeckoGlobalResponse,
            timeout=5,
        )

        if validated_data is None:
            logger.error(
                "    - ❌ Erreur API Volume Global: Aucune donnée retournée ou validation échouée"
            )
            return None

        # Accès simplifié grâce à Pydantic
        volume_usd = float(validated_data.data.total_volume.get("usd", 0))

        # Si le volume est 0, c'est probablement une erreur, ne pas cacher
        if volume_usd > 0:
            logger.info(f"    - ✅ Volume Global (API) : ${volume_usd:,.0f}")
            # Mettre à jour le cache
            _cache["global_volume"] = volume_usd
            _cache["last_updated"] = now
        else:
            logger.warning("    - ⚠️ Volume Global retourné est 0. Non mis en cache.")

        return volume_usd

    except (KeyError, ValueError, AttributeError) as e:
        logger.error(f"    - ❌ Erreur d'accès aux données Volume Global: {e}")
    except Exception as e:
        logger.error(f"    - ❌ Erreur inattendue Volume Global: {e}")
    return None


# ============================================================================
# FONCTION DE MISE À JOUR DE LA BASE DE DONNÉES (WORKER PRINCIPAL)
# ============================================================================


def macro_monitoring_job():
    """
    Job principal exécuté par le scheduler pour mettre à jour les métriques macro.
    """
    logger.info("📡 Lancement du Macro Worker...")

    fgi_value = None
    volume_value = None

    # ------------------------------------------------------------------------
    # 1. Traitement du FGI (Mise à jour quotidienne)
    # ------------------------------------------------------------------------
    latest_fgi_db = get_latest_fgi_for_day()

    if latest_fgi_db is not None:
        fgi_value = latest_fgi_db
        logger.info(f"    - ℹ️ FGI du jour déjà présent ({fgi_value}). API non appelée.")
    else:
        logger.info("    - ⏳ FGI non trouvé pour aujourd'hui. Appel API...")
        fgi_value = fetch_fgi_from_api()

    # ------------------------------------------------------------------------
    # 2. Traitement du Volume Global (Mise à jour à chaque exécution)
    # ------------------------------------------------------------------------
    logger.info("    - ⏳ Récupération du Volume Global 24h...")
    volume_value = fetch_global_volume_from_api()

    # ------------------------------------------------------------------------
    # 3. Sauvegarde dans la DB (Logique "Upsert")
    # ------------------------------------------------------------------------

    if fgi_value is not None or volume_value is not None:
        try:
            # Essayer de trouver une entrée pour aujourd'hui
            today = datetime.now().date()
            metric_entry = (
                MacroMetric.select()
                .where(fn.date(MacroMetric.timestamp) == today)
                .first()
            )

            if metric_entry:
                # Mettre à jour l'entrée existante
                updated = False
                if fgi_value is not None and metric_entry.fear_greed_index is None:
                    metric_entry.fear_greed_index = fgi_value
                    updated = True
                if (
                    volume_value is not None
                ):  # Toujours mettre à jour le volume car il est plus "live"
                    metric_entry.global_volume_usd = volume_value
                    updated = True

                if updated:
                    metric_entry.save()
                    logger.info(
                        "    - ✅ Métriques Macro mises à jour pour aujourd'hui."
                    )
                else:
                    logger.info("    - ℹ️ Aucune nouvelle métrique à mettre à jour.")

            else:
                # Créer une nouvelle entrée si aucune n'existe pour aujourd'hui
                MacroMetric.create(
                    timestamp=datetime.now(),
                    fear_greed_index=fgi_value,
                    global_volume_usd=volume_value,
                )
                logger.info(
                    "    - ✅ Nouvelle entrée de Métriques Macro créée pour aujourd'hui."
                )

        except Exception as e:
            logger.error(
                f"    - ❌ Erreur lors de la sauvegarde des métriques macro: {e}"
            )

    else:
        logger.warning(
            "    - ⚠️ Aucune métrique n'a pu être récupérée. Sauvegarde annulée."
        )

    logger.info("📡 Macro Worker terminé.")


if __name__ == "__main__":
    """
    Test unitaire du worker macro (nécessite une connexion DB active).
    """
    # NOTE: Pour un test unitaire complet, vous auriez besoin d'initialiser la DB ici.
    # Dans l'environnement du bot, ce job est appelé par main.py.
    print("🧪 Test du module macro_worker.py (appel direct du job)")
    macro_monitoring_job()
