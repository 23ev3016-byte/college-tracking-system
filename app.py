
from flask import Flask, request, jsonify, send_from_directory
import os
import time
import json
import uuid
import io
from datetime import datetime, timezone
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024

objects = {}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
PHOTO_INDEX = os.path.join(BASE_DIR, "uploaded_photos.json")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "heic"}

# Load YOLO only when first needed.
yolo_model = None
yolo_load_error = None


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


def save_ai_result(photo_id, result_text, detections=None):
    if not photo_id:
        return

    items = load_photos()
    for item in items:
        if item.get("id") == photo_id:
            item["ai_result"] = result_text
            if detections is not None:
                item["detections"] = detections
            break

    save_photos(items)


def get_yolo_model():
    global yolo_model, yolo_load_error

    if yolo_model is not None:
        return yolo_model

    if yolo_load_error:
        raise RuntimeError(yolo_load_error)

    try:
        from ultralytics import YOLO
        model_path = os.environ.get("YOLO_MODEL", "yolo11n.pt")
        yolo_model = YOLO(model_path)
        return yolo_model
    except Exception as e:
        yolo_load_error = str(e)
        raise RuntimeError("YOLO model could not be loaded.") from e


def detect_objects(image):
    model = get_yolo_model()
    results = model.predict(source=image, conf=0.25, verbose=False)

    detections = []

    for result in results:
        for box in result.boxes:
            class_id = int(box.cls[0])
            confidence = float(box.conf[0])
            label = model.names[class_id]

            coords = box.xyxy[0].tolist()

            detections.append({
                "label": str(label),
                "confidence": round(confidence, 4),
                "box": [round(float(v), 2) for v in coords]
            })

    return detections


def describe_detections(detections):
    if not detections:
        return "YOLO did not detect any supported objects in this photo."

    lines = ["YOLO object detection:"]
    for item in detections:
        lines.append(
            f"- {item['label']}: "
            f"{item['confidence'] * 100:.1f}% confidence"
        )
    return "\n".join(lines)


def identify_with_gemini(image):
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")

    client = genai.Client(api_key=api_key)

    last_error = None

    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"),
                contents=[
                    (
                        "Identify the visible objects in this image. "
                        "Give likely names and useful details. "
                        "State uncertainty and do not guess. "
                        "Do not claim a geographic location unless "
                        "the image provides reliable evidence."
                    ),
                    image
                ]
            )

            result_text = response.text
            if not result_text:
                raise RuntimeError("Gemini returned an empty response.")

            return result_text

        except Exception as e:
            last_error = e
            message = str(e).lower()

            # Retry temporary overload and rate-limit errors only.
            temporary = (
                "503" in message
                or "429" in message
                or "unavailable" in message
                or "resource_exhausted" in message
                or "high demand" in message
            )

            if not temporary or attempt == 2:
                break

            time.sleep(2 ** attempt)

    raise RuntimeError(f"Gemini identification failed: {last_error}")


@app.route("/")
def home():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/photos/<path:filename>")
def photos(filename):
    return send_from_directory(os.path.join(BASE_DIR, "photos"), filename)


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
        return jsonify({
            "error": "Unsupported image type. Use JPG, PNG, WEBP, GIF, or HEIC."
        }), 400

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
            item["latitude"] = None
            item["longitude"] = None
            item["accuracy"] = None

    items = load_photos()
    items.insert(0, item)
    save_photos(items)

    print(
        "PHOTO UPLOADED:",
        item["object_id"],
        item["url"],
        item["latitude"],
        item["longitude"]
    )

    return jsonify({"status": "ok", "photo": item}), 201


@app.route("/photos-feed")
def photos_feed():
    return jsonify(load_photos())


@app.route("/update-location", methods=["POST"])
def update_location():
    data = request.get_json(silent=True) or {}

    object_id = str(data.get("object_id", "A17"))[:40]
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

    print("GPS RECEIVED:", object_id, latitude, longitude, accuracy)

    return jsonify({"status": "ok", "object_id": object_id})


@app.route("/locations")
def locations():
    return jsonify(objects)


@app.route("/location")
def location():
    if "A17" in objects:
        return jsonify(objects["A17"])

    return jsonify({
        "object_id": "A17",
        "latitude": None,
        "longitude": None,
        "accuracy": None,
        "timestamp": None
    })


@app.route("/identify", methods=["POST"])
def identify():
    from PIL import Image, UnidentifiedImageError

    photo = request.files.get("photo")

    if not photo:
        return jsonify({"error": "Please upload a photo."}), 400

    try:
        image_bytes = photo.read()
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
        image = image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        return jsonify({
            "error": "Could not read this image. Please use JPG, PNG, or WEBP."
        }), 400

    photo_id = request.form.get("photo_id")
    errors = []

    # YOLO detection
    detections = []
    try:
        detections = detect_objects(image)
        print("YOLO DETECTIONS:", detections)
    except Exception as e:
        app.logger.exception("YOLO detection failed")
        errors.append("YOLO: " + str(e))

    # Gemini description; retry temporary errors.
    gemini_text = ""
    try:
        gemini_text = identify_with_gemini(image)
    except Exception as e:
        app.logger.warning("Gemini unavailable: %s", e)
        errors.append("Gemini: " + str(e))

    # If Gemini is unavailable, still return successful YOLO results.
    yolo_text = describe_detections(detections)

    if gemini_text and detections:
        result_text = gemini_text + "\n\n" + yolo_text
    elif gemini_text:
        result_text = gemini_text
    elif detections:
        result_text = (
            yolo_text
            + "\n\nGemini description is temporarily unavailable."
        )
    else:
        app.logger.error("Both image identification methods failed: %s", errors)
        return jsonify({
            "error": "Both Gemini and YOLO failed.",
            "details": errors
        }), 500

    save_ai_result(photo_id, result_text, detections)

    return jsonify({
        "result": result_text,
        "detections": detections,
        "gemini_available": bool(gemini_text)
    })


@app.errorhandler(413)
def too_large(_error):
    return jsonify({
        "error": "Photo is too large. Maximum size is 12 MB."
    }), 413


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
