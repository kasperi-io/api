from datetime import datetime, timedelta, timezone
from typing import List, Set, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from dateutil.tz import gettz as ZoneInfo

from app import models

class ElectricityService:
    @staticmethod
    def floor_to_quarter(dt: datetime) -> datetime:
        """Normalize datetime to previous 15-minute boundary."""
        minute = (dt.minute // 15) * 15
        return dt.replace(minute=minute, second=0, microsecond=0)

    @staticmethod
    def calculate_missing_intervals(
        start_date: datetime,
        end_date: datetime,
        existing_prices: List[models.ElectricityPrice]
    ) -> Set[datetime]:
        """Calculate which 15-minute intervals are missing from the given range."""
        start_date = ElectricityService.floor_to_quarter(start_date)
        end_date = ElectricityService.floor_to_quarter(end_date)

        quarters_diff = int((end_date - start_date).total_seconds() // 900)
        expected_intervals = {
            start_date + timedelta(minutes=15 * i)
            for i in range(quarters_diff + 1)
        }

        existing_intervals = {price.timestamp for price in existing_prices}
        return expected_intervals - existing_intervals

    @staticmethod
    def filter_future_intervals(intervals: Set[datetime]) -> Set[datetime]:
        """Filter out intervals that are not yet available from providers."""
        stockholm_tz = ZoneInfo("Europe/Stockholm")
        current_stockholm = datetime.now(stockholm_tz)
        current_stockholm_hour = current_stockholm.hour

        if current_stockholm_hour >= 14:
            # After 14:00 Stockholm: next day data available until 21:00 next day Stockholm
            next_day_stockholm = current_stockholm.date() + timedelta(days=1)
            max_available_stockholm = datetime.combine(
                next_day_stockholm, datetime.min.time()
            ).replace(tzinfo=stockholm_tz) + timedelta(hours=21)
            max_available_time = max_available_stockholm.astimezone(timezone.utc)
        else:
            # Before 14:00 Stockholm: only current Stockholm day available
            end_of_day_stockholm = current_stockholm.replace(
                hour=23, minute=59, second=59, microsecond=999999
            )
            max_available_time = end_of_day_stockholm.astimezone(timezone.utc)

        return {ts for ts in intervals if ts <= max_available_time}

    @staticmethod
    def expand_hourly_prices(prices: List[models.ElectricityPrice]) -> List[models.ElectricityPrice]:
        """Expand hourly prices to 15-minute intervals if needed."""
        if not prices:
            return []

        minutes_set = {p.timestamp.minute for p in prices}
        is_hourly = len(prices) > 0 and minutes_set == {0}

        if is_hourly:
            expanded_prices = []
            for p in prices:
                base = p.timestamp.replace(minute=0, second=0, microsecond=0)
                for offset in (0, 15, 30, 45):
                    ts = base.replace(minute=offset)
                    expanded_price = models.ElectricityPrice()
                    expanded_price.timestamp = ts
                    expanded_price.price = p.price
                    expanded_prices.append(expanded_price)
            return expanded_prices
        return prices

    @staticmethod
    def convert_to_timezone(prices: List[models.ElectricityPrice], target_tz: str = "UTC") -> List[models.ElectricityPrice]:
        """Convert UTC prices to target timezone."""
        if target_tz == "UTC":
            return prices

        try:
            tz = ZoneInfo(target_tz)

            # Modify the timestamps in place (since we're returning the response, not saving to DB)
            for price in prices:
                # Ensure timestamp is UTC-aware first
                utc_timestamp = price.timestamp
                if utc_timestamp.tzinfo is None:
                    utc_timestamp = utc_timestamp.replace(tzinfo=timezone.utc)

                # Convert to target timezone
                price.timestamp = utc_timestamp.astimezone(tz)

            return prices
        except Exception:
            # If timezone conversion fails, return original prices
            return prices

    @staticmethod
    def ensure_utc(dt: datetime, source_timezone: str = "UTC") -> datetime:
        """
        Ensure datetime is in UTC.

        Args:
            dt: The datetime to convert
            source_timezone: The timezone to assume for naive datetimes
        """
        if dt.tzinfo is None:
            # If naive datetime and source timezone is not UTC, treat as source timezone
            if source_timezone != "UTC":
                try:
                    tz = ZoneInfo(source_timezone)
                    # Make datetime aware in source timezone, then convert to UTC
                    return dt.replace(tzinfo=tz).astimezone(timezone.utc)
                except Exception:
                    # logger.warning("Invalid timezone %s, treating naive datetime as UTC: %s", source_timezone, e)
                    return dt.replace(tzinfo=timezone.utc)
            else:
                # Assume naive datetime is UTC
                return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @staticmethod
    def get_local_day_range_utc(timezone_str: str, days: int = 1):
        """Return (start_utc, end_utc) for the local day(s) based on timezone_str.

        - Start is local 00:00 of 'today' in the given timezone
        - End is inclusive end (23:45) of the last day in the range
        - days=1 => today only; days=2 => today + tomorrow
        """
        try:
            tz = timezone.utc if timezone_str == "UTC" else ZoneInfo(timezone_str)
        except Exception:
            tz = timezone.utc

        current_date_local = datetime.now(tz).date()
        start_of_day_local = datetime.combine(current_date_local, datetime.min.time()).replace(tzinfo=tz)
        end_local = start_of_day_local + timedelta(days=days) - timedelta(minutes=15)

        start_utc = start_of_day_local.astimezone(timezone.utc)
        end_utc = end_local.astimezone(timezone.utc)
        return start_utc, end_utc
