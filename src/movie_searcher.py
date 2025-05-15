"""
Film searching module for retrieving movie information and streaming links.

This module provides functionality to search for films using the Kinopoisk Unofficial API
and to find streaming links through web scraping.
"""

import asyncio
import logging
import os
from typing import Mapping

import aiohttp
from bs4 import BeautifulSoup
from fuzzywuzzy import fuzz


class MovieSearcher:
    """
    A class for searching film information using the Kinopoisk Unofficial API and web scraping.
    """

    BASE_KINOPOISKAPI_URL = (
        "https://kinopoiskapiunofficial.tech/api/v2.1/films/search-by-keyword"
    )
    BASE_SSPOISK_URL = "https://sspoisk.ru/"
    FILMIX_SEARCH_URL = "https://filmix.date/engine/ajax/sphinx_search.php"
    MOVIE_FILMIX_URL = "https://filmix.date/play/"

    def __init__(
        self, kinopoisk_unofficial_api_key=None, links_cap=1, movie_cap=3, logger=None
    ):
        """
        Initializes the FilmSearcher.

        Args:
            kinopoisk_unofficial_api_key (str, optional): API key for Kinopoisk Unofficial API.
            links_cap (int, optional): Maximum number of links to retrieve. Defaults to 1.
            results_cap (int, optional): Maximum number of results to return. Defaults to 3.
            logger (logging.Logger, optional): Logger instance for logging.
        Raises:
            ValueError: If the API key or logger is not provided.
        """
        self.logger = logger
        self.movie_cap = movie_cap
        self.links_cap = links_cap
        self.kinopoisk_unofficial_api_key = kinopoisk_unofficial_api_key
        if not self.kinopoisk_unofficial_api_key:
            raise ValueError(
                "API key is required. Set it as an environment variable or "
                "pass it directly."
            )
        if not self.logger:
            raise ValueError("Logger is required. Pass it directly.")

    async def fetch_movies(self, query: str) -> list[dict]:
        """Searches for movie information by name using the Kinopoisk Unofficial API.

        Args:
            session (aiohttp.ClientSession): The aiohttp client session.
            query (str): The movie name to search for.

        Returns:
            dict: A dictionary containing the search query and the result, or an error message.
        """
        headers = {
            "X-API-KEY": self.kinopoisk_unofficial_api_key,
            "accept": "application/json",
        }
        params: Mapping[str, str | int] = {"keyword": query, "page": 1}

        try:
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=False)
            ) as session, session.get(
                self.BASE_KINOPOISKAPI_URL, headers=headers, params=params
            ) as response:
                data = {}
                if response.status == 429:
                    await asyncio.sleep(0.5)
                    async with session.get(
                        self.BASE_KINOPOISKAPI_URL, headers=headers, params=params
                    ) as retry_response:
                        data = await retry_response.json()
                elif response.status in (401, 403):
                    self.logger.error("Invalid kinopoisk API key")
                elif response.status != 200:
                    self.logger.error(f"API error: HTTP {response.status}")
                else:
                    data = await response.json()

                results = data.get("films", [])
                if not results:
                    return [{"error": "Фильм не найден!"}]

                return results[
                    : self.movie_cap
                ]  # Limit to the number of links specified

        except Exception as e:
            self.logger.exception("Error during movie info fetch.")
            raise e

    async def fetch_movie_links(self, movie: dict) -> list:
        """Finds pirate links to the movie by parsing web pages.

        Args:
            movie (dict): The movie information dictionary.

        Returns:
            list: A list of pirate movie links.
        """
        links = []

        movie_type = "series" if movie.get("serial", False) else "film"
        movie_id = movie.get("filmId", None)
        movie_title = movie.get("nameRu", "")
        movie_year = movie.get("nameRu", movie.get("nameEn", None))

        if movie_id:
            links.append(f"{self.BASE_SSPOISK_URL}{movie_type}/{movie_id}/")

        if len(links) < self.links_cap:
            await self._try_add_filmix_link(links, movie_title, movie_year)

        return links

    async def _try_add_filmix_link(
        self, links: list, movie_title: str, movie_year: str
    ) -> None:
        """Try to find and add a Filmix link for the movie.

        Args:
            links: List to add the link to
            movie_title: Title of the movie
            movie_year: Year of the movie
        """
        payload = (
            f"scf=fx&story={movie_title} {movie_year}"
            f"&search_start=0&do=search&subaction=search&years_ot={movie_year}"
            f"&years_do={movie_year}&kpi_ot=1&kpi_do=10&imdb_ot=1&imdb_do=10"
            "&sort_name=&undefined=asc&sort_date=&sort_favorite=&simple=1"
        )
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
        }

        try:
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=False)
            ) as session, session.post(
                self.FILMIX_SEARCH_URL, data=payload, headers=headers
            ) as response:
                response.raise_for_status()
                html = await response.text()
                self._parse_filmix_response(html, links, movie_title)

        except aiohttp.ClientError as e:
            self.logger.exception("HTTP error while fetching filmix links: %s", repr(e))
        except (ValueError, TypeError, RuntimeError) as e:
            self.logger.exception(
                "Error occurred while fetching filmix links: %s", repr(e)
            )

    def _parse_filmix_response(self, html: str, links: list, movie_title: str) -> None:
        """Parse Filmix HTML response and add matching links to the list.

        Args:
            html: HTML response from Filmix
            links: List to add the link to
            movie_title: Title of the movie to match against
        """
        soup = BeautifulSoup(html, "html.parser")

        for div in soup.find_all("article", class_="shortstory line"):
            movie_id = div.get("data-id")
            if not movie_id:
                continue

            title_element = div.find("h2", class_="name")
            if not title_element:
                continue

            title = title_element.text.strip()
            similarity_ratio = fuzz.ratio(title.lower(), movie_title.lower())

            if similarity_ratio >= 30:
                link = self.MOVIE_FILMIX_URL + movie_id
                links.append(link)
                break


async def main():
    """
    Main function to demonstrate the FilmSearcher functionality.

    Searches for several movies and prints their information and links.
    """
    logging.basicConfig(level=logging.INFO)
    api_key = os.environ.get("KINOPOISK_UNOFFICIAL_API_KEY")
    logger = logging.getLogger(__name__)
    searcher = MovieSearcher(kinopoisk_unofficial_api_key=api_key, logger=logger)

    movies = [
        "Venom",
        "остров собак",
        "магия лунного света",
        "Мстители: война бесконечности",
        "город в котором меня нет",
        "как витька чеснок вез леху штыря в дом инвалидов",
    ]

    for movie_name in movies:
        result = (await searcher.fetch_movies(movie_name))[0]
        if "error" in result:
            print(f"Error for '{movie_name}': {result['error']}")
        else:
            print(
                f"Found '{result['nameRu']}' ({result['year']}) "
                f"with rating {result['rating']} "
                f"poster: {result['posterUrl']}"
            )
            print(result["links"])


if __name__ == "__main__":
    asyncio.run(main())
