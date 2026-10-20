"""Torznab search provider — port of TorznabSearchProvider.kt."""

from __future__ import annotations

from ..models import Category, SearchProviderSafety, Torrent, TorznabConfig
from ..network import NetworkClient
from ..provider import SearchProvider
from ..torznab import (
    TORZNAB_FUNCTION_SEARCH,
    TorznabCategoryMapper,
    TorznabResultsXmlParser,
    normalize_api_url,
)


class TorznabSearchProvider(SearchProvider):
    """A search provider which is based on Torznab specification."""

    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, config: TorznabConfig, network: NetworkClient):
        super().__init__(network)
        self.config = config
        self.id = config.id
        self.name = config.search_provider_name
        self.url = config.url
        self.supported_categories = set(config.supported_categories)
        self._results_xml_parser = TorznabResultsXmlParser(
            provider_id=self.id, provider_name=self.name
        )

    async def search(self, query: str, category: Category) -> list[Torrent]:
        api_url = normalize_api_url(self.config.url)
        request_url = (
            f"{api_url}"
            f"?apikey={self.config.api_key}"
            f"&extended=1"
            f"&t={TORZNAB_FUNCTION_SEARCH}"
            f"&q={query}"
        )
        if category is not Category.ALL:
            categories_id = self._get_categories_id(category)
            request_url += f"&cat={categories_id}"

        response_xml = await self.network.get_text(request_url)
        return self._results_xml_parser.parse(response_xml)

    def _get_categories_id(self, category: Category) -> str | None:
        if category is Category.ALL or category not in self.config.supported_categories:
            return None
        return TorznabCategoryMapper.get_cat_param_value(category)
