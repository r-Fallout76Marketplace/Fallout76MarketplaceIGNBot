import datetime
import json
import os
import re
import time
import traceback

import praw
import prawcore
import requests
import trello
from dotenv import load_dotenv
from trello import TrelloClient

load_dotenv()
reddit = praw.Reddit('UserVerificationBot')
fallout76marketplace = reddit.subreddit("Fallout76Marketplace")
trello_api_key = os.getenv('TRELLO_API_KEY')
trello_api_secret = os.getenv('TRELLO_API_SECRET')
trello_token = os.getenv('TRELLO_TOKEN')

trello_client = TrelloClient(
    api_key=trello_api_key,
    api_secret=trello_api_secret,
    token=trello_token
)

boards = trello_client.list_boards()
blacklist_board = boards[0]
user_database = boards[1]


def reply(comment_or_submission, body):
    # Add disclaimer text
    response = body + "\n\n ^(This action was performed by a bot, please contact the mods for any questions. "
    response += "[See disclaimer](https://www.reddit.com/user/Vault-TecTradingCo/comments/lkllre" \
                "/disclaimer_for_rfallout76marketplace/)) "
    try:
        new_comment = comment_or_submission.reply(response)
        new_comment.mod.distinguish(how="yes", sticky=True)
        new_comment.mod.lock()
    except prawcore.exceptions.Forbidden:
        raise prawcore.exceptions.Forbidden("Could not distinguish/lock comment")


def readable_number(num):
    for unit in ['', 'k', 'm', 'g']:
        if abs(num) < 1000:
            return "{}{}".format(round(num, 2), unit)
        num /= 1000
    return "{}{}".format(round(num, 2), 't')


# Checks if submission is eligible for trading
# Checks that need to be passed are
# Submission must have right flair and trade should not be closed
def flair_checks(submission):
    regex = re.compile(r'[\[\{\(](PC|PS4|XB1)[\)\}\]]', re.IGNORECASE)
    match = re.match(regex, submission.title)
    # If No match found match is None
    if match is None:
        return False
    else:
        return True


def add_emoji(redditor, user_flair, flair_template_id, emoji):
    user_flair_split = user_flair.split()
    for i in range(len(user_flair_split)):
        if 'Karma' in user_flair_split[i]:
            user_flair_split.insert(i, emoji)
            break
    user_flair = ' '.join(user_flair_split)
    fallout76marketplace.flair.set(redditor.name, text=user_flair, flair_template_id=flair_template_id)
    return user_flair


def set_platform_flair(submission, json_data):
    user_flair = submission.author_flair_text
    regex = re.compile('xbox|playstation|pc', re.IGNORECASE)
    if user_flair is not None and user_flair != '':
        match = re.search(regex, str(user_flair))
        if match:
            return None
        else:
            flair_template_id = submission.author_flair_template_id
            if 'XBL' in json_data.keys():
                user_flair = add_emoji(submission.author, user_flair, flair_template_id, ':xbox:')
            if 'PSN' in json_data.keys():
                user_flair = add_emoji(submission.author, user_flair, flair_template_id, ':playstation:')
            if 'PC' in json_data.keys():
                add_emoji(submission.author, user_flair, flair_template_id, ':pc:')
    else:
        user_flair = 'Karma: 0'
        flair_template_id = '3c680234-4a4d-11eb-8124-0edd2b620987'
        if 'XBL' in json_data.keys():
            user_flair = add_emoji(submission.author, user_flair, flair_template_id, ':xbox:')
        if 'PSN' in json_data.keys():
            user_flair = add_emoji(submission.author, user_flair, flair_template_id, ':playstation:')
        if 'PC' in json_data.keys():
            add_emoji(submission.author, user_flair, flair_template_id, ':pc:')
    return None


def account_age_readable_form(account_created):
    difference = time.time() - account_created
    if difference < (60 * 60 * 24):
        return 'Today'
    elif (60 * 60 * 24) <= difference < (60 * 60 * 24 * 30):
        return str(round(difference / (60 * 60 * 24), 1)) + ' days ago'
    elif (60 * 60 * 24 * 30) <= difference < (60 * 60 * 24 * 30 * 12):
        return str(round(difference / (60 * 60 * 24 * 30), 1)) + ' months ago'
    else:
        return str(round(difference / (60 * 60 * 24 * 30 * 12), 1)) + ' years ago'


# Send message to discord channel
def send_message_to_discord(message_param):
    webhook = os.getenv('ERROR_CHANNEL')
    data = {"content": message_param, "username": 'IGNBot'}
    output = requests.post(webhook, data=json.dumps(data), headers={"Content-Type": "application/json"})
    output.raise_for_status()


# Removes the archived cards from list
def delete_archived_cards_and_check_desc(search_result, search_query):
    for card in search_result:
        # Some search query returns the boards and the members which creates issue later
        if not isinstance(card, trello.Card):
            search_result.remove(card)
            continue
        # closed means the card is archived
        if card.closed:
            search_result.remove(card)
        # Double check to make sure that search query is in card description
        if search_query.lower() not in card.description.lower().replace('\\', ''):
            search_result.remove(card)
    return search_result


# Searches in trello board using trello api and return the search result in a list\
# The list is empty if there are no search results
def search_in_boards(search_query):
    search_result = list()
    try:
        # escapes the special characters so the search result is exact not from wildcard (e.g '-')
        search_result = trello_client.search(query=re.escape(search_query), cards_limit=1)
        search_result_escaped_underscore = list()
        # If underscore is in search query, we need to search it escaped and non escaped
        if "_" in search_query:
            search_result_escaped_underscore = trello_client.search(
                query=re.escape(search_query.replace("_", "\\_")), cards_limit=1)
        # Adding results from both searches
        search_result = search_result + search_result_escaped_underscore
        # Removing duplicate search results
        search_result = list(set(search_result))
        search_result = delete_archived_cards_and_check_desc(search_result, search_query)
    except NotImplementedError:
        raise NotImplementedError(search_query)
    return search_result


submission_stream = fallout76marketplace.stream.submissions(skip_existing=True)
failed_attempt = 1

print("Bot is now live!", time.strftime('%I:%M %p %Z'))
while True:
    try:
        for submission in submission_stream:
            if not flair_checks(submission):
                continue
            table = ['|**Reddit username**|**Account Creation Date**|**Email Verified**|**Reddit Karma**|',
                     '|:-|:-|:-|:-|', '|u/{}|{}|{}|{}|', '|**{}**|**XBL**|**PSN**|**PC**|', '|{}|{}|{}|{}|',
                     '\n**Note: If the the following user is trading with GamerTag that is not listed here. '
                     'Please report it to moderators immediately. To get all the bot commands summary just comment'
                     ' `!bot commands`**']
            author = submission.author
            reddit_karma = author.comment_karma + author.link_karma
            reddit_karma = readable_number(reddit_karma)
            account_created = author.created_utc
            # Get when account was created
            date_created = datetime.datetime.fromtimestamp(account_created)
            # get how long ago was the account created
            account_age = account_age_readable_form(account_created)
            # formatting data in nice string
            date = '{} - {}'.format(f'{date_created:%D}', account_age)
            table[2] = table[2].format(author.name, date, author.has_verified_email, reddit_karma)
            user_flair = submission.author_flair_text
            trading_karma = 0
            # Only if user has a flair we check for trading karma value
            if user_flair is not None and user_flair != '':
                user_flair_split = user_flair.split()
                trading_karma = user_flair_split[-1]
                # For regular users the table says karma otherwise it will say courier, or bot etc...
                if 'Karma' in user_flair_split[-2]:
                    table[3] = table[3].format('Trading Karma')
                else:
                    table[3] = table[3].format(user_flair_split[-2].replace(':', ''))
            else:
                table[3] = table[3].format('Trading Karma')
            # Check if the user is registered
            result = search_in_boards(submission.author.name)
            if len(result) > 0:
                if result[0].board == user_database:
                    json_data = json.loads(result[0].description)
                    set_platform_flair(submission, json_data)
                    xbl = 'N/A'
                    psn = 'N/A'
                    pc = 'N/A'
                    for key, value in json_data.items():
                        if key == 'XBL':
                            xbl = value[0]
                        elif key == 'PSN':
                            psn = value[0]
                        elif key == 'PC':
                            pc = value[0]
                    table[4] = table[4].format(trading_karma, xbl, psn, pc)
                comment_body = '\n'.join(table)
            else:
                submission.mod.remove(mod_note='User not registered')
                comment_body = "Hi u/{}! It seems that you have not registered your IGN/Gamertag in our system. In " \
                               "order to keep you and the community safe. We decided to make the registration " \
                               "compulsory. It only take a couple of minutes to register. All you need to do is send " \
                               "me a chat message. I shall provide you the instructions from that point on and within" \
                               " a matter of minutes. You will be able to trade on this subreddit. Thank you for your" \
                               " corporation!".format(submission.author.name)
            reply(submission, comment_body)
    except Exception as stream_exception:
        tb = traceback.format_exc()
        try:
            send_message_to_discord(tb)
            print(tb)
            # Refreshing Streams
        except Exception as discord_exception:
            print("Error sending message to discord", str(discord_exception))

        # In case of server error pause for two minutes
        if isinstance(stream_exception, prawcore.exceptions.ServerError):
            print("Waiting 2 minutes")
            # Try again after a pause
            time.sleep(120 * failed_attempt)
            failed_attempt = failed_attempt + 1
        submission_stream = fallout76marketplace.stream.submissions(skip_existing=True)
