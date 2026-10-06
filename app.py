from flask import Flask, request, jsonify, send_from_directory
import os
import time

app = Flask(__name__)

# Store multiple anonymous objects
objects = {}


@app.route("/")
def home():
    return send_from_directory(".", "index.html")


@app.route("/photos/<path:filename>")
def photos(filename):
    return send_from_directory("photos", filename)


@app.route("/update-location", methods=["POST"])
def update_location():

    data = request.get_json(silent=True) or {}

    object_id = data.get("object_id", "A17")
    latitude = data.get("latitude")
    longitude = data.get("longitude")
    accuracy = data.get("accuracy")

    objects[object_id] = {

        "object_id": object_id,

        "latitude": latitude,

        "longitude": longitude,

        "accuracy": accuracy,

        "timestamp": time.time()

    }

    print(
        "GPS RECEIVED:",
        object_id,
        latitude,
        longitude,
        accuracy
    )

    return jsonify({

        "status": "ok",

        "object_id": object_id

    })


@app.route("/locations")
def locations():

    return jsonify(objects)


@app.route("/location")
def location():

    if "A17" in objects:

        return jsonify(
            objects["A17"]
        )

    return jsonify({

        "object_id": "A17",

        "latitude": None,

        "longitude": None,

        "accuracy": None,

        "timestamp": None

    })


if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(

        host="0.0.0.0",

        port=port

    )
