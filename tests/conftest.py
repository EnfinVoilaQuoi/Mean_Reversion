"""Pytest configuration and shared fixtures for Mean Reversion test suite."""

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest


@pytest.fixture
def sample_config_db() -> dict[str, Any]:
    """Sample database configuration for testing."""
    return {
        "HOST": "localhost",
        "PORT": 5432,
        "NAME": "test_db",
        "USER": "test_user",
        "PASSWORD": "test_pass",
    }


@pytest.fixture
def sample_config_analysis() -> dict[str, Any]:
    """Sample analysis configuration for testing."""
    return {
        "ZSCORE_WINDOW_DAYS": 7,
        "LOOKBACK_AGGREGATION_MINUTES": 60,
        "SOCIAL_VOLUME_EPSILON": 0.1,
        "EPSILON_NOISE_RANGE": 0.05,
        "THRESHOLDS": {
            "ZSOCIAL_EXTREME": 3.0,
            "ZSOCIAL_HIGH": 2.0,
            "ZVOL_EXTREME": 3.0,
            "ZVOL_HIGH": 2.0,
            "ZPRICE_EXTREME": 3.0,
            "ZPRICE_HIGH": 2.0,
        },
    }


@pytest.fixture
def sample_screening_config() -> dict[str, Any]:
    """Sample screening configuration for testing."""
    return {
        "TOP_LIMIT": 1000,
        "TARGET_EXCHANGES": ["binance", "coinbase", "kraken"],
        "EXCHANGE_PRIORITY_ORDER": ["binance", "coinbase", "okx", "bybit"],
        "DERIVATIVE_MAPPING": {"BTCUSDT": "BTC", "ETHUSDT": "ETH"},
        "EXCLUDED_STABLECOINS": ["USDT", "USDC", "DAI", "BUSD"],
    }


@pytest.fixture
def sample_tweet_data() -> list[dict[str, Any]]:
    """Sample tweet data for testing social metrics."""
    base_time = datetime(2025, 12, 1, 12, 0, 0)
    return [
        {
            "id": 1,
            "symbol": "BTC",
            "created_at": base_time,
            "likes": 100,
            "retweets": 50,
            "replies": 25,
            "views": 10000,
            "followers": 50000,
            "sentiment_score": 0.8,
        },
        {
            "id": 2,
            "symbol": "BTC",
            "created_at": base_time + timedelta(minutes=30),
            "likes": 200,
            "retweets": 100,
            "replies": 50,
            "views": 20000,
            "followers": 75000,
            "sentiment_score": 0.6,
        },
        {
            "id": 3,
            "symbol": "ETH",
            "created_at": base_time + timedelta(hours=1),
            "likes": 150,
            "retweets": 75,
            "replies": 30,
            "views": 15000,
            "followers": 60000,
            "sentiment_score": 0.7,
        },
    ]


@pytest.fixture
def sample_aggregated_social_data() -> dict[str, Any]:
    """Sample aggregated social metrics for testing."""
    return {
        "social_volume": 1250.5,
        "social_density": 0.85,
        "weighted_sentiment": 0.72,
        "total_interactions": 575,
        "unique_authors": 3,
        "reach": 185000,
    }


@pytest.fixture
def sample_zscore_data() -> list[dict[str, float]]:
    """Sample z-score historical data for testing."""
    base_time = datetime(2025, 11, 24, 0, 0, 0)
    data = []
    for i in range(7):
        timestamp = base_time + timedelta(days=i)
        data.append(
            {
                "timestamp": timestamp.timestamp(),
                "social_volume": 1000 + (i * 100),
                "social_density": 0.5 + (i * 0.05),
                "price": 45000 + (i * 500),
            }
        )
    return data


@pytest.fixture
def sample_token_data() -> list[dict[str, Any]]:
    """Sample token screening data for testing."""
    return [
        {
            "id": "bitcoin",
            "symbol": "BTC",
            "name": "Bitcoin",
            "market_cap_rank": 1,
            "platforms": {},
            "exchanges": {
                "binance": {"pair": "BTCUSDT", "volume_24h": 1000000000},
                "coinbase": {"pair": "BTC-USD", "volume_24h": 500000000},
            },
        },
        {
            "id": "ethereum",
            "symbol": "ETH",
            "name": "Ethereum",
            "market_cap_rank": 2,
            "platforms": {},
            "exchanges": {
                "binance": {"pair": "ETHUSDT", "volume_24h": 800000000},
                "kraken": {"pair": "ETHUSD", "volume_24h": 300000000},
            },
        },
    ]


@pytest.fixture
def mock_database():
    """Mock database connection for testing."""
    db_mock = MagicMock()
    db_mock.connect.return_value = True
    db_mock.is_closed.return_value = False
    db_mock.close.return_value = None
    return db_mock


@pytest.fixture
def mock_http_session():
    """Mock HTTP session for testing API calls."""
    session_mock = Mock()
    response_mock = Mock()
    response_mock.status_code = 200
    response_mock.json.return_value = {"success": True}
    response_mock.text = '{"success": true}'
    session_mock.get.return_value = response_mock
    session_mock.post.return_value = response_mock
    return session_mock


@pytest.fixture
def freeze_time():
    """Fixture to freeze time for consistent testing."""
    return datetime(2025, 12, 5, 12, 0, 0)


@pytest.fixture
def epsilon_config() -> dict[str, float]:
    """Configuration for epsilon noise in social volume calculations."""
    return {"SOCIAL_VOLUME_EPSILON": 0.1, "EPSILON_NOISE_RANGE": 0.05}
