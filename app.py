from flask import Flask, request, jsonify, send_from_directory
import os

app = Flask(__name__)

latest_location = {
    "latitude": None,
    "longitude": None,
    "accuracy": None
}


@app.route("/")
def home():
    return send_from_directory(".", "index.html")


@app.route("/photos/<path:filename>")
def photos(filename):
    return send_from_directory("photos", filename)


@app.route("/update-location", methods=["POST"])
def update_location():
    global latest_location

    data = request.get_json(silent=True) or {}

    latest_location = {
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "accuracy": data.get("accuracy")
    }

    print("GPS RECEIVED:", latest_location)

    return jsonify({"status": "ok"})


@app.route("/location")
def location():
    return jsonify(latest_location)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)