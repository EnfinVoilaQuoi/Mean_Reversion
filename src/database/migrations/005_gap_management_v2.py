"""
Migration 005: Refonte du Système de Gestion des Gaps

Cette migration ajoute un système granulaire de suivi des gaps avec:
1. Nouvelle table GapAttempt pour historiser toutes les tentatives de scraping
2. Modifications de ScrapedGap pour tracking de complétude et statut

NOUVEAU:

GapAttempt:
- Historique complet de chaque tentative de scraping par gap
- Traçabilité de la méthode utilisée (twitterio_top, twitterio_latest, playwright, twscrape)
- Instance Nitter utilisée pour Playwright
- Catégorisation des erreurs (auth, rate_limit, instance_unavailable, empty_result)

ScrapedGap (MODIFIÉ):
- Précision heure par heure au lieu de jour par jour
- Score de complétude (expected_hours, covered_hours, completeness_pct)
- Statut ('partial', 'complete', 'all_methods_exhausted', 'pending')
- Liste des méthodes essayées
- Timestamps first_attempt et last_attempt

OBJECTIF:
Éviter la perte de données en redécoupant les gaps partiellement remplis et permettre
un suivi granulaire des tentatives de scraping pour mieux gérer les échecs.

Usage:
    python src/database/migrations/005_gap_management_v2.py --execute
    python src/database/migrations/005_gap_management_v2.py --dry-run
"""

import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime

# Ajouter le répertoire racine au path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from src.database.models import db, ScrapedGap
from src.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def migrate_gap_management_v2(dry_run: bool = False) -> bool:
    """
    Refonte du système de gestion des gaps.

    Stratégie:
    1. Créer la nouvelle table GapAttempt
    2. Migrer ScrapedGap vers nouvelle structure avec précision heure
    3. Initialiser les nouveaux champs avec valeurs par défaut

    Args:
        dry_run (bool): Si True, simule sans modifier la DB

    Returns:
        bool: True si succès, False sinon
    """
    try:
        logger.info("\n" + "="*80)
        logger.info("MIGRATION 005: Refonte Systeme de Gestion des Gaps")
        logger.info("="*80)

        if dry_run:
            logger.info("MODE DRY-RUN: Aucune modification de la DB")

        # Compter les gaps actuels
        scraped_gap_count = ScrapedGap.select().count()
        logger.info(f"ScrapedGap actuels: {scraped_gap_count} entrees")

        # Obtenir une connexion à la DB
        cursor = db.cursor()

        # ========================================================================
        # PARTIE 1: CRÉATION TABLE GapAttempt
        # ========================================================================
        logger.info("\n" + "-"*80)
        logger.info("PARTIE 1: CREATION TABLE GapAttempt")
        logger.info("-"*80)

        # Vérifier si la table existe déjà
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='gap_attempts'
        """)
        table_exists = cursor.fetchone() is not None

        if table_exists:
            logger.warning("\nLa table 'gap_attempts' existe deja!")
            logger.info("Migration 005 probablement deja appliquee pour GapAttempt.")
        else:
            if dry_run:
                logger.info("\nTable a creer:")
                logger.info("""
                CREATE TABLE gap_attempts (
                    id INTEGER PRIMARY KEY,
                    token_id INTEGER NOT NULL,
                    since_date VARCHAR(16) NOT NULL,     -- YYYY-MM-DD HH:00:00
                    until_date VARCHAR(16) NOT NULL,     -- YYYY-MM-DD HH:00:00
                    method VARCHAR(20) NOT NULL,         -- Method de scraping
                    nitter_instance VARCHAR(255) NULL,   -- Instance Nitter si Playwright
                    attempted_at TIMESTAMP NOT NULL,
                    success BOOLEAN NOT NULL,
                    tweet_count INTEGER DEFAULT 0,
                    error_type VARCHAR(30) NULL,         -- Type erreur si echec
                    error_message TEXT NULL,             -- Message erreur complet
                    FOREIGN KEY (token_id) REFERENCES tokens(id)
                )
                """)
                logger.info("\nIndex a creer:")
                logger.info("   - idx_gap_attempts_token (token_id)")
                logger.info("   - idx_gap_attempts_dates (token_id, since_date, until_date)")
                logger.info("   - idx_gap_attempts_method (method)")
            else:
                logger.info("\nCreation de la table gap_attempts...")

                cursor.execute("""
                    CREATE TABLE gap_attempts (
                        id INTEGER NOT NULL PRIMARY KEY,
                        token_id INTEGER NOT NULL,
                        since_date VARCHAR(16) NOT NULL,
                        until_date VARCHAR(16) NOT NULL,
                        method VARCHAR(20) NOT NULL,
                        nitter_instance VARCHAR(255),
                        attempted_at TIMESTAMP NOT NULL,
                        success BOOLEAN NOT NULL,
                        tweet_count INTEGER DEFAULT 0,
                        error_type VARCHAR(30),
                        error_message TEXT,
                        FOREIGN KEY (token_id) REFERENCES tokens(id)
                    )
                """)
                logger.info("   Table gap_attempts creee")

                # Créer les index
                logger.info("\nCreation des index gap_attempts...")

                cursor.execute("""
                    CREATE INDEX idx_gap_attempts_token
                    ON gap_attempts(token_id)
                """)
                logger.info("   Index idx_gap_attempts_token cree")

                cursor.execute("""
                    CREATE INDEX idx_gap_attempts_dates
                    ON gap_attempts(token_id, since_date, until_date)
                """)
                logger.info("   Index idx_gap_attempts_dates cree")

                cursor.execute("""
                    CREATE INDEX idx_gap_attempts_method
                    ON gap_attempts(method)
                """)
                logger.info("   Index idx_gap_attempts_method cree")

        # ========================================================================
        # PARTIE 2: MIGRATION ScrapedGap
        # ========================================================================
        logger.info("\n" + "-"*80)
        logger.info("PARTIE 2: MIGRATION ScrapedGap vers nouvelle structure")
        logger.info("-"*80)

        # Vérifier le schéma actuel
        logger.info("\nSchema ScrapedGap AVANT migration:")
        cursor.execute("PRAGMA table_info(scraped_gaps)")
        columns_before = cursor.fetchall()
        column_names_before = [col[1] for col in columns_before]

        for col in columns_before:
            logger.info(f"   - {col[1]} ({col[2]})")

        # Vérifier si migration déjà appliquée
        if 'expected_hours' in column_names_before:
            logger.warning("\nLes nouvelles colonnes existent deja!")
            logger.info("Migration 005 probablement deja appliquee pour ScrapedGap.")
        else:
            if dry_run:
                logger.info("\nColonnes a ajouter a ScrapedGap:")
                logger.info("   - expected_hours (INTEGER, default=0)")
                logger.info("   - covered_hours (INTEGER, default=0)")
                logger.info("   - completeness_pct (FLOAT, default=0.0)")
                logger.info("   - status (VARCHAR, default='pending')")
                logger.info("   - methods_tried (VARCHAR, default='')")
                logger.info("   - first_attempt (TIMESTAMP, nullable)")
                logger.info("   - last_attempt (TIMESTAMP, nullable)")
                logger.info("\nMigration des dates vers precision heure:")
                logger.info("   - since_date: YYYY-MM-DD -> YYYY-MM-DD 00:00:00")
                logger.info("   - until_date: YYYY-MM-DD -> YYYY-MM-DD 23:00:00")
            else:
                logger.info("\nApplication migration ScrapedGap...")

                # ÉTAPE 1: Ajouter les nouveaux champs
                logger.info("  1. Ajout des nouveaux champs...")

                cursor.execute("""
                    ALTER TABLE scraped_gaps
                    ADD COLUMN expected_hours INTEGER NOT NULL DEFAULT 0
                """)
                logger.info("     Colonne 'expected_hours' ajoutee")

                cursor.execute("""
                    ALTER TABLE scraped_gaps
                    ADD COLUMN covered_hours INTEGER NOT NULL DEFAULT 0
                """)
                logger.info("     Colonne 'covered_hours' ajoutee")

                cursor.execute("""
                    ALTER TABLE scraped_gaps
                    ADD COLUMN completeness_pct FLOAT NOT NULL DEFAULT 0.0
                """)
                logger.info("     Colonne 'completeness_pct' ajoutee")

                cursor.execute("""
                    ALTER TABLE scraped_gaps
                    ADD COLUMN status VARCHAR(30) NOT NULL DEFAULT 'pending'
                """)
                logger.info("     Colonne 'status' ajoutee")

                cursor.execute("""
                    ALTER TABLE scraped_gaps
                    ADD COLUMN methods_tried VARCHAR(100) NOT NULL DEFAULT ''
                """)
                logger.info("     Colonne 'methods_tried' ajoutee")

                cursor.execute("""
                    ALTER TABLE scraped_gaps
                    ADD COLUMN first_attempt TIMESTAMP NULL
                """)
                logger.info("     Colonne 'first_attempt' ajoutee")

                cursor.execute("""
                    ALTER TABLE scraped_gaps
                    ADD COLUMN last_attempt TIMESTAMP NULL
                """)
                logger.info("     Colonne 'last_attempt' ajoutee")

                # ÉTAPE 2: Migrer les données existantes
                logger.info("\n  2. Migration des donnees existantes...")

                # Récupérer tous les gaps existants
                cursor.execute("""
                    SELECT id, since_date, until_date, scraped_at, tweet_count
                    FROM scraped_gaps
                """)
                existing_gaps = cursor.fetchall()

                logger.info(f"     Migration de {len(existing_gaps)} gaps...")

                for gap in existing_gaps:
                    gap_id, since_date, until_date, scraped_at, tweet_count = gap

                    # Convertir dates YYYY-MM-DD vers YYYY-MM-DD HH:00:00
                    # On suppose que les gaps de jour entier vont de 00:00 à 23:00
                    try:
                        # Parser les dates
                        from datetime import datetime as dt, timedelta

                        since_dt = dt.strptime(since_date, '%Y-%m-%d')
                        until_dt = dt.strptime(until_date, '%Y-%m-%d')

                        # Calculer nombre d'heures (jour complet = 24h)
                        days_diff = (until_dt - since_dt).days + 1
                        expected_hours = days_diff * 24

                        # Nouvelles dates avec précision heure
                        new_since = since_dt.strftime('%Y-%m-%d 00:00:00')
                        new_until = (until_dt + timedelta(hours=23)).strftime('%Y-%m-%d 23:00:00')

                        # Pour les gaps existants, on suppose qu'ils sont complets
                        # (car ils ont été scrapés avec l'ancien système)
                        covered_hours = expected_hours
                        completeness = 100.0
                        status = 'complete'

                        # Mettre à jour le gap
                        cursor.execute("""
                            UPDATE scraped_gaps
                            SET since_date = ?,
                                until_date = ?,
                                expected_hours = ?,
                                covered_hours = ?,
                                completeness_pct = ?,
                                status = ?,
                                first_attempt = ?,
                                last_attempt = ?
                            WHERE id = ?
                        """, (new_since, new_until, expected_hours, covered_hours,
                              completeness, status, scraped_at, scraped_at, gap_id))

                    except Exception as e:
                        logger.warning(f"     Erreur migration gap {gap_id}: {e}")
                        # Continuer avec les autres gaps
                        continue

                logger.info(f"     Migration des donnees terminee")

        # ========================================================================
        # VÉRIFICATIONS FINALES
        # ========================================================================
        if dry_run:
            logger.info("\nDRY-RUN termine - Aucune modification appliquee")
            logger.info("\nQuand vous executerez la migration:")
            logger.info("  - Nouvelle table: gap_attempts")
            logger.info("  - ScrapedGap: +7 colonnes (complétude, statut, etc.)")
            logger.info("  - Gaps existants migrés vers précision heure")
            return True

        logger.info("\n" + "-"*80)
        logger.info("VERIFICATION FINALE")
        logger.info("-"*80)

        # Vérifier GapAttempt
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='gap_attempts'
        """)
        if not cursor.fetchone():
            logger.error("Erreur: Table gap_attempts non creee!")
            db.rollback()
            return False

        logger.info("\nTable gap_attempts creee avec succes")

        # Vérifier ScrapedGap
        logger.info("\nSchema ScrapedGap APRES migration:")
        cursor.execute("PRAGMA table_info(scraped_gaps)")
        columns_after = cursor.fetchall()
        column_names_after = [col[1] for col in columns_after]

        for col in columns_after:
            logger.info(f"   - {col[1]} ({col[2]})")

        scraped_gap_count_after = cursor.execute("SELECT COUNT(*) FROM scraped_gaps").fetchone()[0]
        logger.info(f"\nScrapedGap apres migration: {scraped_gap_count_after} entrees")

        # Vérifier que toutes les colonnes ont bien été ajoutées
        required_columns = ['expected_hours', 'covered_hours', 'completeness_pct',
                            'status', 'methods_tried', 'first_attempt', 'last_attempt']

        for col in required_columns:
            if col not in column_names_after:
                logger.error(f"Erreur: Colonne ScrapedGap '{col}' manquante!")
                db.rollback()
                return False

        # Commit la transaction
        db.commit()

        logger.info("\n" + "="*80)
        logger.info("MIGRATION 005 TERMINEE AVEC SUCCES")
        logger.info("="*80)
        logger.info(f"ScrapedGap: {scraped_gap_count_after}/{scraped_gap_count} entrees migrees")
        logger.info(f"\nNouvelle table creee:")
        logger.info(f"   - gap_attempts: Historique complet des tentatives")
        logger.info(f"\nColonnes ajoutees ScrapedGap:")
        logger.info(f"   - expected_hours: Nombre d'heures attendues")
        logger.info(f"   - covered_hours: Nombre d'heures avec tweets")
        logger.info(f"   - completeness_pct: Pourcentage de completude")
        logger.info(f"   - status: Statut du gap (partial/complete/exhausted/pending)")
        logger.info(f"   - methods_tried: Liste des methodes essayees")
        logger.info(f"   - first_attempt: Date de premiere tentative")
        logger.info(f"   - last_attempt: Date de derniere tentative")
        logger.info(f"\nPrecision des dates:")
        logger.info(f"   - since_date/until_date: YYYY-MM-DD HH:00:00 (heure par heure)")
        logger.info(f"\nSysteme granulaire de gaps pret!")

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
        backup_path = db_path.parent / f"{db_path.stem}_backup_mig005_{timestamp}{db_path.suffix}"

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
        description='Migration 005: Refonte Système de Gestion des Gaps'
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
        logger.warning("Nouvelle table creee:")
        logger.warning("  - gap_attempts (historique tentatives scraping)")
        logger.warning("Colonnes ajoutees a ScrapedGap:")
        logger.warning("  - expected_hours, covered_hours, completeness_pct")
        logger.warning("  - status, methods_tried, first_attempt, last_attempt")
        logger.warning("Migration des dates vers precision heure (YYYY-MM-DD HH:00:00)")
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
    success = migrate_gap_management_v2(dry_run=args.dry_run)

    if success:
        if not args.dry_run:
            logger.info("\nMigration appliquee avec succes!")
            logger.info("N'oubliez pas de mettre a jour models.py pour ajouter:")
            logger.info("  - Classe GapAttempt")
            logger.info("  - Nouveaux champs dans ScrapedGap")
        return 0
    else:
        logger.error("\nMigration echouee")
        return 1


if __name__ == '__main__':
    sys.exit(main())
