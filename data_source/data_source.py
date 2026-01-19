"""Data Source Abstraction Layer.

This module defines the abstract interface and a factory for creating concrete
data sources.

Concrete implementations live in separate modules:
- JoinQuant: `data_source.joinquant_data_source`
- Supermind: `data_source.supermind_data_source`
- Hikyuu(local): `data_source.hikyuu_data_source`
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import List, Optional, Union

import pandas as pd

logger = logging.getLogger(__name__)


class DataSourceBase(ABC):
    """Abstract base class for data source implementations."""

    @abstractmethod
    def get_security_info(self, security_code: str, date: Optional[str] = None):
        """Get basic information for a security."""

    @abstractmethod
    def get_price(
        self,
        security_code: Union[str, List[str]],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        frequency: str = "day",
        fields: Optional[List[str]] = None,
        **kwargs,
    ) -> pd.DataFrame:
        """Get historical OHLCV data for securities."""

    @abstractmethod
    def get_all_codes(self, security_type: str = "stock", date: Optional[str] = None) -> List[str]:
        """Get all security codes of a specific type."""
    @abstractmethod
    def get_security_industry(self, security_code: str, date: Optional[str] = None) -> str:
        """Get the industry of a security."""
    @abstractmethod
    def get_fund_net_value(
        self,
        security_code: Union[str, List[str]],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        fields: Optional[Union[str, List[str]]] = None,
        **kwargs,
    ) -> pd.DataFrame:
        """Get fund net value data."""

    @abstractmethod
    def get_market_cap(
        self,
        security_code: Union[str, List[str]],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """Get total and circulating market capitalization for securities over a period."""


class DataSourceFactory:
    """Factory class for creating data source instances."""

    _sources = {
        "joinquant": "data_source.joinquant_data_source:JoinQuantDataSource",
        "jq": "data_source.joinquant_data_source:JoinQuantDataSource",
        "supermind": "data_source.supermind_data_source:SupermindDataSource",
        "同花顺": "data_source.supermind_data_source:SupermindDataSource",
        "sm": "data_source.supermind_data_source:SupermindDataSource",
        "hikyuu": "data_source.hikyuu_data_source:HikyuuDataSource",
        "hk": "data_source.hikyuu_data_source:HikyuuDataSource",
    }

    @classmethod
    def create(cls, source_type: str) -> DataSourceBase:
        source_type_lower = source_type.lower()

        if source_type_lower not in cls._sources:
            available = ", ".join(cls._sources.keys())
            raise ValueError(
                f"Unsupported data source: {source_type}. " f"Available sources: {available}"
            )

        module_path, class_name = cls._sources[source_type_lower].split(":")
        module = __import__(module_path, fromlist=[class_name])
        klass = getattr(module, class_name)
        return klass()

    @classmethod
    def register_source(cls, name: str, source_class: type):
        if not issubclass(source_class, DataSourceBase):
            raise ValueError("source_class must inherit from DataSourceBase")

        cls._sources[name.lower()] = f"{source_class.__module__}:{source_class.__name__}"


def get_data_source(source_type: str) -> DataSourceBase:
    """Convenience function to get a data source instance."""

    return DataSourceFactory.create(source_type)
