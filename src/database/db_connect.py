"""
Gestion de la connexion à la base de données SQLite.
Configure le mode WAL pour la concurrence et fournit les fonctions d'initialisation.
"""

import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from peewee import SqliteDatabase

from ..config import DB_CONFIG

logger = logging.getLogger(__name__)


# ============================================================================
# TRANSACTION CONTEXT MANAGER
# ============================================================================


@contextmanager
def safe_transaction():
    """
    Context manager pour les transactions avec rollback automatique.

    Exemple:
        with safe_transaction():
            Token.create(cashtag="$BTC", ...)
            RawTweet.create(token=token, ...)

    En cas d'erreur, rollback automatique via db.atomic()
    """
    try:
        with db.atomic():
            logger.debug("🔄 Début transaction")
            yield
            logger.debug("✅ Commit transaction")

    except Exception as e:
        logger.error(f"❌ Erreur transaction: {type(e).__name__}: {e}")
        # Rollback automatique via atomic()
        raise


# ============================================================================
# CONFIGURATION DE LA CONNEXION
# ============================================================================


def get_database():
    """
    Crée et retourne une instance de la base de données SQLite.
    Configure automatiquement le mode WAL et les optimisations.

    Returns:
        SqliteDatabase: Instance de connexion à la base de données
    """
    return SqliteDatabase(DB_CONFIG["name"], pragmas=DB_CONFIG["pragmas"])


# Instance globale de la base de données
db = get_database()


# ============================================================================
# FONCTIONS D'INITIALISATION
# ============================================================================


def initialize_db():
    """
    Initialise la base de données : crée les tables et configure les pragmas.
    À appeler au démarrage de l'application (dans main.py).

    Cette fonction :
    1. Vérifie que le dossier data/ existe
    2. Crée la connexion avec mode WAL
    3. Crée toutes les tables si elles n'existent pas
    4. Affiche un rapport de status

    Returns:
        bool: True si l'initialisation réussit, False sinon
    """
    try:
        # Vérifier que le dossier data existe
        db_path = Path(DB_CONFIG["name"])
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # Connexion à la DB
        if not db.is_closed():
            db.close()
        db.connect(reuse_if_open=True)

        print(f"📂 Base de données : {DB_CONFIG['name']}")
        print(
            f"🔧 Mode WAL : {'✅ Activé' if DB_CONFIG['pragmas'].get('journal_mode') == 'wal' else '❌ Désactivé'}"
        )

        # Import des modèles et création des tables
        from src.database.models import (
            MacroMetric,
            PriceMetric,
            ProjectFollowers,
            RawTweet,
            SignalLog,
            SocialMetric,
            Token,
        )

        db.create_tables(
            [
                Token,
                RawTweet,
                ProjectFollowers,
                SocialMetric,
                PriceMetric,
                SignalLog,
                MacroMetric,
            ],
            safe=True,
        )
        print(
            "✅ Tables créées/vérifiées : Token, RawTweet, ProjectFollowers, SocialMetric, PriceMetric, SignalLog, MacroMetric"
        )

        # Vérification de l'intégrité
        cursor = db.execute_sql("PRAGMA integrity_check;")
        integrity_result = cursor.fetchone()[0]

        if integrity_result == "ok":
            print("✅ Intégrité de la base : OK")
        else:
            print(f"⚠️  Problème d'intégrité : {integrity_result}")
            return False

        # Afficher les statistiques
        stats = get_db_stats()
        print("📊 Statistiques :")
        print(f"   - Tokens : {stats['tokens']}")
        print(f"   - Métriques : {stats['metrics']}")
        print(f"   - Signaux : {stats['signals']}")
        print(f"   - Macro : {stats['macro']}")
        print(f"   - Taille DB : {stats['size_mb']:.2f} MB")

        return True

    except Exception as e:
        print(f"❌ Erreur lors de l'initialisation de la DB : {e}")
        import traceback

        traceback.print_exc()
        return False


# ============================================================================
# GESTION DE LA CONNEXION AVEC RETRY
# ============================================================================


def connect_db_with_retry(max_retries: int = 3, timeout_seconds: int = 10) -> bool:
    """
    Ouvre la connexion à la base de données avec retry automatique.

    Args:
        max_retries: Nombre max de tentatives
        timeout_seconds: Timeout pour chaque tentative

    Returns:
        bool: True si la connexion est réussie
    """
    if not db.is_closed():
        return True

    for attempt in range(max_retries):
        try:
            logger.info(f"🔌 Tentative connexion DB {attempt + 1}/{max_retries}")

            # Close et reconnect forcés
            if not db.is_closed():
                db.close()

            db.connect(reuse_if_open=True, timeout=timeout_seconds)
            logger.info("✅ Connexion DB établie")
            return True

        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                logger.warning(
                    f"⚠️ DB locked (tentative {attempt + 1}), retry dans {2**attempt}s..."
                )
                time.sleep(2**attempt)  # Exponential backoff
            else:
                logger.error(f"❌ DB error: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2**attempt)
        except Exception as e:
            logger.error(f"❌ Erreur connexion DB: {e}")
            if attempt < max_retries - 1:
                time.sleep(2**attempt)

    logger.error(f"❌ Connexion DB échouée après {max_retries} tentatives")
    return False


def connect_db():
    """
    Ouvre la connexion à la base de données si elle est fermée.
    Utilise maintenant connect_db_with_retry() pour robustesse.

    Returns:
        bool: True si la connexion est ouverte
    """
    return connect_db_with_retry()


def close_db():
    """
    Ferme proprement la connexion à la base de données.
    À appeler lors de l'arrêt de l'application.
    """
    try:
        if not db.is_closed():
            db.close()
            print("🔌 Connexion à la base de données fermée proprement")
    except Exception as e:
        print(f"⚠️  Erreur lors de la fermeture : {e}")


def check_connection():
    """
    Vérifie que la connexion à la base de données est active.

    Returns:
        bool: True si la connexion est active et fonctionnelle
    """
    try:
        db.execute_sql("SELECT 1;")
        return True
    except Exception as e:
        print(f"❌ La connexion DB est inactive : {e}")
        return False


# ============================================================================
# STATISTIQUES ET MONITORING
# ============================================================================


def check_wal_files():
    """
    Vérifie l'état des fichiers WAL et alerte si trop gros.

    Returns:
        dict: Info sur les fichiers WAL
    """
    try:
        db_path = Path(DB_CONFIG["name"])
        wal_file = db_path.with_suffix(".db-wal")
        shm_file = db_path.with_suffix(".db-shm")

        result: dict[str, Any] = {
            "wal_exists": wal_file.exists(),
            "wal_size_mb": 0.0,
            "shm_exists": shm_file.exists(),
            "shm_size_mb": 0.0,
            "warning": None,
        }

        if wal_file.exists():
            wal_size = wal_file.stat().st_size / (1024 * 1024)
            result["wal_size_mb"] = wal_size

            if wal_size > 100:  # 100 MB
                result["warning"] = (
                    f"⚠️ WAL file très gros: {wal_size:.1f}MB (ralentit DB)"
                )
                logger.warning(result["warning"])

        if shm_file.exists():
            shm_size = shm_file.stat().st_size / (1024 * 1024)
            result["shm_size_mb"] = shm_size

        return result

    except Exception as e:
        logger.error(f"❌ Erreur check WAL: {e}")
        return {"wal_exists": False, "shm_exists": False, "warning": str(e)}


def get_db_stats():
    """
    Récupère les statistiques de la base de données.

    Returns:
        dict: Dictionnaire avec les statistiques (nombre de tokens, métriques, signaux, taille)
    """
    try:
        from src.database.models import MacroMetric, SignalLog, SocialMetric, Token

        # Compter les enregistrements
        tokens_count = Token.select().count()
        metrics_count = SocialMetric.select().count()
        signals_count = SignalLog.select().count()
        macro_count = MacroMetric.select().count()

        # Taille du fichier DB
        db_path = Path(DB_CONFIG["name"])
        size_bytes = db_path.stat().st_size if db_path.exists() else 0
        size_mb = size_bytes / (1024 * 1024)

        return {
            "tokens": tokens_count,
            "metrics": metrics_count,
            "signals": signals_count,
            "macro": macro_count,
            "size_mb": size_mb,
            "size_bytes": size_bytes,
        }

    except Exception as e:
        print(f"⚠️  Erreur lors de la récupération des stats : {e}")
        return {
            "tokens": 0,
            "metrics": 0,
            "signals": 0,
            "macro": 0,
            "size_mb": 0,
            "size_bytes": 0,
        }


def optimize_db(max_retries: int = 3):
    """
    Optimise la base de données avec ANALYZE avec retry logic.
    Cette fonction est SAFE et ne bloque PAS les écritures.

    ANALYZE met à jour les statistiques du query planner pour améliorer
    les performances des requêtes sans bloquer la DB.

    ⚠️  Pour un nettoyage complet avec VACUUM, utilisez vacuum_db()
    UNIQUEMENT pendant les périodes de maintenance (hors heures de trading).

    Args:
        max_retries: Nombre max de tentatives si la DB est locked

    Returns:
        bool: True si réussi, False sinon
    """
    for attempt in range(max_retries):
        try:
            logger.info(
                f"🔧 Optimisation DB avec ANALYZE (tentative {attempt + 1}/{max_retries})"
            )

            # ANALYZE : Met à jour les statistiques du query planner
            # NE BLOQUE PAS les écritures, safe à tout moment
            db.execute_sql("ANALYZE;")
            logger.info("✅ ANALYZE terminé - Statistiques mises à jour")

            return True

        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                if attempt < max_retries - 1:
                    wait_time = 2**attempt
                    logger.warning(f"⚠️ DB locked, retry dans {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    logger.error(
                        f"❌ ANALYZE échoué après {max_retries} tentatives (DB locked)"
                    )
                    return False
            else:
                logger.error(f"❌ Erreur ANALYZE: {e}")
                return False
        except Exception as e:
            logger.error(f"❌ Erreur optimisation DB: {e}")
            return False

    return False


def vacuum_db(force=False):
    """
    Défragmente et compacte la base de données avec VACUUM.

    ⚠️  ATTENTION : VACUUM est une opération BLOQUANTE ⚠️

    - Bloque TOUTES les écritures pendant son exécution
    - Peut prendre plusieurs minutes sur une DB volumineuse (> 1 GB)
    - Nécessite de l'espace disque libre (taille de la DB × 2)

    À utiliser UNIQUEMENT :
    - En dehors des heures de trading actives
    - Pendant une fenêtre de maintenance planifiée
    - Sur une DB > 30 jours sans VACUUM

    Args:
        force (bool): Si False, demande confirmation. Si True, exécute directement.

    Returns:
        bool: True si réussi, False sinon
    """
    try:
        # Vérifier la taille de la DB avant
        db_path = Path(DB_CONFIG["name"])
        size_mb = db_path.stat().st_size / (1024 * 1024) if db_path.exists() else 0

        print("\n" + "=" * 60)
        print("⚠️  VACUUM - OPÉRATION BLOQUANTE ⚠️")
        print("=" * 60)
        print(f"Taille actuelle de la DB : {size_mb:.2f} MB")
        print("Durée estimée : Quelques secondes à plusieurs minutes")
        print("Pendant ce temps :")
        print("  ❌ Aucune écriture possible (scrapers bloqués)")
        print("  ❌ Les jobs du scheduler seront en attente")
        print("  ✅ Les lectures restent possibles")
        print("=" * 60)

        if not force:
            confirmation = input("\nÊtes-vous sûr de vouloir continuer ? (oui/non): ")
            if confirmation.lower() not in ["oui", "yes", "y"]:
                print("❌ VACUUM annulé")
                return False

        print("\n🔧 VACUUM en cours... (Ne pas interrompre)")

        # Force un checkpoint WAL avant le VACUUM
        enable_wal_checkpoint()

        # VACUUM : Défragmente et réduit la taille du fichier
        start_time = time.time()
        db.execute_sql("VACUUM;")
        duration = time.time() - start_time

        # Taille après VACUUM
        new_size_mb = db_path.stat().st_size / (1024 * 1024)
        saved_mb = size_mb - new_size_mb

        print(f"✅ VACUUM terminé en {duration:.1f}s")
        print(f"📊 Taille avant : {size_mb:.2f} MB")
        print(f"📊 Taille après : {new_size_mb:.2f} MB")
        print(
            f"💾 Espace récupéré : {saved_mb:.2f} MB ({saved_mb / size_mb * 100:.1f}%)"
        )

        return True

    except Exception as e:
        print(f"❌ Erreur lors du VACUUM : {e}")
        return False


def enable_wal_checkpoint():
    """
    Force un checkpoint WAL (Write-Ahead Log).
    Utile pour synchroniser le WAL avec la DB principale.
    """
    try:
        db.execute_sql("PRAGMA wal_checkpoint(TRUNCATE);")
        print("✅ WAL checkpoint effectué")
        return True
    except Exception as e:
        print(f"⚠️  Erreur checkpoint WAL : {e}")
        return False


# ============================================================================
# FONCTIONS DE BACKUP
# ============================================================================


def backup_database(backup_path=None):
    """
    Crée une sauvegarde de la base de données.

    Args:
        backup_path (str, optional): Chemin du fichier de backup.
                                     Si None, utilise un nom avec timestamp.

    Returns:
        str: Chemin du fichier de backup créé, ou None si échec
    """
    import shutil
    from datetime import datetime

    try:
        if backup_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = str(Path(DB_CONFIG["name"]).parent / f"backup_{timestamp}.db")

        # Force un checkpoint WAL avant le backup
        enable_wal_checkpoint()

        # Copie du fichier
        shutil.copy2(DB_CONFIG["name"], backup_path)

        backup_size = Path(backup_path).stat().st_size / (1024 * 1024)
        print(f"✅ Backup créé : {backup_path} ({backup_size:.2f} MB)")

        return backup_path

    except Exception as e:
        print(f"❌ Erreur lors du backup : {e}")
        return None


# ============================================================================
# UTILITAIRES DE DEBUGGING
# ============================================================================


def print_db_schema():
    """
    Affiche le schéma complet de la base de données.
    Utile pour le debugging.
    """
    try:
        print("\n" + "=" * 60)
        print("SCHÉMA DE LA BASE DE DONNÉES")
        print("=" * 60)

        cursor = db.execute_sql("SELECT sql FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()

        for table_sql in tables:
            print(f"\n{table_sql[0]}\n")

        print("=" * 60 + "\n")

    except Exception as e:
        print(f"❌ Erreur lors de l'affichage du schéma : {e}")


def test_write_performance(n_records=1000):
    """
    Test de performance d'écriture dans la DB.

    Args:
        n_records (int): Nombre d'enregistrements de test à créer
    """
    import time

    from src.database.models import Token

    try:
        print(f"\n🧪 Test de performance d'écriture ({n_records} records)...")

        start_time = time.time()

        with db.atomic():
            for i in range(n_records):
                Token.create(
                    symbol=f"TEST{i}",
                    contract=f"0xTEST{i}",
                    cashtag=f"$TEST{i}",
                    status="TESTING",
                )

        duration = time.time() - start_time
        records_per_sec = n_records / duration

        print(f"✅ {n_records} records insérés en {duration:.2f}s")
        print(f"📊 Vitesse : {records_per_sec:.0f} records/sec")

        # Nettoyage
        Token.delete().where(Token.status == "TESTING").execute()
        print("🧹 Records de test supprimés")

    except Exception as e:
        print(f"❌ Erreur durant le test : {e}")


# ============================================================================
# TEST DE CONNEXION
# ============================================================================

if __name__ == "__main__":
    """
    Test des fonctions de connexion.
    Lance ce fichier directement pour vérifier la configuration.
    """
    print("🔧 Test du module db_connect...\n")

    # Test d'initialisation
    if initialize_db():
        print("\n✅ Initialisation réussie")

        # Test de connexion
        if check_connection():
            print("✅ Connexion active")

        # Afficher les stats
        print("\n" + "=" * 60)
        stats = get_db_stats()
        print("📊 Statistiques de la base :")
        print(f"   Tokens     : {stats['tokens']}")
        print(f"   Métriques  : {stats['metrics']}")
        print(f"   Signaux    : {stats['signals']}")
        print(f"   Macro      : {stats['macro']}")
        print(f"   Taille     : {stats['size_mb']:.2f} MB")
        print("=" * 60)

        # Test de backup
        print("\n🔄 Test de backup...")
        backup_file = backup_database()
        if backup_file:
            # Supprimer le backup de test
            Path(backup_file).unlink()
            print("🧹 Backup de test supprimé")

        # Fermeture propre
        close_db()

    else:
        print("\n❌ Échec de l'initialisation")
