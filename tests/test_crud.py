from datetime import datetime, timedelta, timezone
import pytest
from unittest.mock import MagicMock, patch
from app.crud import electricity as crud
from app.models import ElectricityPrice
from app.providers import ProviderManager

# Helper to create UTC datetime
def utc_dt(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)

@pytest.mark.asyncio
async def test_get_electricity_prices_fetch_from_provider(db):
    # Setup
    start_date = utc_dt(2023, 10, 27, 0, 0)
    end_date = utc_dt(2023, 10, 27, 1, 0) # 1 hour range

    # Mock provider manager

    with patch("app.crud.electricity.get_provider_manager") as mock_get_manager:
        mock_manager_instance = MagicMock(spec=ProviderManager)
        mock_get_manager.return_value = mock_manager_instance

        async def mock_get_prices(start, end):
            # Return prices for every 15 mins in the range
            prices = []
            curr = start
            while curr <= end:
                prices.append(ElectricityPrice(timestamp=curr, price=10.0))
                curr += timedelta(minutes=15)
            return prices

        mock_manager_instance.get_electricity_price.side_effect = mock_get_prices

        # Execution
        prices = await crud.get_electricity_prices(db, start_date, end_date)

        # Verification
        assert len(prices) > 0
        assert prices[0].price == 10.0

        # Verify it was stored in DB
        db_prices = db.query(ElectricityPrice).all()
        assert len(db_prices) > 0
        assert db_prices[0].price == 10.0

@pytest.mark.asyncio
async def test_get_electricity_prices_cached(db):
    # Setup
    start_date = utc_dt(2023, 10, 27, 0, 0)
    end_date = utc_dt(2023, 10, 27, 0, 15)

    # Insert into DB
    db.add(ElectricityPrice(timestamp=start_date, price=20.0))
    db.add(ElectricityPrice(timestamp=end_date, price=20.0))
    db.commit()

    # Mock provider manager to ensure it's NOT called
    with patch("app.crud.electricity.get_provider_manager") as mock_get_manager:
        # Execution
        prices = await crud.get_electricity_prices(db, start_date, end_date)

        # Verification
        assert len(prices) == 2
        assert prices[0].price == 20.0
        mock_get_manager.assert_not_called()

@pytest.mark.asyncio
async def test_get_electricity_prices_provider_missing_data(db):
    """Test when provider returns empty list but we have missing intervals."""
    # Setup - range in the PAST so filter_future_intervals allows it
    start_date = utc_dt(2023, 1, 1, 0, 0)
    end_date = utc_dt(2023, 1, 1, 0, 15)

    with patch("app.crud.electricity.get_provider_manager") as mock_get_manager:
        mock_manager_instance = MagicMock(spec=ProviderManager)
        mock_get_manager.return_value = mock_manager_instance

        # Provider returns NO prices
        async def mock_get_prices(start, end):
            return []

        mock_manager_instance.get_electricity_price.side_effect = mock_get_prices

        # Execution
        prices = await crud.get_electricity_prices(db, start_date, end_date)

        # Verification
        # Should contain NULL prices (None)
        assert len(prices) == 2
        assert prices[0].price is None

        # Verify stored as NULL in DB
        db_prices = db.query(ElectricityPrice).all()
        assert len(db_prices) == 2
        assert db_prices[0].price is None
