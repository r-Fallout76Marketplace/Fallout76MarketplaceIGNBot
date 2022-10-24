import re
from os import getenv
from typing import TYPE_CHECKING

from trello import TrelloClient, Card

if TYPE_CHECKING:
    from main import Platform

REGEX_REMOVE_PARENTHESIS = re.compile(r"\(.+\)")
REGEX_MATCH_FIELD_CONTENT = re.compile(r"(?<=:).+$", re.MULTILINE)


def is_in_description(desc: str, search_query: str):
    """
    Makes sure that the search query is really present in the description since trello can return partial matches

    :param desc: card description
    :param search_query: search query input
    :return: True if search query exists in card description otherwise false
    """
    new_desc = re.sub(REGEX_REMOVE_PARENTHESIS, "", desc).splitlines()
    all_lines = [line for line in new_desc if line]  # Removes all empty lines
    all_lines = map(lambda element: element.strip().replace("\\", "").replace("u/", "").lower(), all_lines)
    for line in all_lines:
        # If line has a colon we are only interested the content after it. E.g. XBL: Test
        if ':' in line:
            line = re.search(REGEX_MATCH_FIELD_CONTENT, line).group().strip()
        if search_query.lower() == line:
            return True
    return False


def filter_search_result(search_result: list[Card], search_query: "Platform") -> list[Card]:
    """
    Filters the cards that are archived, don't have the label scammer, and if query doesn't appear in the description
    :param search_result:
    :param search_query:
    :return:
    """
    for card in search_result.copy():
        # closed means the card is archived
        if card.closed:
            search_result.remove(card)

        # Remove cards that are from other platforms
        if search_query.platform_type != "Reddit":
            if search_query.platform_type not in card.name.upper():
                search_result.remove(card)

        if not is_in_description(card.desc, search_query.value):
            search_result.remove(card)

        for label in card.labels:
            if label.name.lower() == 'scamming':
                break
        else:
            search_result.remove(card)
    return search_result


def search_in_blacklist(search_query: "Platform") -> list[Card]:
    """
    Searches in Market76 Blacklist and Fallout76Marketplace Blacklist for the search query.

    :param search_query: Search Query
    :return: List of cards containing that query
    """
    trello_client = TrelloClient(
        api_key=getenv('TRELLO_API_KEY'),
        api_secret=getenv('TRELLO_TOKEN'),
    )
    m76_board = trello_client.get_board("0eCDKYHr")
    fo76_board = trello_client.get_board("8oCsXC2j")
    search_result = trello_client.search(query=search_query.value, board_ids=[m76_board.id, fo76_board.id], models=['cards'], cards_limit=1000)
    search_result = filter_search_result(search_result=search_result, search_query=search_query)
    return search_result


def search_multiple_items_blacklist(search_queries: list["Platform"]) -> bool:
    """
    Checks if any item provided in search queries exist in blacklist

    :param search_queries: List of queries
    :return: True if even one query exists else false
    """
    result = []
    for query in search_queries:
        cards = search_in_blacklist(query)
        result.extend(cards)
    return bool(result)
