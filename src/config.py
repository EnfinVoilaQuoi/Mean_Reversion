import os
from pathlib import Path

from dotenv import load_dotenv

# Import TypedDict definitions for type safety
from .config_types import (
    AnalysisConfig,
    DBConfig,
    ExternalAPIs,
    ModeIntervals,
    PlaywrightNitterConfig,
    SystemConfig,
    TelegramConfig,
    TwitterConfig,
    TwitterioConfig,
    TwitterScrapersConfig,
    TwscrapeConfig,
)

# Charge les variables d'environnement (API Keys) depuis le fichier .env
load_dotenv()

# --- 1. CHEMINS ET SYSTÈME ---
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"

# Crée les dossiers s'ils n'existent pas
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# --- 2. BASE DE DONNÉES (SQLite Optimisée) ---
DB_CONFIG: DBConfig = {
    "name": str(DATA_DIR / "social_data.db"),
    "pragmas": {
        "journal_mode": "wal",  # Write-Ahead Logging pour la concurrence (Crucial)
        "cache_size": -1024 * 64,  # 64MB de cache en RAM
        "foreign_keys": 1,
        "synchronous": 1,  # Normal (compromis sécurité/vitesse)
    },
}

# --- 3. PARAMÈTRES TWITTER / NITTER ---
TWITTER_CONFIG: TwitterConfig = {
    # Instance Nitter : Liste de secours si la principale saute
    # Astuce: Utilise une rotation ou teste-les au démarrage
    "INSTANCES": [
        "https://nitter.tiekoetter.com",
        "https://nitter.net",
        "https://xcancel.com",
        "https://nitter.privacyredirect.com",
        "https://nitter.catsarch.com",
        "https://nitter.poast.org",
        "https://nitter.privacydev.net",
        "https://nitter.cz",
    ],
    "SCRAPE_DELAY": 5.0,  # Secondes entre chaque requête (évite le ban IP)
    "BACKFILL_DAYS": 7,  # Historique à récupérer au pré-signal
    "BACKFILL_MAX_TWEETS": 1000,  # Sécurité pour ne pas boucler à l'infini
    # Pondération de l'Engagement (Score d'Impact)
    # Formule : Log10(Vues + (Likes * 5) + (RT * 20))
    "WEIGHTS": {
        "VIEW": 1.0,  # Base
        "LIKE": 5.0,  # Vaut ~5 vues passives
        "RETWEET": 20.0,  # Vaut ~20 vues (viralité)
        "QUOTE": 15.0,
        "REPLY": 2.0,
    },
    # Fallback: Si Nitter ne donne pas les vues, combien vaut 1 like en "vues estimées" ?
    "ESTIMATED_VIEWS_PER_LIKE": 100,
}

# --- 3b. PARAMÈTRES TWITTERIO (TwitterAPI.io) ---
TWITTERIO_CONFIG: TwitterioConfig = {
    "ENABLED": True,
    "MAX_TWEETS_PER_SEARCH": 1000,
    "MAX_PAGES": 50,
    "DELAY_BETWEEN_PAGES": 1.0,
    "FALLBACK_THRESHOLD": 0.5,  # Tweets par heure minimum pour déclencher fallback (0.5 = 12 en 24h)
    "DEFAULT_QUERY_TYPE": "Auto",  # "Auto", "Both", "Latest", "Top", "Media"
}

# --- 4. PARAMÈTRES TELEGRAM ---
TELEGRAM_CONFIG: TelegramConfig = {
    "API_ID": os.getenv("TELEGRAM_API_ID"),
    "API_HASH": os.getenv("TELEGRAM_API_HASH"),
    "SESSION_NAME": "crypto_bot_session",
    # Mots-clés à exclure pour éviter le bruit (Airdrops, Spam)
    "BLACKLIST_KEYWORDS": ["airdrop", "giveaway", "whitelist", "presale", "promoted"],
    # Channels génériques à écouter en "aspirateur" (Exemple)
    "MONITOR_CHANNELS": [
        "NewPairs_Eth",  # Exemple
        "Tokens_Fund",
        # Ajoute ici tes sources Alpha
    ],
}

# --- 5. CONFIGURATION DES SCRAPERS TWITTER (SYSTÈME DE PRIORITÉ) ---
# Le système essaie les scrapers dans l'ordre de priorité (1 = premier, 2 = second, etc.)
# En cas d'échec, il passe automatiquement au suivant

TWITTER_SCRAPERS_CONFIG: TwitterScrapersConfig = {
    # Ordre de priorité global (modifiable selon tes besoins)
    # 1 = essayé en premier, 2 = backup
    # twscrape: DÉSACTIVÉ pour recherches historiques (inutile), utilisé UNIQUEMENT pour 24h
    "PRIORITY_ORDER": {
        "twitterio": 1,  # TwitterAPI.io (par défaut)
        "playwright": 2,  # Nitter/Playwright (backup)
        # twscrape: UNIQUEMENT pour fetch_latest_updates (24h) via mode=twscrape forcé
    }
}

# --- 5.1 NITTER + PLAYWRIGHT (Priorité 1 par défaut) ---
PLAYWRIGHT_NITTER_CONFIG: PlaywrightNitterConfig = {
    # Activation du scraping Playwright (plus fiable que requests)
    "ENABLED": True,  # Mettre à False pour utiliser requests simple
    # Mode navigateur
    "HEADLESS": False,  # True = sans interface, False = avec navigateur visible (debug)
    # Timeouts
    "TIMEOUT": 30000,  # Timeout général en ms (30 secondes)
    "NAVIGATION_TIMEOUT": 45000,  # Timeout pour la navigation (45 secondes)
    "WAIT_AFTER_LOAD": 2000,  # Attente après chargement de page (ms)
    # User Agent (pour éviter la détection)
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Viewport
    # RÉDUIT pour que le bouton "Load more" soit visible (problème responsive design)
    "VIEWPORT": {"width": 1280, "height": 720},
    # Sélecteurs CSS pour Nitter
    "SELECTORS": {
        "TIMELINE_ITEM": "div.timeline-item",
        "TWEET_LINK": "a.tweet-link",
        "USERNAME": "a.username",
        "TWEET_DATE": "span.tweet-date a",
        "TWEET_CONTENT": "div.tweet-content",
        "TWEET_STATS": "div.tweet-stats",
        "STAT_ITEM": "span.tweet-stat",
        "ICON_HEART": "span.icon-heart",
        "ICON_RETWEET": "span.icon-retweet",
        "ICON_COMMENT": "span.icon-comment",
        "ICON_VIEWS": "span.icon-views",
        # Profil
        "PROFILE_STATS": "ul.profile-statlist",
        "PROFILE_STAT_ITEM": "li.profile-stat",
        "PROFILE_STAT_HEADER": "span.profile-stat-header",
        "PROFILE_STAT_NUM": "span.profile-stat-num",
        "PROFILE_FULLNAME": "a.profile-card-fullname",
    },
    # Scroll settings pour charger plus de tweets
    "SCROLL_ENABLED": True,
    "SCROLL_PAUSE_MS": 1500,  # Pause entre scrolls
    "MAX_SCROLLS": 5,  # Nombre max de scrolls pour charger plus de tweets
    # Validation manuelle de "No more items"
    # Utile quand Nitter est saturé et affiche des faux "No more items"
    # À désactiver une fois que vous aurez votre propre instance Nitter
    "MANUAL_VALIDATION_NO_MORE_ITEMS": True,  # True = pause pour validation manuelle
    "MAX_RETRIES": 3,
    "SMALL_GAP_THRESHOLD_HOURS": 6,
}

# --- 5.2 TWSCRAPE (Priorité 2 par défaut) ---
TWSCRAPE_CONFIG: TwscrapeConfig = {
    "ENABLED": False,  # Désactivé par défaut - Activer si tu as configuré des comptes Twitter
    "MAX_TWEETS_PER_SEARCH": 2000,  # Limite de tweets par recherche
    "DB_PATH": None,  # None = utilise ~/.twscrape/accounts.db (défaut)
}

# --- 6. FRÉQUENCES DU SCHEDULER (MODE_INTERVALS) ---
# Constantes utilisées par main.py pour définir l'intervalle des jobs APScheduler
MODE_INTERVALS: ModeIntervals = {
    "REGULAR": 15,  # 15 minutes - Mode par défaut (Nitter)
    "CROISIERE": 60,  # 60 minutes - Mode économique (faible activité)
    "PENDING": 99999,  # Pause - Token inactif
}

# --- 7. ANALYSE & Z-SCORE (Le Cerveau) ---
ANALYSIS_CONFIG: AnalysisConfig = {
    # Fenêtres temporelles pour le calcul du Z-Score
    "ZSCORE_WINDOW_DAYS": 7,  # Fenêtre des 7 jours pour la moyenne/std de référence
    "LOOKBACK_AGGREGATION_MINUTES": 15,  # Fenêtre pour agréger les tweets avant calcul Z-Score
    # Fréquences de mise à jour (DEPRECATED - Utiliser MODE_INTERVALS à la place)
    # Conservé pour compatibilité avec ancien code
    "INTERVALS": {
        "ACTIVE_MONITORING": 5,  # Mode 3 Agressif avec X Pro (Kill Zone)
        "ACTIVE_MONITORING_FALLBACK": 10,  # Mode 3 Agressif avec Nitter (fallback si X Pro désactivé/échoue)
        "PASSIVE_MONITORING": 15,  # Mode 2 Régulier (MàJ régulière via Nitter)
    },
    # Paramètres du Z-Score Glissant
    "WINDOW_HOURS": 24,  # On compare l'heure actuelle à la moyenne des 24h glissantes
    # OU tu peux mettre 168 (7 jours) pour une tendance plus longue
    "SMOOTHING_FACTOR": 1.0,  # Constante Epsilon pour éviter division par zéro
    # Bruit artificiel minimal pour éviter les valeurs exactement nulles
    # Ajouté au social_volume et social_density pour améliorer la robustesse statistique
    "SOCIAL_VOLUME_EPSILON": 0.1,  # Epsilon pour social_volume (impact minimal)
    "SOCIAL_DENSITY_EPSILON": 0.01,  # Epsilon pour social_density (impact minimal)
    "EPSILON_NOISE_RANGE": 0.1,  # Variation aléatoire (±10% de epsilon)
    # TRUE = Compare 15h aujourd'hui avec 15h les jours d'avant (Recommandé)
    # FALSE = Compare 15h aujourd'hui avec la moyenne globale des dernières 24h
    "USE_SEASONALITY": True,
    # Combien de jours en arrière regarder pour construire la moyenne saisonnière ?
    "SEASONAL_LOOKBACK_DAYS": 7,
    # Tolérance (fenêtre d'heure) : Si on regarde 15h, on prend aussi 14h et 16h
    # pour avoir plus de données (lisser) ?
    # 0 = Heure stricte, 1 = +/- 1 heure
    "SEASONAL_WINDOW_HOUR_PAD": 1,
    # Ajustement de seuil par le sentiment
    "MACRO_ADJUSTMENT": {
        # Z-Score minimum requis si FGI > 75
        "BULL_Z_THRESHOLD": 3.5,
        # Z-Score minimum requis si FGI < 30
        "BEAR_Z_THRESHOLD": 2.0,
        # Z-Score si FGI est neutre
        "NEUTRAL_Z_THRESHOLD": 2.5,
    },
    # Facteur de pondération pour le Ratio de Volume Social
    "GLOBAL_VOLUME_WEIGHT": 0.05,  # Poids du volume global dans le score final
    # Seuils de déclenchement (Triggers)
    "THRESHOLDS": {
        "Z_HIGH": 2.5,  # Hype anormale (Potentiel Short si divergence)
        "Z_EXTREME": 4.0,  # Panique ou Euphorie totale
        "Z_LOW": -1.5,  # Désintérêt total
        # Seuils de Divergence (Social vs Prix)
        "DIVERGENCE_HIGH": 2.5,  # Divergence positive (Hype >> Prix) = Fake Pump potentiel
        "DIVERGENCE_LOW": -2.0,  # Divergence négative (Prix >> Hype) = Croissance organique
        # Filtre "Ratio de Légitimité"
        # Si (Mentions / Followers Officiels) > 50 -> Suspicion de Bots
        "MAX_HYPE_TO_FOLLOWER_RATIO": 50.0,
    },
}

# --- 7. ANALYSE DE SENTIMENT (CryptoBERT) ---
# Pondération linéaire du volume social basée sur le score de confiance

# Coefficient d'agressivité pour la pondération linéaire
# Formule: W = 1.0 + (S × k)
# Où S = sentiment_score converti en [-1.0, +1.0]
#     k = SENTIMENT_COEFFICIENT
#
# Exemples avec k = 0.3:
#   - Très Bullish (S = +1.0) → W = 1.0 + (1.0 × 0.3) = 1.3 (amplification +30%)
#   - Neutre (S = 0.0)        → W = 1.0 + (0.0 × 0.3) = 1.0 (aucun changement)
#   - Très Bearish (S = -1.0) → W = 1.0 + (-1.0 × 0.3) = 0.7 (atténuation -30%)
SENTIMENT_COEFFICIENT: float = 0.3  # Coefficient d'agressivité (0.0 à 0.5 recommandé)

# Limites de sécurité pour le poids final
SENTIMENT_WEIGHT_MIN: float = 0.5  # Poids minimum (protection contre sur-atténuation)
SENTIMENT_WEIGHT_MAX: float = 1.5  # Poids maximum (protection contre sur-amplification)

# --- 8. SYSTÈME & LOGGING ---
SYSTEM_CONFIG: SystemConfig = {
    "MAX_WORKERS": 3,  # Nombre max de threads parallèles pour le scraping (ne pas abuser)
    "LOG_LEVEL": "INFO",
    "RETENTION_DAYS": 30,  # Nettoyage de la DB (supprimer les vieux records > 30j)
}

# --- 8. APIS EXTERNES ---
# DexScreener, etc.
EXTERNAL_APIS: ExternalAPIs = {
    "DEXSCREENER_URL": "https://api.dexscreener.com/latest/dex/tokens/",
    "USER_AGENT": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)...",
    "FGI_API_URL": "https://api.alternative.me/fng/",  # Fear & Greed Index
    "COINGECKO_BASE": "https://api.coingecko.com/api/v3",  # Base URL CoinGecko API
    "COINGECKO_API_KEY": os.getenv("COINGECKO_API_KEY", None),
    "FGI_FETCH_INTERVAL": 1440,  # 1 fois par jour (en minutes)
    "HUGGINGFACE_TOKEN": os.getenv("HUGGINGFACE_TOKEN", None),  # Token pour CryptoBERT
}

# --- 9. DISCORD BOT ---
DISCORD_CONFIG = {
    # Token du bot Discord (depuis .env pour la sécurité)
    "BOT_TOKEN": os.getenv("DISCORD_BOT_TOKEN"),
    # ID du canal pour les alertes de signaux
    # Pour obtenir l'ID : Mode Développeur Discord > Clic droit sur canal > Copier l'ID
    "ALERT_CHANNEL_ID": int(os.getenv("DISCORD_ALERT_CHANNEL_ID") or "0"),
    # Activer/Désactiver le bot Discord
    "ENABLED": os.getenv("DISCORD_ENABLED", "True").lower() == "true",
    # Préfixe des commandes (par défaut: !)
    "COMMAND_PREFIX": "!",
}

