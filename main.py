import datetime
import json
import os
import re
import time
import traceback

import praw
import prawcore
import requests
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
    response = body + "\n\n^(This action was performed by a bot, please contact the mods for any questions. "
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
def submission_checks(submission):
    regex = re.compile(r'(?!.*price\s?check)[\[{(](PC|PS4|XB1)[)}\]]', re.IGNORECASE)
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
def delete_archived_cards(search_result):
    for card in search_result:
        # If Card is archived remove it
        if card.closed:
            search_result.remove(card)
    return search_result


# Searches in trello board using trello api and return the search result in a list\
# The list is empty if there are no search results
def search_in_boards(search_query):
    # escapes the special characters so the search result is exact not from wildcard (e.g '-')
    search_result = trello_client.search(query='name:u/{}'.format(re.escape(search_query)), cards_limit=1,
                                         models=['cards'], board_ids=[user_database.id])
    search_result_escaped_underscore = list()
    # If underscore is in search query, we need to search it escaped and non escaped
    if "_" in search_query:
        search_result_escaped_underscore = trello_client.search(
            query='name:u/{}'.format(re.escape(search_query.replace("_", "\\_"))), cards_limit=1, models=['cards'],
            board_ids=[user_database.id])
    # Adding results from both searches
    search_result = search_result + search_result_escaped_underscore
    # Removing duplicate search results
    search_result = list(set(search_result))
    search_result = delete_archived_cards(search_result)
    return search_result


def comment_user_profile_on_submission(redditor, submission, trello_card):
    table = [
        '|**Reddit username**|**Account Creation Date**|**Email Verified**|**Reddit Karma**|',
        '|:-|:-|:-|:-|', '|u/{}|{}|{}|{}|', '|**{}**|**XBL**|**PSN**|**PC**|', '|{}|{}|{}|{}|',
        '\n**Note: If the the following user is trading with GamerTag that is not listed here. '
        'Please report it to moderators immediately. To get all the bot commands summary just comment'
        ' `!bot commands`**'
    ]
    # Assume that trading karma is 0
    trading_karma = 0
    # If user has a flair we update the karma value otherwise it remains 0
    user_flair = submission.author_flair_text
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

    # Get submission author information
    reddit_karma = redditor.comment_karma + redditor.link_karma
    # converting karma to readable number e,g 10,000 to 10k
    reddit_karma = readable_number(reddit_karma)
    account_created = redditor.created_utc
    # Get when account was created
    date_created = datetime.datetime.fromtimestamp(account_created)
    # get how long ago was the account created
    account_age = account_age_readable_form(account_created)
    # formatting data in nice string
    date = '{} - {}'.format(f'{date_created:%D}', account_age)
    table[2] = table[2].format(redditor.name, date, redditor.has_verified_email, reddit_karma)

    # Getting data from trello card description
    json_data = json.loads(trello_card[0].description)
    set_platform_flair(submission, json_data)
    xbl = 'N/A'
    psn = 'N/A'
    pc = 'N/A'

    # Putting the values in table based off the data from description
    for key, value in json_data.items():
        if key == 'XBL':
            xbl = value[0]
        elif key == 'PSN':
            psn = value[0]
        elif key == 'PC':
            pc = value[0]
    table[4] = table[4].format(trading_karma, xbl, psn, pc)
    comment_body = '\n'.join(table)
    reply(submission, comment_body)


def comment_user_profile(redditor, comment, trello_card):
    table = [
        '|**Platform**|**Username**|', '|:-|:-|', '|Reddit|u/{}|', '|XBL|{}|', '|PSN|{}|', '|PC|{}|',
        '\n^(Note: If the the following user is trading with GamerTag that is not listed here. Please report it '
        'to moderators immediately.)'
    ]
    if len(trello_card) > 0:
        # Getting data from trello card description
        json_data = json.loads(trello_card[0].description)
        xbl = 'N/A'
        psn = 'N/A'
        pc = 'N/A'
        # Putting the values in table based off the data from description
        for key, value in json_data.items():
            if key == 'XBL':
                xbl = value[0]
            elif key == 'PSN':
                psn = value[0]
            elif key == 'PC':
                pc = value[0]
        table[2] = table[2].format(redditor.name)
        table[3] = table[3].format(xbl)
        table[4] = table[4].format(psn)
        table[5] = table[5].format(pc)
        comment_body = '\n'.join(table)
        reply(comment, comment_body)
    else:
        reply(comment, "u/{} has not registered their GamerTag with us. Please take precaution when trading with them."
              .format(redditor.name))


# Remove all the stuff posted by unregistered users
def remove_content_from_unregistered_user(comment_or_submission):
    comment_or_submission.mod.remove(mod_note='User not registered')
    message_body = "## Your submission/comment was removed\n"
    message_body += "[Submission/Comment URL](https://www.reddit.com{})\n".format(comment_or_submission.permalink)
    message_body += "### Why it was removed?\n"
    message_body += "Hi u/{}! It seems that you have not registered your IGN/Gamertag in our system. In order to " \
                    "keep you and the community safe. We decided to make the registration compulsory if you want " \
                    "to trade here.\n\n".format(comment_or_submission.author.name)
    message_body += "### How to register?\n"
    message_body += "The registration is very easy and will take only a couple of minutes. All you need to do is " \
                    "send me (u/Fallout76MktPlBot) a [chat message](https://www.reddit.com/chat/). I shall provide " \
                    "you the instructions from that point on and within a matter of minutes. You will be able to " \
                    "trade on this subreddit.\n"
    message_body += "\nThank you for your corporation!\n\nr/Fallout76Marketplace\n\n"
    message_body += "If you have any question. Please send us a " \
                    "[modmail](https://www.reddit.com/message/compose?to=/r/Fallout76Marketplace). " \
                    "This is a bot account and replies may not get read."
    try:
        comment_or_submission.author.message('Your submission/comment was removed', message_body)
    except Exception as private_message_only:
        print(private_message_only)
        comment_or_submission.reply(message_body)


if __name__ == '__main__':
    # Gets 100 historical comments
    comment_stream = fallout76marketplace.stream.comments(pause_after=-1, skip_existing=True)
    # Gets 100 historical submission
    submission_stream = fallout76marketplace.stream.submissions(pause_after=-1, skip_existing=True)
    failed_attempt = 1

    print("Bot is now live!", time.strftime('%I:%M %p %Z'))
    while True:
        try:
            # Gets comments and if it receives None, it switches to posts
            for comment in comment_stream:
                if comment is None or comment.author.name == "AutoModerator":
                    break
                result = search_in_boards(comment.author.name)
                if len(result) <= 0:
                    remove_content_from_unregistered_user(comment)
                else:
                    match = re.match(r"^(!PROFILE|PROFILE!)", comment.body.strip(), re.IGNORECASE)
                    if match:
                        search = match = re.search(r"u/[^\s\]]+", comment.body.strip(), re.IGNORECASE)
                        if search:
                            result = search_in_boards(search.group().replace('u/', ''))
                            comment_user_profile(reddit.redditor(search.group().replace('u/', '')), comment, result)
                        else:
                            comment_user_profile(comment.author, comment, result)

            # Gets posts and if it receives None, it switches to comments
            for submission in submission_stream:
                if submission is None:
                    break
                result = search_in_boards(submission.author.name)
                if len(result) <= 0:
                    remove_content_from_unregistered_user(submission)
                else:
                    if submission_checks(submission):
                        comment_user_profile_on_submission(submission.author, submission, result)

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
            comment_stream = fallout76marketplace.stream.comments(pause_after=-1, skip_existing=True)
            submission_stream = fallout76marketplace.stream.submissions(pause_after=-1, skip_existing=True)
