import json
import re
import time
import traceback
from os import getenv

import praw
import prawcore
import requests
from deta import Deta
from dotenv import load_dotenv
from praw.models import Comment
from praw.models import Submission

load_dotenv()


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


def set_platform_flair(reddit_post: Submission | Comment, user_info: dict):
    user_flair = reddit_post.author_flair_text or 'Karma: 0'
    flair_template_id = reddit_post.author_flair_template_id or '3c680234-4a4d-11eb-8124-0edd2b620987'
    match = re.search(r'xbox|playstation|pc', str(user_flair))
    if match is None:
        if user_info.get("XBOX"):
            user_flair = f":xbox: {user_flair}"
        if user_info.get("PlayStation"):
            user_flair = f":playstation: {user_flair}"
        if user_info.get("Fallout 76"):
            user_flair = f":pc: {user_flair}"
        fallout76marketplace.flair.set(reddit_post.author.name, text=user_flair, flair_template_id=flair_template_id)


# Send message to discord channel
def send_message_to_discord(message_param, webhook):
    data = {"content": message_param, "username": 'IGNBot'}
    output = requests.post(webhook, data=json.dumps(data), headers={"Content-Type": "application/json"})
    try:
        output.raise_for_status()
    except requests.HTTPError as http_err:
        print(http_err)


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
        reddit_post.author.message('Your submission/comment was removed', message_body)
    except Exception as private_message_only:
        print(private_message_only)
        reddit_post.reply(message_body)


def search_user_in_db(reddit_post: Submission | Comment):
    deta = Deta(getenv('DETA_PROJECT_KEY'))
    fallout_76_db = deta.Base("fallout_76_db")
    fetch_res = fallout_76_db.fetch({"key": reddit_post.author.name.lower()})
    if fetch_res.count == 0:
        remove_content_from_unregistered_user(reddit_post)
    else:
        user_info: dict = fetch_res.items[0]
        if user_info.get("is_blacklisted"):
            send_message_to_discord(f"Blacklisted user u/{reddit_post.author.name} tried to post.", getenv('MOD_CHANNEL'))
            reddit_post.mod.remove(mod_note='User blacklisted')
        else:
            set_platform_flair(reddit_post, user_info)


def main():
    # Gets 100 historical comments
    comment_stream = fallout76marketplace.stream.comments(pause_after=-1, skip_existing=True)
    # Gets 100 historical submission
    submission_stream = fallout76marketplace.stream.submissions(pause_after=-1, skip_existing=True)

    print("Bot is now live!", time.strftime('%I:%M %p %Z'))
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
            tb = traceback.format_exc()
            send_message_to_discord(tb, getenv('ERROR_CHANNEL'))
            print(tb)
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
    print(f"Logged in as u/{reddit.user.me()}")
    fallout76marketplace = reddit.subreddit("Fallout76Marketplace")
    main()
