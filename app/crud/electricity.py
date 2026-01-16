from datetime import datetime, timedelta, timezone
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.providers import get_provider_manager, ProviderError
from app import models
from app.services.electricity_service import ElectricityService
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
import logging

logger = logging.getLogger("app")

async def get_electricity_prices(
    db: Session, start_date: datetime, end_date: datetime
) -> List[models.ElectricityPrice]:
    """
    Get electricity prices for the specified time range.

    Args:
        db: The database session.
        start_date: The start date of the time range.
        end_date: The end date of the time range.
    Returns:
        List of electricity prices within the specified time range.
    """
    start_date = ElectricityService.floor_to_quarter(start_date)
    end_date = ElectricityService.floor_to_quarter(end_date)

    # Query existing prices from database using SQLAlchemy 2.0 select
    stmt = select(models.ElectricityPrice).where(
        models.ElectricityPrice.timestamp >= start_date,
        models.ElectricityPrice.timestamp <= end_date,
    ).order_by(models.ElectricityPrice.timestamp)

    result = db.execute(stmt)
    prices = result.scalars().all()

    missing_intervals = ElectricityService.calculate_missing_intervals(start_date, end_date, prices)
    fetchable_missing_intervals = ElectricityService.filter_future_intervals(missing_intervals)

    if fetchable_missing_intervals:
        try:
            provider_manager = get_provider_manager()

            # Fetch the entire fetchable missing range in one request
            min_ts = min(fetchable_missing_intervals)
            max_ts = max(fetchable_missing_intervals)

            logger.info(
                "Fetching %d fetchable missing intervals from %s to %s (filtered out %d future intervals)",
                len(fetchable_missing_intervals), min_ts, max_ts,
                len(missing_intervals) - len(fetchable_missing_intervals)
            )

            # Fetch data from provider
            all_provider_prices = await provider_manager.get_electricity_price(min_ts, max_ts)

            # Expand hourly prices to 15-minute intervals if needed
            expanded_prices = ElectricityService.expand_hourly_prices(all_provider_prices)
            if len(expanded_prices) > len(all_provider_prices):
                 logger.info(f"Expanded {len(all_provider_prices)} hourly prices to {len(expanded_prices)} 15-minute intervals")

            # Filter to only include fetchable missing timestamps
            new_prices = [
                price for price in expanded_prices
                if price.timestamp in fetchable_missing_intervals
            ]

            logger.info("Provider returned %d prices, %d are new", len(all_provider_prices), len(new_prices))

            # Batch insert logic
            batch_size = 500
            total_inserted = 0

            if new_prices:
                for i in range(0, len(new_prices), batch_size):
                    batch = new_prices[i:i + batch_size]
                    price_dicts = [{"timestamp": p.timestamp, "price": p.price} for p in batch]

                    stmt = sqlite_insert(models.ElectricityPrice).values(price_dicts)
                    stmt = stmt.on_conflict_do_nothing(index_elements=['timestamp'])
                    db.execute(stmt)
                    total_inserted += len(batch)

                db.commit()
                logger.info("Inserted %d new prices into database", total_inserted)

            fetched_timestamps = {p.timestamp for p in new_prices}
            still_missing = fetchable_missing_intervals - fetched_timestamps

            if still_missing:
                logger.warning(
                    "Data unavailable for %d intervals from all providers, inserting NULL placeholders",
                    len(still_missing)
                )

                null_prices = [
                    models.ElectricityPrice(timestamp=ts, price=None)
                    for ts in still_missing
                ]

                for i in range(0, len(null_prices), batch_size):
                    batch = null_prices[i:i + batch_size]
                    price_dicts = [{"timestamp": p.timestamp, "price": p.price} for p in batch]

                    stmt = sqlite_insert(models.ElectricityPrice).values(price_dicts)
                    stmt = stmt.on_conflict_do_nothing(index_elements=['timestamp'])
                    db.execute(stmt)

                db.commit()
                logger.info("Inserted %d NULL placeholders for unavailable data", len(null_prices))
                new_prices.extend(null_prices)

            # Merge existing and new prices
            all_prices = list(set(prices) | set(new_prices))
            all_prices.sort(key=lambda x: x.timestamp)

        except (ProviderError, Exception) as e:
            logger.error("Provider error: %s", str(e))
            db.rollback()
            all_prices = list(prices)
            all_prices.sort(key=lambda x: x.timestamp)
    else:
        all_prices = list(set(prices))
        all_prices.sort(key=lambda x: x.timestamp)

    return all_prices