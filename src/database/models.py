"""
Modles de donnes pour le systme de surveillance sociale crypto.
Utilise Peewee ORM avec SQLite en mode WAL pour la gestion de la concurrence.
"""

from datetime import datetime
from typing import Any, cast

from peewee import (
    AutoField,
    BooleanField,
    CharField,
    DateTimeField,
    FloatField,
    ForeignKeyField,
    IntegerField,
    Model,
    SqliteDatabase,
    TextField,
)

# Import relatif propre (pas de manipulation sys.path)
from ..config import DB_CONFIG

# ============================================================================
# 1. CONFIGURATION DE BASE
# ============================================================================

# Initialisation de la base de donnes SQLite avec mode WAL
# (Write-Ahead Logging pour grer la concurrence entre scrapers et analyseurs)
db = SqliteDatabase(DB_CONFIG["name"], pragmas=DB_CONFIG["pragmas"])


class BaseModel(Model):
    """
    Classe de base pour dfinir la base de donnes de toutes les tables.
    Tous les modles hritent de cette classe.
    """

    class Meta:
        database = db


# ============================================================================
# 2. TABLE TOKEN : Le Catalogue des Actifs [.]ª™
# ============================================================================


class Token(BaseModel):
    """
    Table principale contenant les informations des tokens sous surveillance.

    Attributs:
        symbol (str): Symbole du token (ex: PEPE)
        name (str): Nom complet du token (ex: Pepe)
        contract (str): Adresse du contrat primaire (unique, index)
        cashtag (str): Format Twitter du token (ex: $PEPE)

        # CoinGecko & Ranking
        coingecko_id (str): ID unique CoinGecko (ex: 'pepe', 'bitcoin')
        rank (int): Classement MarketCap (pour filtrage Top 1000)

        # Source de Prix Primaire
        primary_price_source (str): Plateforme privilégiée (ex: MEXC, PancakeSwap)
        primary_chain_id (str): Chaîne de la paire la plus liquide (ex: binance-smart-chain)
        primary_pair_address (str): Adresse du pool/paire principale
        contract_addresses (str): JSON de tous les contrats par chaîne

        # Métadonnées Trading (Anciennes - pour compatibilité)
        chain_id (str): ID de la blockchain (ex: ethereum, bsc, solana)
        pair_address (str): Adresse de la paire de trading (ex: 0x123...)

        # Liens Sociaux
        twitter_handle (str): Username Twitter officiel du projet (ex: NEARProtocol)
        twitter_link (str): URL complète du compte Twitter
        telegram_link (str): URL du canal/groupe Telegram

        # État du Bot
        status (str): État actuel (PENDING, REGULAR, AGGRESSIVE)
        official_followers (int): Nombre de followers Twitter officiels
        created_at (datetime): Date d'ajout à la base de données
    """

    id = AutoField()
    symbol = CharField(index=True)
    name = CharField(null=True)  # Nom complet du token
    contract = CharField(unique=True, index=True)  # Adresse du contrat primaire
    cashtag = CharField()

    # CoinGecko & Ranking (pour screening)
    coingecko_id = CharField(null=True, unique=True, index=True)  # ID unique CoinGecko
    rank = IntegerField(null=True, index=True)  # Classement MarketCap

    # Source de Prix Primaire (déterminée par screening)
    primary_price_source = CharField(null=True)  # Ex: MEXC, PancakeSwap, Bitget
    primary_chain_id = CharField(null=True)  # Ex: binance-smart-chain, ethereum
    primary_pair_address = CharField(null=True)  # Adresse du pool/paire principale
    contract_addresses = TextField(
        null=True
    )  # JSON: {"ethereum": "0x123...", "bsc": "0x456..."}

    # Volumes de Trading (24h en USD)
    primary_volume_24h = FloatField(
        null=True
    )  # Volume sur la plateforme primaire choisie
    total_volume_24h = FloatField(null=True)  # Volume total agrégé (toutes plateformes)

    # Métadonnées Trading (anciennes - pour compatibilité avec code existant)
    chain_id = CharField(default="ethereum")  # Blockchain du token
    pair_address = CharField(null=True)  # Adresse de la paire de trading

    # Liens Sociaux
    twitter_handle = CharField(
        null=True
    )  # Username du compte Twitter officiel (sans @)
    twitter_link = TextField(
        null=True
    )  # URL complète Twitter (ex: https://twitter.com/username)
    telegram_link = TextField(null=True)  # URL Telegram (ex: https://t.me/channelname)

    # État du Bot
    status = CharField(
        default="PENDING", index=True
    )  # Mode surveillance: INCOMPLETE, LISTED, PENDING, REGULAR, AGGRESSIVE, CROISIERE

    # Statut de Classement (Screening)
    rank_status = CharField(
        default="OUT_OF_RANK", index=True
    )  # MONITORING, OUT_OF_RANK, IGNORED
    last_rank_update = DateTimeField(default=datetime.now)  # Dernière MAJ du classement

    official_followers = IntegerField(null=True)

    # Statut de Vérification Twitter
    twitter_verified = BooleanField(null=True)  # Legacy verification (pre-2023)
    twitter_blue = BooleanField(null=True)  # Twitter Blue / X Premium
    twitter_blue_type = CharField(null=True)  # Type: "Blue", "Business", "Government"
    twitter_verification_checked_at = DateTimeField(null=True)  # Dernière vérification
    twitter_verification_lost_at = DateTimeField(
        null=True
    )  # Quand la coche a été perdue

    created_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "tokens"

    def __repr__(self):
        return f"<Token {self.cashtag} ({self.status})>"


class RawTweet(BaseModel):
    """
    Table de stockage des tweets individuels avec leurs statistiques.
    Permet le suivi et la mise  jour des mtriques d'engagement.

    Attributs:
        token (Token): Rfrence vers le token concern
        tweet_id (str): ID unique du tweet (identifiant Twitter)
        author (str): Username de l'auteur (@handle)
        content (str): Contenu textuel du tweet (optionnel, pour analyse future)
        posted_at (datetime): Date et heure de publication du tweet
        views (int): Nombre de vues du tweet
        likes (int): Nombre de likes
        retweets (int): Nombre de retweets
        quotes (int): Nombre de citations
        replies (int): Nombre de rponses
        impact_score (float): Score d'impact calcul (log10 pondr)
        last_updated (datetime): Dernire mise  jour des statistiques
        created_at (datetime): Date d'ajout en base de donnes
    """

    id = AutoField()
    token = ForeignKeyField(Token, backref="tweets", index=True)
    tweet_id = CharField(unique=True, index=True)  # ID Twitter unique
    author = CharField(index=True)  # @username
    content = TextField(null=True)  # Contenu du tweet (optionnel)
    posted_at = DateTimeField(index=True)  # Date de publication

    # Statistiques d'engagement
    views = IntegerField(default=0)
    likes = IntegerField(default=0)
    retweets = IntegerField(default=0)
    quotes = IntegerField(default=0)
    replies = IntegerField(default=0)

    # Mtriques calcules
    impact_score = FloatField(default=0.0)
    impact_rate = FloatField(default=0.0)  # Velocite d'impact (score/heures)

    # Analyse de sentiment (CryptoBERT)
    sentiment_label = CharField(
        max_length=15, null=True
    )  # 'BULLISH', 'BEARISH', 'NEUTRAL'
    sentiment_score = FloatField(null=True)  # Score de confiance (0.0-1.0)
    analyzed_by_bert = BooleanField(default=False)  # Flag pour éviter retraitement

    # Mtadonnes
    last_updated = DateTimeField(default=datetime.now)
    created_at = DateTimeField(default=datetime.now, index=True)

    class Meta:
        table_name = "raw_tweets"
        # Index composite pour recherche efficace par token et date
        indexes = (
            (("token", "posted_at"), False),
            (("token", "last_updated"), False),
        )

    def __repr__(self):
        return f"<RawTweet {self.tweet_id} by @{self.author} (Impact={self.impact_score:.2f})>"

    def calculate_impact_score(self, weights=None):
        """
        Calcule le score d'impact du tweet selon la formule :
        Impact = log10(Views + (Likes — 5) + (RTs — 20) + (Quotes — 15) + (Replies — 2) + 1)

        Args:
            weights (dict, optional): Poids personnaliss pour chaque mtrique

        Returns:
            float: Score d'impact calcul
        """
        import math

        # Poids par dfaut (depuis config.py)
        if weights is None:
            weights = {
                "VIEW": 1.0,
                "LIKE": 5.0,
                "RETWEET": 20.0,
                "QUOTE": 15.0,
                "REPLY": 2.0,
            }

        # Si pas de vues, estimation base sur l'engagement
        if self.views == 0:
            # 1 like [->]‰ˆ 100 vues empiriquement sur crypto Twitter
            estimated_views = (self.likes * 100) + (self.retweets * 500)
            raw_score = estimated_views
        else:
            raw_score = (
                self.views * weights["VIEW"]
                + self.likes * weights["LIKE"]
                + self.retweets * weights["RETWEET"]
                + self.quotes * weights.get("QUOTE", 15.0)
                + self.replies * weights.get("REPLY", 2.0)
            )

        # Transformation logarithmique pour normaliser
        self.impact_score = math.log10(raw_score + 1)
        return self.impact_score

    def update_stats(self, tweet_data):
        """
        Met  jour les statistiques du tweet  partir de donnes fraches.

        Args:
            tweet_data (dict): Donnes du tweet depuis ntscraper

        Returns:
            bool: True si des changements ont t dtects
        """
        stats = tweet_data.get("stats", {})

        old_views = self.views
        old_likes = self.likes
        old_retweets = self.retweets

        # Mise  jour des statistiques
        self.views = stats.get("views", stats.get("view", 0))
        self.likes = stats.get("likes", 0)
        self.retweets = stats.get("retweets", 0)
        self.quotes = stats.get("quotes", 0)
        self.replies = stats.get("replies", 0)

        # Recalcul du score d'impact
        self.calculate_impact_score()

        # Mise  jour du timestamp
        self.last_updated = datetime.now()

        # Dtection de changements significatifs
        return (
            self.views != old_views
            or self.likes != old_likes
            or self.retweets != old_retweets
        )



class ProjectFollowers(BaseModel):
    """
    Table d'historique du nombre de followers du projet.
    Permet de suivre l'volution de la popularit du token.

    Attributs:
        token (Token): Rfrence vers le token concern
        timestamp (datetime): Moment de la capture
        followers_count (int): Nombre de followers
        following_count (int): Nombre d'abonnements (optionnel)
    """

    id = AutoField()
    token = ForeignKeyField(Token, backref="followers_history", index=True)
    timestamp = DateTimeField(default=datetime.now, index=True)
    followers_count = IntegerField()
    following_count = IntegerField(null=True)

    class Meta:
        table_name = "project_followers"
        indexes = (
            (("token", "timestamp"), True),  # Index unique par token et timestamp
        )

    def __repr__(self):
        return f"<ProjectFollowers {self.token.cashtag} @ {self.timestamp}: {self.followers_count}>"


# ============================================================================
# 3. TABLE SOCIALMETRIC : Les Sries Temporelles (La Hype) [.]“Š
# ============================================================================


class SocialMetric(BaseModel):
    """
    Table des métriques sociales UNIQUEMENT (Architecture 3NF).

    IMPORTANT: Cette table ne contient plus les métriques financières ni les scores.
    - Métriques financières → PriceMetric
    - Scores et divergence → SignalMetric

    Utilisez signal_helpers.get_complete_signal_dict() pour récupérer toutes les métriques
    en JOINant SocialMetric + PriceMetric + SignalMetric.

    Attributs:
        token (Token): Référence vers le token concerné
        timestamp (datetime): Moment du snapshot (toutes les 5/15min selon mode)

        # Métriques Sociales (UNIQUEMENT)
        social_volume (float): Volume Social Brut (Étape 1 du Z-Score)
        social_density (float): Densité Sociale (social_volume / log10(trading_volume))

        # Z-Scores (gardés pour compatibilité legacy - préférer SignalMetric)
        z_score_raw (float): Z-Score Social Brut
        z_score_final (float): Z-Score Social Final (alias de z_score_raw)
    """

    id = AutoField()
    token = ForeignKeyField(Token, backref="metrics", index=True)
    timestamp = DateTimeField(index=True)

    # Métriques Sociales (UNIQUEMENT - Architecture 3NF)
    social_volume = FloatField()  # Volume d'Impact Social (brut)
    social_density = FloatField(null=True)  # Densité Sociale

    # Z-Scores (gardés pour compatibilité legacy - préférer SignalMetric pour les nouveaux usages)
    z_score_raw = FloatField(null=True)  # Social Z-Score Brut
    z_score_final = FloatField(null=True)  # Social Z-Score Final (Alias)

    # Flags d'imputation (Migration 003) - Traçabilité des données imputées
    is_imputed = BooleanField(default=False, null=False)
    # Flag indiquant si cette métrique a été imputée (vs scrapée depuis Twitter)

    imputation_method = CharField(null=True)
    # Méthode d'imputation utilisée: 'linear_interpolation', 'adjacent_average', etc.
    # NULL si données réelles (is_imputed=False)

    updated_at = DateTimeField(null=True)
    # Timestamp de dernière mise à jour (pour tracer remplacement imputé → réel)

    # Flags de validation et qualité (Migration 004) - Système dual Z-Score
    is_valid = BooleanField(default=True, null=False)
    # Flag indiquant si la métrique est cohérente (False si anomalie détectée)

    collection_method = CharField(default='scraping', null=False)
    # Méthode de collecte: 'scraping', 'webhook', 'imputed'

    data_quality = CharField(default='uncertain', null=False)
    # Qualité des données: 'high' (webhook/scraping fiable), 'uncertain' (scraping normal), 'low' (epsilon/imputed)

    class Meta:
        table_name = "social_metrics"
        # Index unique : garantit une seule entre par Token et par priode
        # Indispensable pour la vitesse de recherche et viter les doublons
        indexes = (
            (("token", "timestamp"), True),  # True = index unique
        )

    def __repr__(self):
        return f"<SocialMetric {self.token.cashtag} @ {self.timestamp} (Z={self.z_score_final})>"


# ============================================================================
# 4. TABLE SIGNALLOG : Le Journal des Dcisions (Le Backtesting) [.]š¨
# ============================================================================


class SignalLog(BaseModel):
    """
    Table d'historique des signaux et dcisions.
    Cruciale pour le backtesting et l'analyse de performance.

    Attributs:
        token (Token): Rfrence vers le token concern
        timestamp (datetime): Moment du dclenchement du signal
        signal_type (str): Type de signal (HYPE_SPIKE, DIVERGENCE, LEGIT_FAIL, etc.)
        value (float): Valeur du Z-Score au moment du signal
        status (str): ‰tat du signal (OPEN, CLOSED, MISSED)
        technical_signal_received (bool): Signal technique confirm par l'analyse technique
    """

    id = AutoField()
    token = ForeignKeyField(Token, backref="signals", index=True)
    timestamp = DateTimeField(default=datetime.now, index=True)
    signal_type = CharField()  # HYPE_SPIKE, DIVERGENCE, LEGIT_FAIL, EXTREME_Z, etc.
    value = FloatField()  # Valeur du Z-Score au dclenchement
    status = CharField(default="OPEN")  # OPEN, CLOSED, MISSED
    technical_signal_received = BooleanField(default=False)

    # Analyse de la source du hype
    hype_source = CharField(null=True)  # COMMUNITY, NEWS, MIXED, UNKNOWN
    official_contribution = FloatField(
        null=True
    )  # Ratio de contribution du compte officiel (0.0-1.0)

    class Meta:
        table_name = "signal_logs"

    def __repr__(self):
        return (
            f"<SignalLog {self.token.cashtag} - {self.signal_type} @ {self.timestamp}>"
        )


# ============================================================================
# 5. TABLE PRICE METRICS : Données OHLC Historiques
# ============================================================================


class PriceMetric(BaseModel):
    """
    Table des données de prix historiques OHLC + Z-Scores Prix.
    Structure similaire à SocialMetric pour cohérence du système.

    Attributs:
        token (Token): Référence vers le token concerné
        timestamp (datetime): Moment de la capture (candle open time)
        resolution (str): Résolution temporelle (ex: '5m', '15m', '1h', '4h', '1d')

        # Données OHLC (Phase 1 - Backfill)
        open (float): Prix d'ouverture
        high (float): Prix le plus haut
        low (float): Prix le plus bas
        close (float): Prix de clôture
        volume (float): Volume de trading pendant cette période

        # Métriques de Z-Score Prix (Phase 2 - Calcul)
        z_score_price (float): Z-Score du prix (prix_actuel - μ) / σ
        mean_price (float): Moyenne mobile sur N jours (μ)
        std_price (float): Écart-type sur N jours (σ)
        data_points_used (int): Nombre de points utilisés pour le calcul
    """

    id = AutoField()
    token = ForeignKeyField(Token, backref="price_history", index=True)
    timestamp = DateTimeField(index=True)  # Open time du candle
    resolution = CharField(default="15m")  # 5m, 15m, 1h, 4h, 1d

    # Données OHLC (Phase 1 - Backfill)
    open = FloatField()
    high = FloatField()
    low = FloatField()
    close = FloatField()
    volume = FloatField(null=True)  # Volume en USD

    # Métriques de Z-Score Prix (Phase 2 - Calcul)
    z_score_price = FloatField(null=True)  # Z-Score du prix
    mean_price = FloatField(null=True)  # Moyenne mobile (μ)
    std_price = FloatField(null=True)  # Écart-type (σ)
    data_points_used = IntegerField(null=True)  # Nombre de points pour le calcul

    class Meta:
        table_name = "price_metrics"
        # Index unique : garantit une seule entrée par Token, timestamp et resolution
        indexes = (
            (("token", "timestamp", "resolution"), True),  # True = index unique
        )

    def __repr__(self):
        return f"<PriceMetric {self.token.cashtag} @ {self.timestamp} ({self.resolution}) Close=${self.close:.6f}>"


# ============================================================================
# 6. TABLE SIGNAL METRIC : Agrégation des Signaux (Z-Scores + Divergence)
# ============================================================================


class SignalMetric(BaseModel):
    """
    Table d'agrégation des signaux combinant les métriques sociales et financières.
    C'est la source de vérité pour les signaux de mean reversion (divergence).

    Cette table suit l'architecture 3NF en séparant les responsabilités :
    - SocialMetric → Métriques sociales pures
    - PriceMetric → Métriques financières pures
    - SignalMetric → Signaux calculés (JOIN des deux)

    Attributs:
        token (Token): Référence vers le token concerné
        timestamp (datetime): Moment du snapshot

        # Références aux métriques sources (pour traçabilité)
        social_metric (SocialMetric): FK vers la métrique sociale source
        price_metric (PriceMetric): FK vers la métrique prix source (nullable si prix manquant)

        # Z-Scores (snapshots au moment du signal)
        z_score_social (float): Z-Score Social Final (depuis SocialMetric.z_score_final)
        z_score_price (float): Z-Score Prix (depuis PriceMetric.z_score_price)

        # Signal calculé
        divergence_score (float): Divergence (Z_Social - Z_Price) - signal de mean reversion
        signal_strength (float): Force du signal (abs(divergence_score))

        # Contexte macro (optionnel)
        fgi_correction (int): Fear & Greed Index au moment du signal
        z_vol (float): Z-Score du Volume Global (filtre de fiabilité)
    """

    id = AutoField()
    token = ForeignKeyField(Token, backref="signal_metrics", index=True)
    timestamp = DateTimeField(index=True)

    # Références aux métriques sources (traçabilité)
    social_metric = ForeignKeyField(SocialMetric, backref="signals", null=True)
    price_metric = ForeignKeyField(PriceMetric, backref="signals", null=True)

    # Z-Scores (snapshots)
    z_score_social = FloatField()
    z_score_price = FloatField(null=True)

    # Dual Z-Score (Migration 004)
    z_score_vs_activity = FloatField(null=True)
    # Z-Score vs activité réelle uniquement (exclut epsilon de la baseline)

    # Signal calculé
    divergence_score = FloatField()
    signal_strength = FloatField(null=True)  # abs(divergence)

    # Contexte macro (optionnel)
    fgi_correction = IntegerField(null=True)
    z_vol = FloatField(null=True)

    class Meta:
        table_name = "signal_metrics"
        # Index unique : garantit une seule entrée par Token et timestamp
        indexes = (
            (("token", "timestamp"), True),  # True = index unique
        )

    def __repr__(self):
        return f"<SignalMetric {self.token.cashtag} @ {self.timestamp} (Div={self.divergence_score:.2f})>"


# ============================================================================
# 7. TABLE MACRO METRICS : FGI et Volume Global
# ============================================================================


class MacroMetric(BaseModel):
    """
    Stocke les métriques macro-économiques (FGI, Volume Global 24h, etc.)
    avec un horodatage pour contrôler la fréquence des appels API.

    Attributs:
        timestamp (datetime): Moment de la capture
        fear_greed_index (int): Fear & Greed Index (0-100) depuis alternative.me
        global_volume_usd (float): Volume global crypto 24h en USD depuis CoinGecko
    """

    id = AutoField()
    timestamp = DateTimeField(default=datetime.now, index=True)

    # Fear & Greed Index (alternative.me) - Valeur Int de 0 à 100
    fear_greed_index = IntegerField(null=True)

    # Volume Global Crypto 24h (CoinGecko) - Valeur en USD
    global_volume_usd = FloatField(null=True)

    class Meta:
        table_name = "macro_metrics"
        indexes = ((("timestamp",), False),)

    def __repr__(self):
        return f"<MacroMetric @ {self.timestamp} (FGI={self.fear_greed_index})>"


class GapAttempt(BaseModel):
    """
    Enregistre chaque tentative de scraping d'un gap individuellement.
    Permet de tracer l'historique complet des tentatives et identifier les méthodes qui fonctionnent.

    Attributs:
        token (ForeignKey): Token concerné
        since_date (str): Date de début du gap (YYYY-MM-DD HH:00:00)
        until_date (str): Date de fin du gap (YYYY-MM-DD HH:00:00)
        method (str): Méthode de scraping utilisée ('twitterio_top', 'twitterio_latest', 'playwright', 'twscrape')
        nitter_instance (str): URL de l'instance Nitter (NULL si pas Playwright)
        attempted_at (datetime): Moment de la tentative
        success (bool): True si des tweets ont été trouvés, False sinon
        tweet_count (int): Nombre de tweets récupérés lors de cette tentative
        error_type (str): Type d'erreur si échec ('auth_failure', 'rate_limit', 'instance_unavailable', 'empty_result', 'unknown')
        error_message (str): Message d'erreur complet si échec
    """

    id = AutoField()
    token = ForeignKeyField(Token, backref="gap_attempts")
    since_date = CharField()  # Format: YYYY-MM-DD HH:00:00
    until_date = CharField()  # Format: YYYY-MM-DD HH:00:00
    method = CharField()  # twitterio_top, twitterio_latest, playwright, twscrape
    nitter_instance = CharField(null=True)  # Instance Nitter si Playwright
    attempted_at = DateTimeField(default=datetime.now, index=True)
    success = BooleanField()  # True si tweets trouvés
    tweet_count = IntegerField(default=0)
    error_type = CharField(null=True)  # auth_failure, rate_limit, instance_unavailable, empty_result, unknown
    error_message = TextField(null=True)

    class Meta:
        table_name = "gap_attempts"
        indexes = (
            (("token", "since_date", "until_date"), False),
            (("token",), False),
            (("method",), False),
        )

    def __repr__(self):
        status = "SUCCESS" if self.success else f"FAILED ({self.error_type})"
        return f"<GapAttempt {self.token.cashtag} {self.since_date}-{self.until_date} {self.method} {status}>"


class ScrapedGap(BaseModel):
    """
    Trace les gaps de tweets avec suivi granulaire de la complétude.
    Système V2 avec précision heure par heure et tracking des méthodes essayées.

    Attributs:
        token (ForeignKey): Token concerné
        since_date (str): Date de début du gap (YYYY-MM-DD HH:00:00) - précision heure
        until_date (str): Date de fin du gap (YYYY-MM-DD HH:00:00) - précision heure
        expected_hours (int): Nombre d'heures attendues dans ce gap
        covered_hours (int): Nombre d'heures avec tweets récupérés
        completeness_pct (float): Pourcentage de complétude (covered/expected * 100)
        status (str): Statut du gap ('partial', 'complete', 'all_methods_exhausted', 'pending')
        methods_tried (str): Liste séparée par virgules des méthodes essayées
        first_attempt (datetime): Date de première tentative de scraping
        last_attempt (datetime): Date de dernière tentative de scraping
        scraped_at (datetime): Deprecated - utilisé pour compatibilité avec ancien système
        tweet_count (int): Nombre total de tweets scrapés dans ce gap
    """

    id = AutoField()
    token = ForeignKeyField(Token, backref="scraped_gaps")
    since_date = CharField()  # Format: YYYY-MM-DD HH:00:00
    until_date = CharField()  # Format: YYYY-MM-DD HH:00:00

    # Métriques de complétude (V2)
    expected_hours = IntegerField(default=0)
    covered_hours = IntegerField(default=0)
    completeness_pct = FloatField(default=0.0)
    status = CharField(default='pending')  # partial, complete, all_methods_exhausted, pending
    methods_tried = CharField(default='')  # Comma-separated list
    first_attempt = DateTimeField(null=True, index=True)
    last_attempt = DateTimeField(null=True, index=True)

    # Champs legacy (compatibilité)
    scraped_at = DateTimeField(default=datetime.now, index=True)
    tweet_count = IntegerField(default=0)

    class Meta:
        table_name = "scraped_gaps"
        indexes = (
            (("token", "since_date", "until_date"), True),  # Unique constraint
            (("token", "scraped_at"), False),
            (("token", "status"), False),  # Pour filtrer par statut
        )

    def __repr__(self):
        return f"<ScrapedGap {self.token.cashtag} {self.since_date}-{self.until_date} {self.completeness_pct:.1f}% {self.status}>"


# ============================================================================
# 6. FONCTION D'INITIALISATION
# ============================================================================


def initialize_db():
    """
    Cre les tables si elles n'existent pas.
    € appeler au dmarrage de l'application.

    Returns:
        bool: True si l'initialisation russit
    """
    try:
        with db:
            # Cration de toutes les tables dfinies
            db.create_tables(
                [
                    Token,
                    SocialMetric,
                    SignalLog,
                    RawTweet,
                    ProjectFollowers,
                    PriceMetric,
                    SignalMetric,
                    MacroMetric,
                    ScrapedGap,
                ],
                safe=True,
            )
            print("[OK] Base de donnes initialise avec succs.")
            return True
    except Exception as e:
        print(f"Œ Erreur lors de l'initialisation de la DB : {e}")
        return False


def get_db_connection():
    """
    Retourne la connexion  la base de donnes.
    Utile pour les oprations avances ou le debugging.

    Returns:
        SqliteDatabase: Instance de la connexion DB
    """
    return db


# ============================================================================
# 6. FONCTIONS UTILITAIRES (OPTIONNELLES)
# ============================================================================


def cleanup_old_metrics(days=30):
    """
    Nettoie les mtriques sociales plus anciennes que X jours.
    € utiliser pour grer la taille de la DB sur le long terme.

    Args:
        days (int): Nombre de jours de rtention

    Returns:
        int: Nombre d'enregistrements supprims
    """
    from datetime import timedelta

    cutoff_date = datetime.now() - timedelta(days=days)
    deleted = (
        SocialMetric.delete().where(SocialMetric.timestamp < cutoff_date).execute()
    )

    print(f"[.]§¹ {deleted} anciennes mtriques supprimes (> {days} jours).")
    return deleted


def get_token_stats(contract: str) -> dict[str, Any] | None:
    """
    Rcupre les statistiques d'un token.

    Args:
        contract (str): Adresse du contrat

    Returns:
        Optional[Dict[str, Any]]: Statistiques du token ou None si non trouv
    """
    try:
        token = Token.get(Token.contract == contract)
        metrics_count = token.metrics.count()
        signals_count = token.signals.count()

        return {
            "symbol": token.symbol,
            "cashtag": token.cashtag,
            "status": token.status,
            "followers": token.official_followers,
            "metrics_count": metrics_count,
            "signals_count": signals_count,
            "created_at": token.created_at,
        }
    except Token.DoesNotExist:
        return None


def get_tweets_in_window(
    token: Token, start_time: datetime, end_time: datetime
) -> list["RawTweet"]:
    """
    Rcupre tous les tweets d'un token dans une fentre temporelle.

    Args:
        token (Token): Token concern
        start_time (datetime): Dbut de la fentre
        end_time (datetime): Fin de la fentre

    Returns:
        list[RawTweet]: Liste des tweets dans la fentre
    """
    return cast(
        list["RawTweet"],
        list(
            RawTweet.select()
            .where(
                (RawTweet.token == token)
                & (RawTweet.posted_at >= start_time)
                & (RawTweet.posted_at < end_time)
            )
            .order_by(RawTweet.posted_at)
        ),
    )


def get_tweets_needing_update(
    token: Token, lookback_hours: int = 6
) -> list["RawTweet"]:
    """
    Rcupre les tweets actifs (rcents) qui ncessitent une mise  jour de leurs stats.

    Args:
        token (Token): Token concern
        lookback_hours (int): Nombre d'heures en arrire

    Returns:
        list[RawTweet]: Liste des tweets  mettre  jour
    """
    from datetime import timedelta

    cutoff_time = datetime.now() - timedelta(hours=lookback_hours)

    return cast(
        list["RawTweet"],
        list(
            RawTweet.select()
            .where((RawTweet.token == token) & (RawTweet.posted_at >= cutoff_time))
            .order_by(RawTweet.posted_at.desc())
        ),
    )


def aggregate_tweets_by_hour(
    token: Token, start_time: datetime, end_time: datetime
) -> dict[datetime, dict[str, Any]]:
    """
    Agrge les tweets par heure dans une fentre temporelle.

    Args:
        token (Token): Token concern
        start_time (datetime): Dbut de la fentre
        end_time (datetime): Fin de la fentre

    Returns:
        dict: Dictionnaire {heure: {'count': int, 'total_impact': float}}
    """
    from collections import defaultdict

    tweets = get_tweets_in_window(token, start_time, end_time)

    hourly_data: defaultdict[datetime, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "total_impact": 0.0}
    )

    for tweet in tweets:
        # Tronquer  l'heure
        hour_key = tweet.posted_at.replace(minute=0, second=0, microsecond=0)
        hourly_data[hour_key]["count"] += 1
        hourly_data[hour_key]["total_impact"] += tweet.impact_score

    return dict(hourly_data)


def get_latest_followers_count(token: Token) -> int | None:
    """
    Rcupre le dernier nombre de followers enregistr pour un token.

    Args:
        token (Token): Token concern

    Returns:
        int: Nombre de followers ou None si aucune donne
    """
    try:
        latest = (
            ProjectFollowers.select()
            .where(ProjectFollowers.token == token)
            .order_by(ProjectFollowers.timestamp.desc())
            .get()
        )
        return cast(int | None, latest.followers_count)
    except ProjectFollowers.DoesNotExist:
        return None


def get_latest_macro_metrics() -> MacroMetric | None:
    """
    Récupère la dernière entrée MacroMetric enregistrée.

    Returns:
        MacroMetric: Dernière métrique macro ou None si aucune donnée
    """
    try:
        return cast(
            MacroMetric,
            MacroMetric.select().order_by(MacroMetric.timestamp.desc()).get(),
        )
    except MacroMetric.DoesNotExist:
        return None


def get_latest_fgi_for_day() -> int | None:
    """
    Récupère la valeur FGI si elle a été enregistrée AUJOURD'HUI.

    Returns:
        int: Valeur du FGI (0-100) ou None si pas de donnée aujourd'hui
    """
    today = datetime.now().date()
    try:
        # Tente de trouver l'entrée la plus récente dont le jour est égal à aujourd'hui
        metric = (
            MacroMetric.select()
            .where(
                (MacroMetric.timestamp >= datetime.combine(today, datetime.min.time()))
                & (MacroMetric.fear_greed_index.is_null(False))
            )
            .order_by(MacroMetric.timestamp.desc())
            .get()
        )
        return cast(int, metric.fear_greed_index)
    except MacroMetric.DoesNotExist:
        return None


# ============================================================================
# TEST DE CONNEXION
# ============================================================================

if __name__ == "__main__":
    """
    Test basique de la configuration de la DB.
    Lance ce fichier directement pour vrifier que tout fonctionne.
    """
    print("[.]”§ Test de la configuration de la base de donnes...")

    # Test de connexion
    try:
        db.connect()
        print(f"[OK] Connexion russie : {DB_CONFIG['name']}")

        # Test de cration des tables
        initialize_db()

        # Test d'insertion simple
        test_token, created = Token.get_or_create(
            contract="0x123TEST",
            defaults={"symbol": "TEST", "cashtag": "$TEST", "official_followers": 1000},
        )

        if created:
            print(f"[OK] Token de test cr : {test_token}")
        else:
            print(f"[INFO]i¸  Token de test dj existant : {test_token}")

        # Nettoyage du test
        test_token.delete_instance()
        print("[OK] Test russi - Token de test supprim")

        db.close()

    except Exception as e:
        print(f"Œ Erreur durant le test : {e}")
        import traceback

        traceback.print_exc()
