"""
Migration 003: Ajout des flags d'imputation pour SocialMetric

Cette migration ajoute les champs nécessaires pour tracer et gérer l'imputation des données:
- is_imputed: Flag indiquant si la métrique est imputée (vs scrapée)
- imputation_method: Méthode utilisée pour l'imputation (linear_interpolation, etc.)
- updated_at: Timestamp de dernière mise à jour (pour tracer remplacement imputé → réel)

OBJECTIF:
Permettre l'imputation légère des micro-gaps (<3h) tout en maintenant la traçabilité
et en garantissant que les vraies données écrasent toujours les imputées.

Usage:
    python src/database/migrations/003_add_imputation_flags.py --execute
    python src/database/migrations/003_add_imputation_flags.py --dry-run
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


def migrate_add_imputation_flags(dry_run: bool = False) -> bool:
    """
    Ajoute les champs is_imputed, imputation_method, et updated_at à SocialMetric.

    SQLite supporte ALTER TABLE ADD COLUMN, donc pas besoin de recréer la table.

    Args:
        dry_run (bool): Si True, simule sans modifier la DB

    Returns:
        bool: True si succès, False sinon
    """
    try:
        logger.info("\n" + "="*60)
        logger.info("🔄 MIGRATION 003: Ajout des flags d'imputation")
        logger.info("="*60)

        if dry_run:
            logger.info("🔍 MODE DRY-RUN: Aucune modification de la DB")

        # Compter les entrées actuelles
        count_before = SocialMetric.select().count()
        logger.info(f"📊 SocialMetric actuels: {count_before} entrées")

        # Obtenir une connexion à la DB
        cursor = db.cursor()

        # Vérifier le schéma actuel
        logger.info("\n📋 Schéma SocialMetric AVANT migration:")
        cursor.execute("PRAGMA table_info(social_metrics)")
        columns_before = cursor.fetchall()
        column_names_before = [col[1] for col in columns_before]

        for col in columns_before:
            logger.info(f"   - {col[1]} ({col[2]})")

        # Vérifier si les colonnes existent déjà
        if 'is_imputed' in column_names_before:
            logger.warning("\n⚠️ La colonne 'is_imputed' existe déjà!")
            logger.info("Migration probablement déjà appliquée.")
            return True

        if dry_run:
            logger.info("\n✨ Colonnes à ajouter:")
            logger.info("   - is_imputed (BOOLEAN, default=FALSE)")
            logger.info("   - imputation_method (VARCHAR, nullable)")
            logger.info("   - updated_at (DATETIME, nullable)")
            logger.info("\n✅ DRY-RUN terminé - Aucune modification appliquée")
            return True

        logger.info("\n🔧 Application de la migration...")

        # ÉTAPE 1: Ajouter la colonne is_imputed
        logger.info("  1️⃣ Ajout de la colonne 'is_imputed'...")
        cursor.execute("""
            ALTER TABLE social_metrics
            ADD COLUMN is_imputed BOOLEAN NOT NULL DEFAULT 0
        """)
        logger.info("     ✅ Colonne 'is_imputed' ajoutée")

        # ÉTAPE 2: Ajouter la colonne imputation_method
        logger.info("  2️⃣ Ajout de la colonne 'imputation_method'...")
        cursor.execute("""
            ALTER TABLE social_metrics
            ADD COLUMN imputation_method VARCHAR(50) NULL
        """)
        logger.info("     ✅ Colonne 'imputation_method' ajoutée")

        # ÉTAPE 3: Ajouter la colonne updated_at
        logger.info("  3️⃣ Ajout de la colonne 'updated_at'...")
        cursor.execute("""
            ALTER TABLE social_metrics
            ADD COLUMN updated_at DATETIME NULL
        """)
        logger.info("     ✅ Colonne 'updated_at' ajoutée")

        # ÉTAPE 4: Vérification finale
        logger.info("\n🔍 Vérification du nouveau schéma:")
        cursor.execute("PRAGMA table_info(social_metrics)")
        columns_after = cursor.fetchall()

        for col in columns_after:
            logger.info(f"   - {col[1]} ({col[2]})")

        count_after = cursor.execute("SELECT COUNT(*) FROM social_metrics").fetchone()[0]
        logger.info(f"\n📊 SocialMetric après migration: {count_after} entrées")

        # Vérification que toutes les colonnes ont bien été ajoutées
        column_names_after = [col[1] for col in columns_after]
        required_columns = ['is_imputed', 'imputation_method', 'updated_at']

        for col in required_columns:
            if col not in column_names_after:
                logger.error(f"❌ Erreur: Colonne '{col}' manquante!")
                db.rollback()
                return False

        # Commit la transaction
        db.commit()

        logger.info("\n" + "="*60)
        logger.info("✅ MIGRATION 003 TERMINÉE AVEC SUCCÈS")
        logger.info("="*60)
        logger.info(f"📊 Entrées conservées: {count_after}/{count_before}")
        logger.info(f"✨ Colonnes ajoutées:")
        logger.info(f"   - is_imputed (BOOLEAN): Track si métrique imputée")
        logger.info(f"   - imputation_method (VARCHAR): Méthode d'imputation")
        logger.info(f"   - updated_at (DATETIME): Timestamp de mise à jour")
        logger.info(f"🎯 Système d'imputation traçable prêt!")

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
        description='Migration 003: Ajout des flags d\'imputation'
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
        logger.warning("Les colonnes suivantes seront AJOUTÉES à SocialMetric:")
        logger.warning("  - is_imputed (BOOLEAN, default=FALSE)")
        logger.warning("  - imputation_method (VARCHAR, nullable)")
        logger.warning("  - updated_at (DATETIME, nullable)")
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
    success = migrate_add_imputation_flags(dry_run=args.dry_run)

    if success:
        if not args.dry_run:
            logger.info("\n✅ Migration appliquée avec succès!")
            logger.info("⚠️ N'oubliez pas de mettre à jour models.py pour ajouter les nouveaux champs!")
        return 0
    else:
        logger.error("\n❌ Migration échouée")
        return 1


if __name__ == '__main__':
    sys.exit(main())
