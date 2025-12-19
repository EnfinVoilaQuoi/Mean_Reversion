"""
Migration 004: Ajout du Dual Z-Score et flags de validation

Cette migration ajoute les champs nécessaires pour:
1. Dual Z-Score System (z_score_vs_activity dans SignalMetric)
2. Validation des données (is_valid, collection_method, data_quality dans SocialMetric)

CHAMPS AJOUTÉS:

SocialMetric:
- is_valid: Flag pour marquer les métriques incohérentes (BOOLEAN, default TRUE)
- collection_method: Source de la donnée ('scraping', 'webhook', 'imputed')
- data_quality: Qualité de la donnée ('high', 'uncertain', 'low')

SignalMetric:
- z_score_vs_activity: Z-Score vs activité réelle (exclut epsilon)

OBJECTIF:
Permettre le calcul de deux Z-Scores complémentaires et filtrer les données de mauvaise
qualité (epsilon, anomalies) des baselines pour des signaux plus fiables.

Usage:
    python src/database/migrations/004_add_dual_zscore_and_validation.py --execute
    python src/database/migrations/004_add_dual_zscore_and_validation.py --dry-run
"""

import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime

# Ajouter le répertoire racine au path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from src.database.models import db, SocialMetric, SignalMetric
from src.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def migrate_add_dual_zscore_and_validation(dry_run: bool = False) -> bool:
    """
    Ajoute les champs pour dual Z-Score et validation des données.

    SQLite supporte ALTER TABLE ADD COLUMN, donc pas besoin de recréer les tables.

    Args:
        dry_run (bool): Si True, simule sans modifier la DB

    Returns:
        bool: True si succès, False sinon
    """
    try:
        logger.info("\n" + "="*80)
        logger.info("MIGRATION 004: Dual Z-Score + Validation des Donnees")
        logger.info("="*80)

        if dry_run:
            logger.info("MODE DRY-RUN: Aucune modification de la DB")

        # Compter les entrées actuelles
        social_count = SocialMetric.select().count()
        signal_count = SignalMetric.select().count()
        logger.info(f"SocialMetric actuels: {social_count} entrees")
        logger.info(f"SignalMetric actuels: {signal_count} entrees")

        # Obtenir une connexion à la DB
        cursor = db.cursor()

        # ========================================================================
        # PARTIE 1: MODIFICATIONS SocialMetric
        # ========================================================================
        logger.info("\n" + "-"*80)
        logger.info("PARTIE 1: SOCIALMETRIC - Ajout flags validation")
        logger.info("-"*80)

        # Vérifier le schéma actuel SocialMetric
        logger.info("\nSchema SocialMetric AVANT migration:")
        cursor.execute("PRAGMA table_info(social_metrics)")
        columns_social_before = cursor.fetchall()
        column_names_social_before = [col[1] for col in columns_social_before]

        for col in columns_social_before:
            logger.info(f"   - {col[1]} ({col[2]})")

        # Vérifier si les colonnes existent déjà
        if 'is_valid' in column_names_social_before:
            logger.warning("\nLa colonne 'is_valid' existe deja!")
            logger.info("Migration 004 probablement deja appliquee pour SocialMetric.")
            # Continue quand même pour vérifier SignalMetric
        else:
            if dry_run:
                logger.info("\nColonnes a ajouter a SocialMetric:")
                logger.info("   - is_valid (BOOLEAN, default=TRUE)")
                logger.info("   - collection_method (VARCHAR, default='scraping')")
                logger.info("   - data_quality (VARCHAR, default='uncertain')")
            else:
                logger.info("\nApplication migration SocialMetric...")

                # ÉTAPE 1: Ajouter is_valid
                logger.info("  1 Ajout de la colonne 'is_valid'...")
                cursor.execute("""
                    ALTER TABLE social_metrics
                    ADD COLUMN is_valid BOOLEAN NOT NULL DEFAULT 1
                """)
                logger.info("     Colonne 'is_valid' ajoutee")

                # ÉTAPE 2: Ajouter collection_method
                logger.info("  2 Ajout de la colonne 'collection_method'...")
                cursor.execute("""
                    ALTER TABLE social_metrics
                    ADD COLUMN collection_method VARCHAR(20) NOT NULL DEFAULT 'scraping'
                """)
                logger.info("     Colonne 'collection_method' ajoutee")

                # ÉTAPE 3: Ajouter data_quality
                logger.info("  3 Ajout de la colonne 'data_quality'...")
                cursor.execute("""
                    ALTER TABLE social_metrics
                    ADD COLUMN data_quality VARCHAR(20) NOT NULL DEFAULT 'uncertain'
                """)
                logger.info("     Colonne 'data_quality' ajoutee")

        # ========================================================================
        # PARTIE 2: MODIFICATIONS SignalMetric
        # ========================================================================
        logger.info("\n" + "-"*80)
        logger.info("PARTIE 2: SIGNALMETRIC - Ajout Z-Score vs Activity")
        logger.info("-"*80)

        # Vérifier le schéma actuel SignalMetric
        logger.info("\nSchema SignalMetric AVANT migration:")
        cursor.execute("PRAGMA table_info(signal_metrics)")
        columns_signal_before = cursor.fetchall()
        column_names_signal_before = [col[1] for col in columns_signal_before]

        for col in columns_signal_before:
            logger.info(f"   - {col[1]} ({col[2]})")

        # Vérifier si la colonne existe déjà
        if 'z_score_vs_activity' in column_names_signal_before:
            logger.warning("\nLa colonne 'z_score_vs_activity' existe deja!")
            logger.info("Migration 004 probablement deja appliquee pour SignalMetric.")
        else:
            if dry_run:
                logger.info("\nColonnes a ajouter a SignalMetric:")
                logger.info("   - z_score_vs_activity (FLOAT, nullable)")
            else:
                logger.info("\nApplication migration SignalMetric...")

                # ÉTAPE 4: Ajouter z_score_vs_activity
                logger.info("  4 Ajout de la colonne 'z_score_vs_activity'...")
                cursor.execute("""
                    ALTER TABLE signal_metrics
                    ADD COLUMN z_score_vs_activity FLOAT NULL
                """)
                logger.info("     Colonne 'z_score_vs_activity' ajoutee")

        # ========================================================================
        # VÉRIFICATIONS FINALES
        # ========================================================================
        if dry_run:
            logger.info("\nDRY-RUN termine - Aucune modification appliquee")
            logger.info("\nQuand vous executerez la migration:")
            logger.info("  - SocialMetric: +3 colonnes (is_valid, collection_method, data_quality)")
            logger.info("  - SignalMetric: +1 colonne (z_score_vs_activity)")
            return True

        logger.info("\n" + "-"*80)
        logger.info("VERIFICATION FINALE")
        logger.info("-"*80)

        # Vérification SocialMetric
        logger.info("\nSchema SocialMetric APRES migration:")
        cursor.execute("PRAGMA table_info(social_metrics)")
        columns_social_after = cursor.fetchall()
        column_names_social_after = [col[1] for col in columns_social_after]

        for col in columns_social_after:
            logger.info(f"   - {col[1]} ({col[2]})")

        social_count_after = cursor.execute("SELECT COUNT(*) FROM social_metrics").fetchone()[0]
        logger.info(f"\nSocialMetric apres migration: {social_count_after} entrees")

        # Vérification SignalMetric
        logger.info("\nSchema SignalMetric APRES migration:")
        cursor.execute("PRAGMA table_info(signal_metrics)")
        columns_signal_after = cursor.fetchall()
        column_names_signal_after = [col[1] for col in columns_signal_after]

        for col in columns_signal_after:
            logger.info(f"   - {col[1]} ({col[2]})")

        signal_count_after = cursor.execute("SELECT COUNT(*) FROM signal_metrics").fetchone()[0]
        logger.info(f"\nSignalMetric apres migration: {signal_count_after} entrees")

        # Vérifier que toutes les colonnes ont bien été ajoutées
        required_social_columns = ['is_valid', 'collection_method', 'data_quality']
        required_signal_columns = ['z_score_vs_activity']

        for col in required_social_columns:
            if col not in column_names_social_after:
                logger.error(f"Erreur: Colonne SocialMetric '{col}' manquante!")
                db.rollback()
                return False

        for col in required_signal_columns:
            if col not in column_names_signal_after:
                logger.error(f"Erreur: Colonne SignalMetric '{col}' manquante!")
                db.rollback()
                return False

        # Commit la transaction
        db.commit()

        logger.info("\n" + "="*80)
        logger.info("MIGRATION 004 TERMINEE AVEC SUCCES")
        logger.info("="*80)
        logger.info(f"SocialMetric: {social_count_after}/{social_count} entrees conservees")
        logger.info(f"SignalMetric: {signal_count_after}/{signal_count} entrees conservees")
        logger.info(f"\nColonnes ajoutees SocialMetric:")
        logger.info(f"   - is_valid (BOOLEAN): Valide si pas d'incoherence")
        logger.info(f"   - collection_method (VARCHAR): Source ('scraping', 'webhook', 'imputed')")
        logger.info(f"   - data_quality (VARCHAR): Qualite ('high', 'uncertain', 'low')")
        logger.info(f"\nColonnes ajoutees SignalMetric:")
        logger.info(f"   - z_score_vs_activity (FLOAT): Z-Score vs activite reelle (filtre epsilon)")
        logger.info(f"\nSysteme dual Z-Score pret!")

        return True

    except Exception as e:
        logger.error(f"\nERREUR MIGRATION: {e}", exc_info=True)
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
        backup_path = db_path.parent / f"{db_path.stem}_backup_mig004_{timestamp}{db_path.suffix}"

        logger.info(f"\nCreation backup: {backup_path}")
        shutil.copy2(db_path, backup_path)
        logger.info(f"   Backup cree avec succes")

        return str(backup_path)

    except Exception as e:
        logger.error(f"   Erreur creation backup: {e}")
        raise


def main():
    """Point d'entrée principal du script de migration."""
    parser = argparse.ArgumentParser(
        description='Migration 004: Dual Z-Score + Validation'
    )
    parser.add_argument(
        '--execute',
        action='store_true',
        help='Executer la migration (ATTENTION: modifie la DB!)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Mode simulation sans modification'
    )
    parser.add_argument(
        '--no-backup',
        action='store_true',
        help='Skip la creation du backup (non recommande!)'
    )

    args = parser.parse_args()

    # Validation
    if not args.execute and not args.dry_run:
        parser.error("Vous devez specifier --execute ou --dry-run")

    if args.execute and args.dry_run:
        parser.error("Vous ne pouvez pas utiliser --execute et --dry-run ensemble")

    # Avertissement pour --execute
    if args.execute:
        logger.warning("\n" + "WARNING "*10)
        logger.warning("ATTENTION: Cette migration va MODIFIER le schema de la base de donnees!")
        logger.warning("Colonnes ajoutees a SocialMetric:")
        logger.warning("  - is_valid (BOOLEAN, default=TRUE)")
        logger.warning("  - collection_method (VARCHAR, default='scraping')")
        logger.warning("  - data_quality (VARCHAR, default='uncertain')")
        logger.warning("Colonnes ajoutees a SignalMetric:")
        logger.warning("  - z_score_vs_activity (FLOAT, nullable)")
        logger.warning("WARNING "*10)

        if not args.no_backup:
            logger.info("\nCreation d'un backup de securite...")
            try:
                backup_path = create_backup()
                logger.info(f"Backup cree: {backup_path}")
            except Exception as e:
                logger.error(f"Impossible de creer le backup: {e}")
                logger.error("Migration annulee par securite")
                return 1

        # Confirmation utilisateur
        response = input("\nContinuer avec la migration? (tapez 'OUI' pour confirmer): ")
        if response != 'OUI':
            logger.info("Migration annulee par l'utilisateur")
            return 0

    # Exécuter la migration
    success = migrate_add_dual_zscore_and_validation(dry_run=args.dry_run)

    if success:
        if not args.dry_run:
            logger.info("\nMigration appliquee avec succes!")
            logger.info("N'oubliez pas de mettre a jour models.py pour ajouter les nouveaux champs!")
        return 0
    else:
        logger.error("\nMigration echouee")
        return 1


if __name__ == '__main__':
    sys.exit(main())
