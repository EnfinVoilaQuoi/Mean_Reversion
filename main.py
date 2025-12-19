"""
Main.py - Orchestrateur Synchronisé (5m/15m) + Bot Discord
"""

import os
import sys

# Fix UTF-8 encoding sur Windows
if os.name == "nt":  # Windows
    import codecs

    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, errors="replace")
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, errors="replace")

import logging
import threading
import time

# Scheduler
from apscheduler.schedulers.background import BackgroundScheduler

# Analyse
from src.analysis.zscore_calc import compute_social_zscore, get_fear_greed_index

# Config (Toutes les constantes centralisées)
from src.config import (
    ANALYSIS_CONFIG,
    DISCORD_CONFIG,
    MODE_INTERVALS,
    VERIFICATION_CONFIG,
)

# Dashboard
from src.dashboard.dash_app import (  # NOUVEAU: Injection des fonctions
    run_dashboard,
    set_main_functions,
)

# Database
from src.database import Token, initialize_db

# Discord
from src.discord.discord_bot import bot, run_discord_bot, set_scheduler

# Configuration du logging centralisée
from src.logging_config import setup_logging
from src.scrapers.dexscreener_worker import (
    resolve_incomplete_tokens,  # NOUVEAU: Résolution tokens incomplets
)
from src.scrapers.macro_worker import macro_monitoring_job
from src.scrapers.price_worker import (
    check_and_ensure_price_coverage,
    get_current_metrics,
    store_price_snapshot,
)
from src.scrapers.sentiment_worker import analyze_high_impact_tweets
from src.scrapers.token_screener import (  # NOUVEAU: Module de screening
    fetch_top_x_and_screen,
    request_shutdown,
)
from src.scrapers.twitter_worker import update_token_social_data
from src.signals.signal_manager import set_discord_bot

# Initialiser le logging avec la configuration personnalisée (moins verbeuse pour httpx/twscrape)
setup_logging()
logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

# ============================================================================
# JOB DE MONITORING SYNCHRONISÉ
# Responsabilité: Orchestre les workers selon la fréquence configurée
# ============================================================================


def monitoring_job_synchronized(token_id: int):
    """
    Orchestrateur principal - Exécuté selon la fréquence du mode (5m/15m/60m).

    Responsabilités strictes:
    1. Lit la fréquence depuis MODE_INTERVALS
    2. Appelle les workers avec les paramètres appropriés
    3. Coordonne le calcul du Z-Score avec les données collectées

    Les workers NE GÈRENT PAS le timing, ils fournissent simplement les données demandées.
    """
    try:
        # Recharger le token frais
        token = Token.get_by_id(token_id)
        current_mode = token.status.upper()  # REGULAR, AGGRESSIVE ou CROISIERE

        # Récupérer la fenêtre temporelle depuis la configuration
        window_minutes = MODE_INTERVALS.get(current_mode, 15)

        # Mapper le mode Discord vers le mode worker
        mode_map = {
            "AGGRESSIVE": "aggressive",
            "REGULAR": "regular",
            "CROISIERE": "croisiere",
        }
        worker_mode = mode_map.get(current_mode, "regular")

        print(
            f"\n⏰ [JOB {current_mode}] {token.cashtag} : Sync Start (fenêtre: {window_minutes}min)..."
        )

        # ========================================================================
        # ÉTAPE 1: SOCIAL (Twitter) - Récupération des tweets récents
        # ========================================================================
        # Le worker scrape les tweets de la fenêtre spécifiée
        # Exemple: mode REGULAR → scrape les 15 dernières minutes
        update_token_social_data(
            token,
            mode=worker_mode,
            lookback_minutes=window_minutes,  # ✨ Passage explicite de la fenêtre
        )

        # ========================================================================
        # ÉTAPE 1.5: SENTIMENT - Analyse CryptoBERT des tweets à fort impact
        # ========================================================================
        logger.info("   🧠 Analyse de sentiment des tweets à fort impact...")
        analyze_high_impact_tweets(token)

        # ========================================================================
        # ÉTAPE 2: FINANCE (Prix, Volume) - Données actuelles
        # ========================================================================
        # Récupération des métriques financières en temps réel
        fin_data = get_current_metrics(
            token.primary_chain_id, token.primary_pair_address
        )
        if not fin_data:
            logger.error(f"   ❌ Prix indisponible pour {token.cashtag}. Skip.")
            return

        # ========================================================================
        # ÉTAPE 3: STOCKAGE OHLC - Construction progressive de l'historique
        # ========================================================================
        # Détermine la résolution selon le mode (cohérent avec la fréquence)
        resolution_map = {"AGGRESSIVE": "5m", "REGULAR": "15m", "CROISIERE": "1h"}
        resolution = resolution_map.get(current_mode, "15m")

        # Stocke le snapshot de prix pour construire les candles OHLC
        store_price_snapshot(
            token=token,
            price_usd=fin_data["price_usd"],
            volume=fin_data["volume_h1"],
            resolution=resolution,
        )

        # Vérifie la couverture des données de prix (fenêtre fixe de 7 jours)
        zscore_window_days = ANALYSIS_CONFIG["ZSCORE_WINDOW_DAYS"]
        price_coverage = check_and_ensure_price_coverage(
            token, days_back=zscore_window_days, resolution=resolution
        )
        if not price_coverage["is_sufficient"]:
            logger.info(
                f"   📊 Couverture prix: {price_coverage['candles_count']}/{price_coverage['expected_candles']} "
                f"({price_coverage['coverage_ratio'] * 100:.1f}%) - Construction en cours..."
            )

        # ========================================================================
        # ÉTAPE 4: MACRO (Fear & Greed Index)
        # ========================================================================
        fgi = get_fear_greed_index()

        # ========================================================================
        # ÉTAPE 5: CALCUL Z-SCORES - Analyse Mean Reversion
        # ========================================================================
        # Le calcul des Z-Scores utilise:
        # - window_minutes: Pour agréger les tweets de la fenêtre actuelle
        # - ZSCORE_WINDOW_DAYS: Pour calculer la baseline (μ, σ) sur 7 jours (fixe, dans config)
        # Retourne un Dict avec toutes les métriques (3 Z-Scores + Divergence + métriques)
        metrics = compute_social_zscore(
            token=token,
            price=fin_data["price_usd"],
            trading_volume_h1=fin_data["volume_h1"],
            trading_volume_h24=fin_data[
                "volume_h24"
            ],  # ✨ Utilisé pour normalisation sociale
            liquidity_usd=fin_data["liquidity_usd"],
            fgi_correction=fgi,
            window_minutes=window_minutes,  # Fenêtre d'agrégation (5/15/60 min selon mode)
        )

        if metrics is None:
            logger.error(f"   ❌ Erreur calcul Z-Scores pour {token.cashtag}")
            return

        print(f"   ✅ Fin Sync : {token.cashtag} | Prix ${fin_data['price_usd']:.6f}")

        # ========================================================================
        # ÉTAPE 6: DÉTECTION DE SIGNAUX - Analyse par signal_manager
        # ========================================================================
        # La logique de décision des signaux est dans signal_manager.py
        from src.signals.signal_manager import check_for_signal

        try:
            check_for_signal(token, metrics)
        except Exception as e:
            logger.error(f"   ❌ Erreur détection de signaux pour {token.cashtag}: {e}")

    except Exception as e:
        logger.error(f"❌ Erreur Job {token_id}: {e}")


# ============================================================================
# UTILS / AJOUT ET MISE À JOUR DU MONITORING
# ============================================================================


def add_token_to_monitoring(token: Token, mode: str = "regular"):
    """
    Ajoute ou met à jour un token dans le scheduler avec la fréquence spécifiée.
    """
    mode = mode.upper()
    job_id = f"monitor_reg_{token.id}"
    interval_minutes = MODE_INTERVALS.get(mode, 15)  # 15 min par défaut

    # 1. Suppression si le job existe déjà
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
        logger.info(f"🧹 Job existant {job_id} supprimé.")

    # Si le mode est 'PENDING' (backfill en cours), on n'ajoute pas de job récurrent
    if mode == "PENDING":
        logger.info(
            f"⏳ {token.cashtag} est en mode PENDING. Aucun job récurrent ajouté."
        )
    else:
        # 2. Ajout du nouveau job
        scheduler.add_job(
            func=monitoring_job_synchronized,  # La fonction qui exécute scraping + calcul Z-Score
            trigger="interval",
            minutes=interval_minutes,
            id=job_id,
            args=[token.id],
            name=f"Monitoring {token.cashtag} ({mode})",
            max_instances=1,  # S'assurer qu'un seul job par token s'exécute à la fois
            replace_existing=True,
        )
        logger.info(
            f"✅ Job {job_id} créé/mis à jour. Fréquence : {interval_minutes} minutes ({mode})."
        )

    # 3. Mise à jour du statut dans la base de données
    token.status = mode
    token.save()


# ============================================================================
# FONCTION EXPORTÉE (update_monitoring_job)
# ============================================================================


def update_monitoring_job(token: Token, mode: str) -> bool:
    """
    Fonction principale appelée par Discord Bot et Dash pour changer la fréquence.

    Si le token passe de LISTED/INCOMPLETE vers un mode actif (REGULAR/AGGRESSIVE/CROISIERE),
    lance d'abord un backfill immédiat avant de planifier le job récurrent.
    """
    mode = mode.upper()

    if mode not in MODE_INTERVALS:
        logger.warning(f"⚠️ Mode de monitoring inconnu: {mode}")
        return False

    try:
        # Vérifier si le token nécessite un backfill initial
        old_status = token.status
        needs_backfill = old_status in ["LISTED", "INCOMPLETE"] and mode in [
            "REGULAR",
            "AGGRESSIVE",
            "CROISIERE",
        ]

        if needs_backfill:
            logger.info(
                f"🚀 {token.cashtag} passe de {old_status} à {mode} → Lancement du backfill immédiat"
            )

            # Lancer le backfill avec les jobs asynchrones, en spécifiant le mode cible
            success = add_token_and_start_backfill(token, target_mode=mode)

            if not success:
                logger.error(f"❌ Échec du backfill pour {token.cashtag}")
                return False

            logger.info(
                f"✅ Backfill planifié pour {token.cashtag}. Le mode {mode} sera activé après complétion."
            )
            return True

        # Pas besoin de backfill : juste mettre à jour le job
        add_token_to_monitoring(token, mode)
        logger.info(f"🔄 Changement de mode réussi pour {token.cashtag} vers {mode}.")
        return True

    except Exception as e:
        logger.error(
            f"❌ Erreur critique lors de la mise à jour du mode pour {token.cashtag}: {e}",
            exc_info=True,
        )
        return False


# ============================================================================
# AJOUT D'UN TOKEN AVEC BACKFILL ASYNCHRONE
# ============================================================================


def add_token_and_start_backfill(token: Token, target_mode: str = "REGULAR") -> bool:
    """
    Ajoute un token et planifie ses backfills (Twitter + Prix) de manière asynchrone via APScheduler.

    Le token est mis en statut PENDING pendant le backfill.
    Une fois les backfills terminés avec succès, le token passe automatiquement dans le mode demandé.

    Stratégie:
    - Backfill Twitter (7j) : Démarre après 5 secondes
    - Backfill Prix (7j) : Démarre après 30 secondes (décalage pour respecter rate limits)

    Args:
        token: Token à ajouter (doit déjà exister en DB)
        target_mode: Mode cible après backfill (REGULAR par défaut, AGGRESSIVE, CROISIERE)

    Returns:
        bool: True si les jobs de backfill ont été planifiés avec succès
    """
    try:
        # 1. Mettre le token en statut PENDING
        token.status = "PENDING"
        token.save()
        logger.info(f"📋 Token {token.cashtag} mis en statut PENDING")

        # 2. Créer le callback qui sera appelé après le backfill Twitter
        def on_twitter_backfill_complete(completed_token: Token):
            """Callback appelé quand le backfill Twitter réussit"""
            logger.info(
                f"🎯 Backfill Twitter terminé pour {completed_token.cashtag}, passage en mode {target_mode}"
            )
            update_monitoring_job(completed_token, target_mode)

        # 3. Planifier le job de backfill TWITTER (démarrage dans 5 secondes)
        from datetime import datetime, timedelta

        from src.scrapers.price_worker import start_price_backfill_job
        from src.scrapers.twitter_worker import start_backfill_job

        job_id_twitter = f"backfill_twitter_{token.id}"

        scheduler.add_job(
            func=start_backfill_job,
            trigger="date",  # Déclenchement unique
            run_date=datetime.now() + timedelta(seconds=5),
            id=job_id_twitter,
            args=[token, on_twitter_backfill_complete],  # Passer le callback
            name=f"Backfill Twitter {token.cashtag} (7j)",
            max_instances=1,
            replace_existing=True,
        )

        logger.info(f"✅ Job backfill Twitter planifié (ID: {job_id_twitter})")
        logger.info("   ⏰ Démarrage dans 5 secondes...")

        # 4. Planifier le job de backfill PRIX (démarrage dans 30 secondes - décalage pour rate limits)
        job_id_price = f"backfill_price_{token.id}"

        scheduler.add_job(
            func=start_price_backfill_job,
            trigger="date",
            run_date=datetime.now() + timedelta(seconds=30),
            id=job_id_price,
            args=[token, 7, "15m"],  # token, days_back, resolution
            name=f"Backfill Prix {token.cashtag} (7j)",
            max_instances=1,
            replace_existing=True,
        )

        logger.info(f"✅ Job backfill Prix planifié (ID: {job_id_price})")
        logger.info("   ⏰ Démarrage dans 30 secondes...")
        logger.info(
            "📊 Les deux backfills s'exécuteront en parallèle avec un décalage de 25s"
        )

        return True

    except Exception as e:
        logger.error(
            f"❌ Erreur lors de la planification des backfills pour {token.cashtag}: {e}",
            exc_info=True,
        )
        return False


# ============================================================================
# JOB DE MAINTENANCE PIPELINE
# ============================================================================


def pipeline_maintenance_job():
    """
    Job de maintenance périodique du pipeline de métriques (toutes les 6h).

    Responsabilités:
    - Détecte les gaps dans social_metrics, price_metrics, signal_metrics
    - Comble automatiquement les gaps détectés
    - Backfill macro_metrics si nécessaire (FGI)
    - Assure la cohérence du pipeline pour tous les tokens actifs

    Exécution: Toutes les 6 heures (00h00, 06h00, 12h00, 18h00)
    """
    logger.info("🔧 === Pipeline Maintenance Job Démarrage ===")

    try:
        from src.analysis.metric_aggregator import MetricAggregator

        # Initialiser l'aggregator
        aggregator = MetricAggregator()

        # Orchestrer tous les tokens actifs (mode incremental)
        result = aggregator.orchestrate_all_tokens(mode="incremental")

        # Log résultats
        logger.info(
            f"✅ Pipeline Maintenance Terminée:\n"
            f"   Total tokens: {result['total_tokens']}\n"
            f"   Succès: {result['success_count']}\n"
            f"   Partiels: {result['partial_count']}\n"
            f"   Échecs: {result['failed_count']}"
        )

        # Log tokens échoués si présents
        if result["failed_count"] > 0:
            failed_tokens = [
                token
                for token, res in result["results"].items()
                if res.get("status") == "FAILED"
            ]
            logger.warning(f"⚠️  Tokens échoués: {', '.join(failed_tokens)}")

    except Exception as e:
        logger.error(f"❌ Erreur Pipeline Maintenance Job: {e}", exc_info=True)

    logger.info("🔧 === Pipeline Maintenance Job Terminé ===")


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("🚀 Crypto Social Mean Reversion Bot - Démarrage")
    print("=" * 60)

    # 1. Initialisation de la DB
    print("\n📂 Initialisation de la base de données...")
    initialize_db()

    # 2. Démarrage du Scheduler
    print("\n⏰ Démarrage du Scheduler APScheduler...")

    # Job Macro (1h) - Surveillance des métriques macro (FGI, Volume Global)
    scheduler.add_job(
        macro_monitoring_job,
        "interval",
        hours=1,
        id="macro_monitoring",
        name="Macro Monitoring (FGI + Volume Global)",
    )

    # Job Screening Quotidien (02h00) - Mise à jour du Top X et classement
    scheduler.add_job(
        fetch_top_x_and_screen,
        "cron",
        hour=2,  # Exécution à 2h00 du matin
        id="token_screening_daily",
        name="Screening Quotidien Top 1000",
        replace_existing=True,
    )
    print("   📊 Job de screening quotidien configuré (02h00)")

    # Job Résolution Tokens INCOMPLETE (02h10) - 10 min après le screening
    scheduler.add_job(
        resolve_incomplete_tokens,
        "cron",
        hour=2,
        minute=10,  # Exécution à 2h10 du matin (10 min après screening)
        id="resolve_incomplete_tokens",
        name="Résolution Tokens INCOMPLETE",
        replace_existing=True,
    )
    print("   🔍 Job de résolution tokens INCOMPLETE configuré (02h10)")

    # Job de vérification Twitter (02h30) - 20 min après résolution
    from src.scrapers.twitter_verification_worker import daily_verification_check

    scheduler.add_job(
        daily_verification_check,
        "cron",
        hour=VERIFICATION_CONFIG["CHECK_TIME_HOUR"],
        minute=VERIFICATION_CONFIG["CHECK_TIME_MINUTE"],
        id="twitter_verification_check",
        name="Vérification Quotidienne Twitter",
        replace_existing=True,
    )
    print("   ✅ Job de vérification Twitter configuré (02h30)")

    # Job Pipeline Maintenance (6h) - Maintenance du pipeline de métriques
    scheduler.add_job(
        pipeline_maintenance_job,
        "interval",
        hours=6,
        id="pipeline_maintenance",
        name="Pipeline Maintenance (6h)",
        replace_existing=True,
    )
    print("   🔧 Job de maintenance pipeline configuré (toutes les 6h)")

    scheduler.start()

    # 3. Démarrage du Bot Discord (si activé)
    if DISCORD_CONFIG["ENABLED"] and DISCORD_CONFIG["BOT_TOKEN"]:
        print("\n💬 Démarrage du Bot Discord...")

        # Injecter le scheduler et les fonctions dans le bot Discord
        set_scheduler(scheduler, update_monitoring_job, add_token_and_start_backfill)

        # Démarrer le bot Discord dans un thread séparé (car bot.run() est bloquant)
        discord_thread = threading.Thread(
            target=run_discord_bot,
            args=(DISCORD_CONFIG["BOT_TOKEN"],),
            daemon=True,
            name="DiscordBotThread",
        )
        discord_thread.start()

        # Attendre que le bot soit prêt pour injecter la référence dans signal_manager
        # (On attend 5 secondes pour que bot.on_ready() se déclenche)
        print("   Attente de connexion du bot...")
        time.sleep(5)

        # Injecter la référence du bot dans signal_manager pour les alertes
        if DISCORD_CONFIG["ALERT_CHANNEL_ID"]:
            set_discord_bot(bot, DISCORD_CONFIG["ALERT_CHANNEL_ID"])
            print(
                f"   ✅ Bot Discord connecté | Canal alertes: {DISCORD_CONFIG['ALERT_CHANNEL_ID']}"
            )
        else:
            logger.warning("⚠️ DISCORD_ALERT_CHANNEL_ID non configuré dans .env")

    else:
        print("\n⏸️ Bot Discord désactivé (DISCORD_ENABLED=False ou BOT_TOKEN manquant)")

    # 4. Démarrage du Dashboard Dash (toujours activé)
    print("\n📊 Démarrage du Dashboard Web...")

    # Injecter les fonctions dans le dashboard pour le contrôle
    set_main_functions(update_monitoring_job)

    dash_thread = threading.Thread(
        target=run_dashboard,
        kwargs={"host": "0.0.0.0", "port": 8050, "debug": False},
        daemon=True,
        name="DashboardThread",
    )
    dash_thread.start()
    print("   ✅ Dashboard accessible sur http://localhost:8050")
    print("   💡 Accès réseau : http://<votre_ip>:8050")

    # 5. Reload des tokens existants
    print("\n🔄 Chargement des tokens existants...")
    existing_tokens = Token.select().where(
        Token.status.in_(["REGULAR", "AGGRESSIVE", "CROISIERE"])
    )
    if existing_tokens.count() > 0:
        for t in existing_tokens:
            update_monitoring_job(t, t.status)
        print(f"   ✅ {existing_tokens.count()} token(s) chargé(s)")
    else:
        print("   ℹ️ Aucun token en surveillance")

    # 6. Boucle principale (keep-alive)
    print("\n✅ Système opérationnel !")
    print("=" * 60)
    print("📋 ACCÈS AU SYSTÈME :")
    print("   🌐 Dashboard Web : http://localhost:8050")
    print("   💬 Bot Discord   : Utilisez !add_token pour ajouter des tokens")
    print("   🛑 Arrêt         : Ctrl+C")
    print("=" * 60 + "\n")

    while True:
        try:
            time.sleep(1)
            # Possibilité d'ajouter une interface CLI ici si besoin
        except KeyboardInterrupt:
            print("\n\n🛑 Arrêt du système...")

            # Demander l'arrêt gracieux du screening en cours (si actif)
            request_shutdown()

            # Attendre 2 secondes pour laisser le screening se terminer proprement
            print("   ⏳ Arrêt gracieux en cours...")
            time.sleep(2)

            scheduler.shutdown()
            print("   ✅ Scheduler arrêté")
            print("   ✅ Bot Discord s'arrêtera automatiquement (daemon thread)")
            print("\n👋 Au revoir !")
            break
