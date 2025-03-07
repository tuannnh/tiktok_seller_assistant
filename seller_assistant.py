from TikTokLive import TikTokLiveClient
from TikTokLive.events import CommentEvent
import re
import time

# Global list to store current buyers (to be shared with the Flask app)
current_buyers = []  # Each element is a dict with user_id, username, price, timestamp

# Replace with your TikTok username
client = TikTokLiveClient(unique_id="hong.anh.thanh.l")

# Pattern to match price (e.g., "30" or "30k")
PRICE_PATTERN = re.compile(r'\b\d+\s*[kK]?\b')

@client.on(CommentEvent)
async def on_comment(event: CommentEvent):
    comment_text = event.comment
    print(f"📝 New comment: {comment_text}")
    username = event.user.nickname
    user_id = event.user.unique_id

    match = PRICE_PATTERN.search(comment_text)
    if match:
        price = match.group()
        buyer = {
            "user_id": user_id,
            "username": username,
            "price": price,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        current_buyers.append(buyer)
        print(f"Added buyer: {buyer}")

client.run()
