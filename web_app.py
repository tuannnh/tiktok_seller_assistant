import threading, time, re, csv, os, subprocess, psycopg2, json
from queue import Queue
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify, Response
from TikTokLive import TikTokLiveClient
from TikTokLive.events import CommentEvent, LiveEndEvent
from TikTokLive.client.errors import UserOfflineError

app = Flask(__name__)
app.secret_key = "your_secret_key"  # Replace with your secure key

# Global lists for storing data
current_buyers = []   # Each element: {"user_id", "username", "price", "timestamp"}
all_comments = []     # Each element: {"user_id", "username", "comment", "timestamp"}

# Queue for SSE updates for the dashboard (all events trigger a dashboard update)
dashboard_queue = Queue()

# Global variables for live listener and status
live_thread = None
live_running = False
live_status = "not_started"  # Possible values: "not_started", "live", "ended", "offline"

# CSV file location for confirmed (synced) buyers
CSV_FILE = "sales_log.csv"

# Configure the TikTokLive client
client = TikTokLiveClient(unique_id="loanhoanggym")
# Accept numbers with optional space and optional 'k'
PRICE_PATTERN = re.compile(r'\b\d+\s*[kK]?\b')

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

def print_label(username, price):
    label_text = f"Buyer: {username}\nPrice: {price}\n"
    temp_file = "/tmp/label.txt"
    with open(temp_file, "w") as f:
        f.write(label_text)
    # Replace with your actual AirPrint printer name on macOS
    subprocess.run(["lp", "-d", "Your_AirPrint_Printer_Name", temp_file])

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
                cur.execute("INSERT INTO liquid_sales_log (user_id, username, price, timestamp) VALUES (%s, %s, %s, %s)",
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
    global live_thread, live_running, live_status
    if not live_running:
        live_running = True
        live_status = "live"
        live_thread = threading.Thread(target=run_live_listener, daemon=True)
        live_thread.start()
        flash("TikTok Live listener started!")
    else:
        flash("TikTok Live listener is already running!")
    return redirect(url_for("dashboard"))

@app.route('/confirm', methods=["POST"])
def confirm():
    index = int(request.form.get("index"))
    confirmed = None
    if 0 <= index < len(current_buyers):
        confirmed = current_buyers[index]
        append_to_csv(confirmed["user_id"], confirmed["username"], confirmed["price"])
        current_buyers.clear()  # Clear waiting buyers for next item
        flash(f"Confirmed {confirmed['username']} at {confirmed['price']}")
        dashboard_queue.put("update")
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"confirmed": confirmed})
    return redirect(url_for("dashboard"))


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
    app.run(debug=True, host="0.0.0.0")
