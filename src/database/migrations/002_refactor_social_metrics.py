"""
Migration 002: Refactor SocialMetric - Suppression des champs redondants (3NF)

Cette migration implémente la Phase 2 de l'architecture 3NF en supprimant les champs
redondants de SocialMetric qui sont maintenant dans PriceMetric et SignalMetric.

Champs SUPPRIMÉS:
- price_at_capture      → Maintenant dans PriceMetric
- trading_volume_h1     → Maintenant dans PriceMetric
- trading_volume_h24    → Maintenant dans PriceMetric
- liquidity_usd         → Maintenant dans PriceMetric
- z_score_price         → Maintenant dans SignalMetric
- divergence_score      → Maintenant dans SignalMetric

Champs CONSERVÉS:
- social_volume         → Métrique sociale pure
- social_density        → Métrique sociale pure
- z_score_raw           → Z-Score Social
- z_score_final         → Alias de z_score_raw

Usage:
    python src/database/migrations/002_refactor_social_metrics.py --execute
    python src/database/migrations/002_refactor_social_metrics.py --dry-run
"""

import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime

# Ajouter le répertoire racine au path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from src.database.models import db, SocialMetric
from src.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def migrate_social_metric_schema(dry_run: bool = False) -> bool:
    """
    Migre le schéma SocialMetric en supprimant les champs redondants.

    SQLite ne supporte pas ALTER TABLE DROP COLUMN (avant 3.35.0),
    donc on utilise la méthode de recréation de table:
    1. Créer nouvelle table avec bon schéma
    2. Copier les données (colonnes conservées)
    3. Supprimer ancienne table
    4. Renommer nouvelle table

    Args:
        dry_run (bool): Si True, simule sans modifier la DB

    Returns:
        bool: True si succès, False sinon
    """
    try:
        logger.info("\n" + "="*60)
        logger.info("🔄 MIGRATION 002: Refactor SocialMetric (3NF)")
        logger.info("="*60)

        if dry_run:
            logger.info("🔍 MODE DRY-RUN: Aucune modification de la DB")

        # Compter les entrées actuelles
        count_before = SocialMetric.select().count()
        logger.info(f"📊 SocialMetric actuels: {count_before} entrées")

        # Obtenir une connexion à la DB
        cursor = db.cursor()

        # Sauvegarder le schéma actuel
        logger.info("\n📋 Schéma SocialMetric AVANT migration:")
        cursor.execute("PRAGMA table_info(social_metrics)")
        columns_before = cursor.fetchall()
        for col in columns_before:
            logger.info(f"   - {col[1]} ({col[2]})")

        if dry_run:
            logger.info("\n✅ DRY-RUN terminé - Aucune modification appliquée")
            return True

        logger.info("\n🔧 Application de la migration...")

        # IMPORTANT: Désactiver temporairement les contraintes FK
        # (nécessaire car SignalMetric référence SocialMetric)
        logger.info("  🔓 Désactivation temporaire des contraintes FK...")
        cursor.execute("PRAGMA foreign_keys = OFF")

        # Nettoyage: Supprimer la table temporaire si elle existe (migration précédente échouée)
        cursor.execute("DROP TABLE IF EXISTS social_metrics_new")

        # ÉTAPE 1: Créer la nouvelle table avec schéma 3NF
        logger.info("  1️⃣ Création de la nouvelle table social_metrics_new...")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS social_metrics_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id INTEGER NOT NULL,
                timestamp DATETIME NOT NULL,

                -- Métriques sociales (UNIQUEMENT)
                social_volume REAL NOT NULL,
                social_density REAL NOT NULL,

                -- Z-Scores (conservés pour compatibilité legacy)
                z_score_raw REAL NOT NULL DEFAULT 0.0,
                z_score_final REAL NOT NULL DEFAULT 0.0,

                FOREIGN KEY (token_id) REFERENCES tokens(id) ON DELETE CASCADE,
                UNIQUE (token_id, timestamp)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_social_metrics_new_token
            ON social_metrics_new(token_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_social_metrics_new_timestamp
            ON social_metrics_new(timestamp)
        """)

        logger.info("     ✅ Nouvelle table créée")

        # ÉTAPE 2: Copier les données (seulement les colonnes conservées)
        logger.info("  2️⃣ Copie des données vers la nouvelle table...")

        cursor.execute("""
            INSERT INTO social_metrics_new
                (id, token_id, timestamp, social_volume, social_density, z_score_raw, z_score_final)
            SELECT
                id, token_id, timestamp, social_volume, social_density, z_score_raw, z_score_final
            FROM social_metrics
        """)

        count_copied = cursor.execute("SELECT COUNT(*) FROM social_metrics_new").fetchone()[0]
        logger.info(f"     ✅ {count_copied} entrées copiées")

        # Vérification
        if count_copied != count_before:
            logger.error(f"     ❌ Erreur: {count_before} entrées avant, {count_copied} après!")
            db.rollback()
            return False

        # ÉTAPE 3: Supprimer l'ancienne table
        logger.info("  3️⃣ Suppression de l'ancienne table...")
        cursor.execute("DROP TABLE social_metrics")
        logger.info("     ✅ Ancienne table supprimée")

        # ÉTAPE 4: Renommer la nouvelle table
        logger.info("  4️⃣ Renommage de la nouvelle table...")
        cursor.execute("ALTER TABLE social_metrics_new RENAME TO social_metrics")
        logger.info("     ✅ Table renommée")

        # ÉTAPE 5: Vérification finale
        logger.info("\n🔍 Vérification du nouveau schéma:")
        cursor.execute("PRAGMA table_info(social_metrics)")
        columns_after = cursor.fetchall()

        for col in columns_after:
            logger.info(f"   - {col[1]} ({col[2]})")

        count_after = cursor.execute("SELECT COUNT(*) FROM social_metrics").fetchone()[0]
        logger.info(f"\n📊 SocialMetric après migration: {count_after} entrées")

        # Réactiver les contraintes FK
        logger.info("  🔒 Réactivation des contraintes FK...")
        cursor.execute("PRAGMA foreign_keys = ON")

        # Commit la transaction
        db.commit()

        logger.info("\n" + "="*60)
        logger.info("✅ MIGRATION 002 TERMINÉE AVEC SUCCÈS")
        logger.info("="*60)
        logger.info(f"📊 Entrées conservées: {count_after}/{count_before}")
        logger.info(f"🗑️ Champs supprimés: price_at_capture, trading_volume_h1, trading_volume_h24, liquidity_usd, z_score_price, divergence_score")
        logger.info(f"✨ Architecture 3NF complète!")

        return True

    except Exception as e:
        logger.error(f"\n❌ ERREUR MIGRATION: {e}", exc_info=True)
        db.rollback()
        return False


def create_backup() -> str:
    """
    Crée une sauvegarde de la base de données avant migration.

    Returns:
        str: Chemin du fichier de backup
    """
    try:
        import shutil
        from src.config import DB_CONFIG

        db_path = Path(DB_CONFIG['name'])
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = db_path.parent / f"{db_path.stem}_backup_{timestamp}{db_path.suffix}"

        logger.info(f"\n💾 Création backup: {backup_path}")
        shutil.copy2(db_path, backup_path)
        logger.info(f"   ✅ Backup créé avec succès")

        return str(backup_path)

    except Exception as e:
        logger.error(f"   ❌ Erreur création backup: {e}")
        raise


def main():
    """Point d'entrée principal du script de migration."""
    parser = argparse.ArgumentParser(
        description='Migration 002: Refactor SocialMetric (3NF)'
    )
    parser.add_argument(
        '--execute',
        action='store_true',
        help='Exécuter la migration (ATTENTION: modifie la DB!)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Mode simulation sans modification'
    )
    parser.add_argument(
        '--no-backup',
        action='store_true',
        help='Skip la création du backup (non recommandé!)'
    )

    args = parser.parse_args()

    # Validation
    if not args.execute and not args.dry_run:
        parser.error("Vous devez spécifier --execute ou --dry-run")

    if args.execute and args.dry_run:
        parser.error("Vous ne pouvez pas utiliser --execute et --dry-run ensemble")

    # Avertissement pour --execute
    if args.execute:
        logger.warning("\n" + "⚠️ "*20)
        logger.warning("ATTENTION: Cette migration va MODIFIER le schéma de la base de données!")
        logger.warning("Les champs suivants seront SUPPRIMÉS de SocialMetric:")
        logger.warning("  - price_at_capture, trading_volume_h1, trading_volume_h24")
        logger.warning("  - liquidity_usd, z_score_price, divergence_score")
        logger.warning("⚠️ "*20)

        if not args.no_backup:
            logger.info("\n💾 Création d'un backup de sécurité...")
            try:
                backup_path = create_backup()
                logger.info(f"✅ Backup créé: {backup_path}")
            except Exception as e:
                logger.error(f"❌ Impossible de créer le backup: {e}")
                logger.error("Migration annulée par sécurité")
                return 1

        # Confirmation utilisateur
        response = input("\nContinuer avec la migration? (tapez 'OUI' pour confirmer): ")
        if response != 'OUI':
            logger.info("Migration annulée par l'utilisateur")
            return 0

    # Exécuter la migration
    success = migrate_social_metric_schema(dry_run=args.dry_run)

    if success:
        if not args.dry_run:
            logger.info("\n✅ Migration appliquée avec succès!")
            logger.info("⚠️ N'oubliez pas de mettre à jour models.py pour refléter le nouveau schéma!")
        return 0
    else:
        logger.error("\n❌ Migration échouée")
        return 1


if __name__ == '__main__':
    sys.exit(main())
