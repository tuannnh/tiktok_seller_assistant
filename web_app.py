import threading, time, re, csv, os, subprocess, psycopg2, json
from queue import Queue
from flask import Flask, render_template, redirect, url_for, request, flash, session, Response
from TikTokLive import TikTokLiveClient
from TikTokLive.events import CommentEvent, LiveEndEvent
from TikTokLive.client.errors import UserOfflineError

# Label
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

pdfmetrics.registerFont(TTFont('DancingScript', 'static/fonts/DancingScript-Regular.ttf'))

app = Flask(__name__)
app.secret_key = "your_secret_key"  # Replace with your secure key

# Default TikTok Live user ID
DEFAULT_LIVE_USER_ID = "emy_stylee"

# Global TikTok Live user ID (can be changed from settings)
live_user_id = DEFAULT_LIVE_USER_ID

# Global lists for storing data
current_buyers = []  # Each element: {"user_id", "username", "price", "timestamp"}
all_comments = []  # Each element: {"user_id", "username", "comment", "timestamp"}

# Queue for SSE updates for the dashboard (all events trigger a dashboard update)
dashboard_queue = Queue()

# Global variables for live listener and status
live_thread = None
live_running = False
live_status = "not_started"  # Possible values: "not_started", "live", "ended", "offline"

# CSV file location for confirmed (synced) buyers
CSV_FILE = "sales_log.csv"

# Configure the TikTokLive client
client = TikTokLiveClient(unique_id="vtn0203")
# Accept numbers with optional space and optional 'k'
PRICE_PATTERN = re.compile(r'\b\d+\s*[kK]?\b')


# def create_tiktok_client():
#     """ Create a new TikTok Live client with the current user ID """
#     return TikTokLiveClient(unique_id=live_user_id)
#
# client = create_tiktok_client()

@client.on(CommentEvent)
async def on_comment(event: CommentEvent):
    comment_text = event.comment
    print(f"📝 New comment: {comment_text}")
    username = event.user.nickname
    user_id = event.user.unique_id
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    # Build and store comment entry
    comment_entry = {
        "user_id": user_id,
        "username": username,
        "comment": comment_text,
        "timestamp": timestamp
    }
    all_comments.append(comment_entry)
    # Trigger a dashboard update
    dashboard_queue.put("update")

    # If comment contains a valid price, add to current buyers
    match = PRICE_PATTERN.search(comment_text)
    if match:
        price = match.group()
        buyer = {
            "user_id": user_id,
            "username": username,
            "price": price,
            "timestamp": timestamp
        }
        current_buyers.append(buyer)
        print(f"Added buyer: {buyer}")
        dashboard_queue.put("update")


@client.on(LiveEndEvent)
async def on_live_end(event):
    global live_status, live_running
    live_status = "ended"
    live_running = False
    print("⚠️ Live stream ended unexpectedly.")
    dashboard_queue.put("update")


def run_live_listener():
    global live_status, live_running
    try:
        client.run()
    except UserOfflineError as e:
        live_status = "offline"
        live_running = False
        print("⚠️ TikTok LIVE user is offline. Please start a live stream.")
        dashboard_queue.put("update")
    except Exception as e:
        live_status = "ended"
        live_running = False
        print(f"⚠️ An error occurred in the live listener: {e}")
        dashboard_queue.put("update")


def append_to_csv(user_id, username, price):
    with open(CSV_FILE, mode="a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow([user_id, username, price, time.strftime("%Y-%m-%d %H:%M:%S")])


def print_label(user_id, username, price):
    # Create a PDF file with size 50x30mm
    pdf_file = "/tmp/label.pdf"
    c = canvas.Canvas(pdf_file, pagesize=(50 * mm, 30 * mm))
    c.setFont("DancingScript", 15)
    # Calculate center x position (50mm / 2 = 25mm)
    center_x = 25 * mm
    # Define vertical positions (from bottom)
    id_y = 24 * mm  # Top line for user ID
    buyer_y = 18 * mm  # Middle line for buyer
    price_y = 12 * mm  # Bottom line for price
    # Draw centered strings
    c.drawCentredString(center_x, id_y, f"{user_id}")
    c.drawCentredString(center_x, buyer_y, f"{username}")
    c.drawCentredString(center_x, price_y, f"{price}")
    c.showPage()
    c.save()

    # Replace with your actual AirPrint printer name on macOS
    subprocess.run(["lp", "-d", "HPRT_SL43", "-o", "media=Custom.50x30mm", pdf_file])


@app.route('/print_label')
def print_label_page():
    user_id = request.args.get('user_id')
    username = request.args.get('username')
    price = request.args.get('price')
    return render_template("print_label.html", user_id=user_id, username=username, price=price)


def sync_csv_to_postgres():
    conn = psycopg2.connect("dbname=tiktokseller user=ryan password=sup3rh4rdp4ssw0rd host=10.1.1.99")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS liquid_sales_log (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(255),
            username VARCHAR(255),
            price VARCHAR(50),
            timestamp TIMESTAMP
        );
    """)
    conn.commit()
    with open(CSV_FILE, "r", encoding="utf-8") as file:
        reader = csv.reader(file)
        for row in reader:
            if row:
                user_id, username, price, ts = row
                cur.execute(
                    "INSERT INTO liquid_sales_log (user_id, username, price, timestamp) VALUES (%s, %s, %s, %s)",
                    (user_id, username, price, ts))
    conn.commit()
    cur.close()
    conn.close()


def read_csv_log():
    sales = []
    if os.path.exists(CSV_FILE):
        with open(CSV_FILE, "r", encoding="utf-8") as file:
            reader = csv.reader(file)
            sales = list(reader)
    return sales


@app.route('/')
def dashboard():
    sales = read_csv_log()
    return render_template("dashboard.html",
                           live_user_id=session.get("live_user_id", DEFAULT_LIVE_USER_ID),
                           current_buyers=current_buyers,
                           sales=sales,
                           all_comments=all_comments,
                           live_status=live_status)


@app.route('/dashboard_stream')
def dashboard_stream():
    def event_stream():
        while True:
            dashboard_queue.get()  # wait for an update event
            data = {
                "current_buyers": current_buyers,
                "sales": read_csv_log(),
                "all_comments": all_comments,
                "live_status": live_status
            }
            yield f"data: {json.dumps(data)}\n\n"

    return Response(event_stream(), mimetype="text/event-stream")


@app.route('/comments_stream')
def comments_stream():
    # Separate SSE endpoint for the All Comments page.
    def comment_stream():
        last_index = 0
        while True:
            if len(all_comments) > last_index:
                new_comment = all_comments[last_index]
                last_index += 1
                yield f"data: {json.dumps(new_comment)}\n\n"
            time.sleep(1)

    return Response(comment_stream(), mimetype="text/event-stream")


@app.route('/all_comments_page')
def all_comments_page():
    return render_template("all_comments.html", all_comments=all_comments)


@app.route('/start_live', methods=["POST"])
def start_live():
    global live_thread, live_running, live_status, live_user_id, client

    # live_user_id = request.form.get("live_user_id", DEFAULT_LIVE_USER_ID)
    # session["live_user_id"] = live_user_id
    #
    # client = create_tiktok_client()  # Recreate client with new user ID

    if not live_running:
        live_running = True
        live_status = "online"
        live_thread = threading.Thread(target=run_live_listener, daemon=True)
        live_thread.start()
        flash(f"TikTok Live listener started for user: {live_user_id}!")
    else:
        flash("TikTok Live listener is already running!")
    return redirect(url_for("dashboard"))


@app.route('/confirm', methods=["POST"])
def confirm():
    index = int(request.form.get("index"))
    confirmed = None
    if 0 <= index < len(current_buyers):
        confirmed = current_buyers[index]
        user_id = confirmed["user_id"]
        username = confirmed["username"]
        price = confirmed["price"]
        append_to_csv(confirmed["user_id"], confirmed["username"], confirmed["price"])
        current_buyers.clear()  # Clear waiting buyers for next item
        print_label(user_id, username, price)
        flash(f"Confirmed {confirmed['username']} at {confirmed['price']}")
        dashboard_queue.put("update")

    return redirect(url_for("dashboard"))  # Redirect instead of returning JSON


@app.route('/clear_buyers', methods=["POST"])
def clear_buyers():
    current_buyers.clear()
    flash("Cleared current buyers.")
    dashboard_queue.put("update")
    return redirect(url_for("dashboard"))


@app.route('/clear_comments', methods=["POST"])
def clear_comments():
    global all_comments
    all_comments.clear()
    flash("Cleared all comments.")
    dashboard_queue.put("update")
    return redirect(url_for("all_comments_page"))


@app.route('/sync_csv', methods=["POST"])
def sync_csv():
    try:
        sync_csv_to_postgres()
        if os.path.exists(CSV_FILE):
            open(CSV_FILE, "w").close()
        flash("CSV log synced to Postgres successfully and cleared!")
        dashboard_queue.put("update")
    except Exception as e:
        flash(f"Error syncing CSV to Postgres: {str(e)}")
    return redirect(url_for("dashboard"))


if __name__ == '__main__':
    # run on other port
    app.run(debug=True, host="0.0.0.0", port=5001)
