from flask import Flask, request, jsonify, send_from_directory
import os
import time
import json
import uuid
from datetime import datetime, timezone
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024  # 12 MB per upload

objects = {}
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
PHOTO_INDEX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploaded_photos.json")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "heic"}

def load_photos():
    try:
        with open(PHOTO_INDEX, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []

def save_photos(items):
    temp_path = PHOTO_INDEX + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, PHOTO_INDEX)

@app.route("/")
def home():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "index.html")

@app.route("/photos/<path:filename>")
def photos(filename):
    return send_from_directory(os.path.join(os.path.dirname(os.path.abspath(__file__)), "photos"), filename)

@app.route("/uploads/<path:filename>")
def uploaded_photo(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route("/upload-photo", methods=["POST"])
def upload_photo():
    photo = request.files.get("photo")
    if photo is None or not photo.filename:
        return jsonify({"error": "Please select a photo."}), 400
    original_name = secure_filename(photo.filename)
    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "Unsupported image type. Use JPG, PNG, WEBP, GIF, or HEIC."}), 400
    filename = f"{uuid.uuid4().hex}.{ext}"
    save_path = os.path.join(UPLOAD_FOLDER, filename)
    photo.save(save_path)
    item = {
        "id": uuid.uuid4().hex,
        "filename": filename,
        "original_name": original_name,
        "url": f"/uploads/{filename}",
        "object_id": (request.form.get("object_id") or "unknown")[:40],
        "latitude": request.form.get("latitude") or None,
        "longitude": request.form.get("longitude") or None,
        "accuracy": request.form.get("accuracy") or None,
        "uploaded_at": datetime.now(timezone.utc).isoformat()
    }
    if item["latitude"] is not None:
        try:
            item["latitude"] = float(item["latitude"])
            item["longitude"] = float(item["longitude"])
            if item["accuracy"] is not None:
                item["accuracy"] = float(item["accuracy"])
        except (TypeError, ValueError):
            item["latitude"] = item["longitude"] = item["accuracy"] = None
    items = load_photos()
    items.insert(0, item)
    save_photos(items)
    print("PHOTO UPLOADED:", item["object_id"], item["url"], item["latitude"], item["longitude"])
    return jsonify({"status": "ok", "photo": item}), 201

@app.route("/photos-feed")
def photos_feed():
    return jsonify(load_photos())

@app.route("/update-location", methods=["POST"])
def update_location():
    data = request.get_json(silent=True) or {}
    object_id = data.get("object_id", "A17")
    latitude = data.get("latitude")
    longitude = data.get("longitude")
    accuracy = data.get("accuracy")
    objects[object_id] = {"object_id": object_id, "latitude": latitude, "longitude": longitude, "accuracy": accuracy, "timestamp": time.time()}
    print("GPS RECEIVED:", object_id, latitude, longitude, accuracy)
    return jsonify({"status": "ok", "object_id": object_id})

@app.route("/locations")
def locations():
    return jsonify(objects)

@app.route("/location")
def location():
    if "A17" in objects:
        return jsonify(objects["A17"])
    return jsonify({"object_id": "A17", "latitude": None, "longitude": None, "accuracy": None, "timestamp": None})

@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error": "Photo is too large. Maximum size is 12 MB."}), 413

@app.route("/identify", methods=["POST"])
def identify():
    from google import genai
    from PIL import Image
    import io

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return jsonify({"error": "Gemini API key is not configured."}), 500

    photo = request.files.get("photo")
    if not photo:
        return jsonify({"error": "Please upload a photo."}), 400

    try:
        image = Image.open(io.BytesIO(photo.read()))
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-3.0-flash",
            contents=[
                "Identify the objects in this image like Google Lens. "
                "Give likely names and useful details. State uncertainty; don't guess.",
                image
            ]
        )
        result_text = response.text
        photo_id = request.form.get("photo_id")

        if photo_id:
            items = load_photos()
            for item in items:
                if item.get("id") == photo_id:
                    item["ai_result"] = result_text
                    break
            save_photos(items)

        return jsonify({"result": result_text})
    except Exception as e:
        app.logger.exception("Image identification failed")
        return jsonify({"error": "Image identification failed. Check server logs."}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
