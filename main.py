import json
import logging
import re
import time
import traceback
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from os import getenv
from threading import Thread
from typing import NamedTuple, Optional

import praw
import prawcore
import requests
import schedule
import yaml
from deta import Deta
from dotenv import load_dotenv
from praw.models import Comment
from praw.models import Message
from praw.models import Submission

from trello_api import search_multiple_items_blacklist

load_dotenv()


class Platform(NamedTuple):
    platform_type: str
    value: Optional[str]


def create_logger(module_name: str, level: int | str = logging.INFO) -> logging.Logger:
    """
    Creates logger and returns an instance of logging object.
    :param level: The level for logging. (Default: logging.INFO)
    :param module_name: Logger name that will appear in text.
    :return: Logging Object.
    """
    # Setting up the root logger
    logger = logging.getLogger(module_name)
    logger.setLevel(logging.DEBUG)

    log_stream = logging.StreamHandler()
    log_stream.setLevel(level)
    formatter = logging.Formatter('[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s')
    log_stream.setFormatter(formatter)
    logger.addHandler(log_stream)

    file_stream = TimedRotatingFileHandler("./logs/ign_bot.log", when='D', interval=1, backupCount=15)
    file_stream.setLevel(level)
    file_stream.setFormatter(formatter)
    logger.addHandler(file_stream)
    logger.propagate = False
    return logger


def auto_responder():
    reddit_2 = praw.Reddit(client_id=getenv('CLIENT_ID'),
                           client_secret=getenv('CLIENT_SECRET'),
                           username=getenv('REDDIT_USERNAME'),
                           password=getenv('PASSWORD'),
                           user_agent="IGNBot by u/Vault-TecTradingCo")

    my_logger.info("Running the auto_responder")
    for item in reddit_2.inbox.unread(limit=None):
        if isinstance(item, Message):
            item.reply(body="This is a bot account, therefore your messages will not get read by anyone. For any concerns send us modmail "
                            "https://www.reddit.com/message/compose?to=/r/Fallout76Marketplace")
            item.mark_read()


def auto_responder_scheduler():
    schedule.every(30).minutes.do(auto_responder)
    my_logger.info("Scheduled the auto_responder")
    while True:
        schedule.run_pending()
        time.sleep(5)


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


# Send message to discord channel
def send_message_to_discord(message_param, webhook):
    data = {"content": message_param, "username": 'IGNBot'}
    output = requests.post(webhook, data=json.dumps(data), headers={"Content-Type": "application/json"})
    try:
        output.raise_for_status()
    except requests.HTTPError as http_err:
        my_logger.exception(http_err, exc_info=True)


def update_item(data: dict, key: str):
    deta = Deta(getenv('DETA_PROJECT_KEY'))
    fallout_76_db = deta.Base("fallout_76_db")
    fallout_76_db.put(data, key)


# Remove all the stuff posted by unregistered users
def remove_content_from_unregistered_user(reddit_post):
    message_body = \
        f"""## Your submission/comment was removed
        
[Submission/Comment URL](https://www.reddit.com{reddit_post.permalink})

### Why it was removed?

Hi u/{reddit_post.author.name}! It seems that you have not registered your IGN/Gamertag in our system. In order to keep you and the community safe, we 
have made the registration compulsory, if you want to trade here. 

### How to register?

The registration is very easy and will take only a couple of minutes. All you need to do is go to https://fallout76marketplace.com and complete the 
verification process. 

If you have any question. Please send us a [modmail](https://www.reddit.com/message/compose?to=/r/Fallout76Marketplace). 

Thank you for your corporation
r/Fallout76Marketplace
"""
    reddit_post.mod.remove(mod_note='User not registered')
    try:
        reddit_post.author.message(subject='Your submission/comment was removed', message=message_body)
    except Exception:
        my_logger.exception("Can't send message to user", exc_info=True)
        reddit_post.reply(body=message_body)


def set_platform_flair(reddit_post: Submission | Comment, user_info: dict):
    my_logger.info(f"{reddit_post.author_flair_text} {reddit_post.author_flair_template_id}")
    user_flair = reddit_post.author_flair_text or 'Karma: 0'
    flair_template_id = reddit_post.author_flair_template_id or '3c680234-4a4d-11eb-8124-0edd2b620987'
    my_logger.info(f"{user_flair} {flair_template_id}")
    match = re.search(r'xbox|playstation|pc', str(user_flair))
    if match is None:
        if user_info.get("XBOX"):
            user_flair = f":xbox: {user_flair}"
        if user_info.get("PlayStation"):
            user_flair = f":playstation: {user_flair}"
        if user_info.get("Fallout 76"):
            user_flair = f":pc: {user_flair}"
        my_logger.info(f"{user_flair} {flair_template_id}")
        fallout76marketplace.flair.set(reddit_post.author.name, text=user_flair, flair_template_id=flair_template_id)


def check_user_in_blacklist(reddit_post: Submission | Comment, user_data: dict):
    filtered_data: list[Platform] = [Platform("Reddit", user_data['key']),
                                     Platform("PC", user_data.get('Fallout 76')),
                                     Platform("PS4", user_data.get('PlayStation')),
                                     Platform("PS4", user_data.get('PlayStation_ID')),
                                     Platform("XB1", user_data.get('XBOX')),
                                     Platform("XB1", user_data.get('XBOX_ID'))]
    result = search_multiple_items_blacklist([data for data in filtered_data if data.value is not None])
    if result:
        user_data |= {"is_blacklisted": True}
        update_item(user_data, user_data['key'])
        reddit_post.mod.remove(mod_note='User blacklisted')


def check_if_exempted(reddit_username: str):
    wiki = fallout76marketplace.wiki["custom_bot_config/user_verification"]
    yaml_format = yaml.safe_load(wiki.content_md)
    exemption_list = [x.lower() for x in yaml_format['exempted']]
    if reddit_username in exemption_list:
        return True
    else:
        return False


def search_user_in_db(reddit_post: Submission | Comment):
    my_logger.info(f"{reddit_post.author.name} {type(reddit_post)} {reddit_post.id}")
    deta = Deta(getenv('DETA_PROJECT_KEY'))
    fallout_76_db = deta.Base("fallout_76_db")
    fetch_res = fallout_76_db.fetch({"key": reddit_post.author.name.lower()})
    my_logger.info(fetch_res.items)
    if fetch_res.count == 0:
        remove_content_from_unregistered_user(reddit_post)
    else:
        user_data: dict = fetch_res.items[0]
        if user_data.get("is_blacklisted"):
            send_message_to_discord(f"Blacklisted user u/{reddit_post.author.name} tried to post. <https://www.reddit.com{reddit_post.permalink}>",
                                    getenv('USER_VERIFICATION_CHANNEL'))

            reddit_post.mod.remove(mod_note='User blacklisted')
        elif user_data.get("verification_complete"):
            if not check_if_exempted(reddit_post.author.name.lower()):
                check_user_in_blacklist(reddit_post, user_data)
            set_platform_flair(reddit_post, user_data)
        else:
            remove_content_from_unregistered_user(reddit_post)


def main():
    auto_resp_thread = Thread(target=auto_responder_scheduler)
    auto_resp_thread.start()
    # Gets 100 historical comments
    comment_stream = fallout76marketplace.stream.comments(pause_after=-1, skip_existing=True)
    # Gets 100 historical submission
    submission_stream = fallout76marketplace.stream.submissions(pause_after=-1, skip_existing=True)

    my_logger.info(f"Bot is now live! {datetime.now():%I:%M %p %Z}")
    failed_attempt = 1
    while True:
        try:
            # Gets comments and if it receives None, it switches to posts
            for comment in comment_stream:
                if comment is None or comment.author.name == "AutoModerator":
                    break
                search_user_in_db(comment)

            # Gets posts and if it receives None, it switches to comments
            for submission in submission_stream:
                if submission is None:
                    break
                search_user_in_db(submission)

        except Exception as stream_exception:
            send_message_to_discord(traceback.format_exc(), getenv('ERROR_CHANNEL'))
            my_logger.exception("Stream Exception", exc_info=True)
            # In case of server error pause for two minutes
            if isinstance(stream_exception, prawcore.exceptions.ServerError):
                print("Waiting 2 minutes")
                # Try again after a pause
                time.sleep(120 * failed_attempt)
                failed_attempt = failed_attempt + 1
            comment_stream = fallout76marketplace.stream.comments(pause_after=-1, skip_existing=True)
            submission_stream = fallout76marketplace.stream.submissions(pause_after=-1, skip_existing=True)


if __name__ == '__main__':
    reddit = praw.Reddit(client_id=getenv('CLIENT_ID'),
                         client_secret=getenv('CLIENT_SECRET'),
                         username=getenv('REDDIT_USERNAME'),
                         password=getenv('PASSWORD'),
                         user_agent="IGNBot by u/Vault-TecTradingCo")

    my_logger = create_logger(__name__)
    my_logger.info(f"Logged in as u/{reddit.user.me()}")
    fallout76marketplace = reddit.subreddit("Fallout76Marketplace")
    main()
