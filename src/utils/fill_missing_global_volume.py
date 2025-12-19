"""
fill_missing_global_volume.py - Interpolation des global_volume_usd manquants

Ce script remplit les valeurs manquantes de global_volume_usd dans MacroMetric
en utilisant une interpolation basée sur :
1. Les valeurs avant et après (interpolation linéaire)
2. Le fear_greed_index comme facteur d'ajustement

Logique:
- Si FGI est élevé (>50), le volume devrait être plus élevé (marché actif)
- Si FGI est bas (<50), le volume devrait être plus bas (marché calme)

Usage:
    python -m src.utils.fill_missing_global_volume
    python -m src.utils.fill_missing_global_volume --dry-run
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

# Ajouter le répertoire racine au path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.database.models import MacroMetric

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def find_surrounding_values(metric: MacroMetric) -> tuple[float | None, float | None]:
    """
    Trouve les valeurs global_volume_usd avant et après un MacroMetric donné.

    Args:
        metric: MacroMetric avec global_volume_usd manquant

    Returns:
        tuple: (volume_before, volume_after) ou (None, None) si non trouvé
    """
    try:
        # Chercher la valeur AVANT (la plus récente avant metric.timestamp)
        before = (
            MacroMetric.select()
            .where(
                (MacroMetric.timestamp < metric.timestamp)
                & (MacroMetric.global_volume_usd.is_null(False))
            )
            .order_by(MacroMetric.timestamp.desc())
            .first()
        )

        # Chercher la valeur APRÈS (la plus ancienne après metric.timestamp)
        after = (
            MacroMetric.select()
            .where(
                (MacroMetric.timestamp > metric.timestamp)
                & (MacroMetric.global_volume_usd.is_null(False))
            )
            .order_by(MacroMetric.timestamp.asc())
            .first()
        )

        volume_before = before.global_volume_usd if before else None
        volume_after = after.global_volume_usd if after else None

        return (volume_before, volume_after)

    except Exception as e:
        logger.error(f"Erreur find_surrounding_values: {e}")
        return (None, None)


def interpolate_volume(
    metric: MacroMetric,
    volume_before: float,
    volume_after: float,
    timestamp_before: datetime,
    timestamp_after: datetime,
) -> float:
    """
    Interpole le global_volume_usd en utilisant l'interpolation linéaire
    avec ajustement selon le fear_greed_index.

    Args:
        metric: MacroMetric avec global_volume_usd manquant
        volume_before: Volume avant
        volume_after: Volume après
        timestamp_before: Timestamp de la valeur avant
        timestamp_after: Timestamp de la valeur après

    Returns:
        float: Volume interpolé
    """
    try:
        # 1. Interpolation linéaire simple
        total_time = (timestamp_after - timestamp_before).total_seconds()
        elapsed_time = (metric.timestamp - timestamp_before).total_seconds()

        if total_time == 0:
            # Cas où les timestamps sont identiques (ne devrait pas arriver)
            base_volume = (volume_before + volume_after) / 2
        else:
            # Interpolation linéaire classique
            ratio = elapsed_time / total_time
            base_volume = volume_before + (volume_after - volume_before) * ratio

        # 2. Ajustement selon le Fear & Greed Index
        if metric.fear_greed_index is not None:
            # Normaliser FGI de [0, 100] à [-1, 1]
            # FGI = 50 (neutre) → facteur = 0
            # FGI = 100 (extrême greed) → facteur = +1
            # FGI = 0 (extrême fear) → facteur = -1
            fgi_normalized = (metric.fear_greed_index - 50) / 50

            # Facteur d'ajustement (±10% maximum basé sur FGI)
            # Si FGI élevé, volume devrait être plus élevé
            adjustment_factor = 1.0 + (fgi_normalized * 0.1)

            adjusted_volume = base_volume * adjustment_factor

            logger.debug(
                f"Interpolation: base={base_volume:.2e}, "
                f"FGI={metric.fear_greed_index}, "
                f"facteur={adjustment_factor:.3f}, "
                f"ajusté={adjusted_volume:.2e}"
            )

            return float(adjusted_volume)
        # Pas de FGI disponible, retourner l'interpolation simple
        return float(base_volume)

    except Exception as e:
        logger.error(f"Erreur interpolate_volume: {e}")
        # Fallback: moyenne simple
        return float((volume_before + volume_after) / 2)


def fill_missing_global_volumes(dry_run: bool = False):
    """
    Remplit les global_volume_usd manquants dans MacroMetric.

    Args:
        dry_run: Si True, simule sans modifier la base
    """
    logger.info("=" * 80)
    logger.info("INTERPOLATION DES GLOBAL_VOLUME_USD MANQUANTS")
    if dry_run:
        logger.info("MODE SIMULATION (DRY-RUN) - Aucune modification ne sera faite")
    logger.info("=" * 80)

    try:
        # Trouver tous les MacroMetric avec global_volume_usd manquant
        missing_metrics = list(
            MacroMetric.select()
            .where(MacroMetric.global_volume_usd.is_null())
            .order_by(MacroMetric.timestamp.asc())
        )

        total_count = len(missing_metrics)
        logger.info(
            f"\nNombre de MacroMetric avec global_volume_usd manquant: {total_count}"
        )

        if total_count == 0:
            logger.info("✅ Aucune valeur manquante trouvée.")
            return

        # Statistiques
        stats = {
            "filled": 0,
            "skipped_no_before": 0,
            "skipped_no_after": 0,
            "skipped_both": 0,
            "errors": 0,
        }

        logger.info("\nDébut de l'interpolation...\n")

        for i, metric in enumerate(missing_metrics, 1):
            try:
                # Trouver les valeurs avant et après
                volume_before, volume_after = find_surrounding_values(metric)

                # Cas où on ne peut pas interpoler
                if volume_before is None and volume_after is None:
                    stats["skipped_both"] += 1
                    logger.warning(
                        f"[{i}/{total_count}] {metric.timestamp.strftime('%Y-%m-%d %H:%M')} - "
                        f"Aucune valeur avant ET après, impossible d'interpoler"
                    )
                    continue
                if volume_before is None:
                    stats["skipped_no_before"] += 1
                    logger.warning(
                        f"[{i}/{total_count}] {metric.timestamp.strftime('%Y-%m-%d %H:%M')} - "
                        f"Aucune valeur avant, utilisation de la valeur après: {volume_after:.2e}"
                    )
                    interpolated_volume = volume_after
                elif volume_after is None:
                    stats["skipped_no_after"] += 1
                    logger.warning(
                        f"[{i}/{total_count}] {metric.timestamp.strftime('%Y-%m-%d %H:%M')} - "
                        f"Aucune valeur après, utilisation de la valeur avant: {volume_before:.2e}"
                    )
                    interpolated_volume = volume_before
                else:
                    # Interpolation complète
                    # Récupérer les timestamps des valeurs avant/après
                    before_metric = (
                        MacroMetric.select()
                        .where(
                            (MacroMetric.timestamp < metric.timestamp)
                            & (MacroMetric.global_volume_usd.is_null(False))
                        )
                        .order_by(MacroMetric.timestamp.desc())
                        .first()
                    )

                    after_metric = (
                        MacroMetric.select()
                        .where(
                            (MacroMetric.timestamp > metric.timestamp)
                            & (MacroMetric.global_volume_usd.is_null(False))
                        )
                        .order_by(MacroMetric.timestamp.asc())
                        .first()
                    )

                    interpolated_volume = interpolate_volume(
                        metric,
                        volume_before,
                        volume_after,
                        before_metric.timestamp,
                        after_metric.timestamp,
                    )

                # Sauvegarder ou afficher
                if dry_run:
                    logger.info(
                        f"[{i}/{total_count}] {metric.timestamp.strftime('%Y-%m-%d %H:%M')} - "
                        f"FGI={metric.fear_greed_index} - "
                        f"Volume interpolé: {interpolated_volume:.2e} USD"
                    )
                else:
                    metric.global_volume_usd = interpolated_volume
                    metric.save()
                    stats["filled"] += 1

                    if i % 10 == 0:  # Log tous les 10
                        logger.info(
                            f"Progression: {i}/{total_count} ({i * 100 // total_count}%) - "
                            f"{stats['filled']} remplis"
                        )

            except Exception as e:
                stats["errors"] += 1
                logger.error(f"Erreur traitement métrique {metric.id}: {e}")
                continue

        # Résumé final
        logger.info("\n" + "=" * 80)
        logger.info("TERMINÉ")
        logger.info("=" * 80)
        logger.info(f"Total traité: {total_count}")
        logger.info(f"  - Remplis: {stats['filled']}")
        logger.info(f"  - Sans valeur avant: {stats['skipped_no_before']}")
        logger.info(f"  - Sans valeur après: {stats['skipped_no_after']}")
        logger.info(f"  - Sans valeur avant ET après: {stats['skipped_both']}")
        logger.info(f"  - Erreurs: {stats['errors']}")

        if dry_run:
            logger.info("\n⚠️  MODE SIMULATION - Aucune modification n'a été faite")
            logger.info("Relancez sans --dry-run pour appliquer les changements")
        else:
            logger.info("\n✅ Modifications sauvegardées dans la base de données")

        logger.info("=" * 80)

    except Exception as e:
        logger.error(f"❌ Erreur fatale: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Interpoler les global_volume_usd manquants dans MacroMetric"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simule les changements sans modifier la base de données",
    )

    args = parser.parse_args()

    fill_missing_global_volumes(dry_run=args.dry_run)
