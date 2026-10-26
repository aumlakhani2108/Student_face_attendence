import os
import sqlite3

import cv2
import numpy as np

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR") or APP_DIR
os.makedirs(DATA_DIR, exist_ok=True)
MODEL_PATH = os.path.join(DATA_DIR, "face_embeddings.npz")
MODEL_DIR = os.path.join(APP_DIR, "models")
DETECTOR_PATH = os.path.join(MODEL_DIR, "face_detection_yunet_2023mar.onnx")
RECOGNIZER_PATH = os.path.join(MODEL_DIR, "face_recognition_sface_2021dec.onnx")


def create_face_models():
    missing = [path for path in (DETECTOR_PATH, RECOGNIZER_PATH) if not os.path.isfile(path)]
    if missing:
        raise FileNotFoundError("Face models are missing. Run `python download_models.py` first.")
    detector = cv2.FaceDetectorYN.create(
        DETECTOR_PATH,
        "",
        (320, 320),
        score_threshold=0.8,
        nms_threshold=0.3,
        top_k=5000,
    )
    recognizer = cv2.FaceRecognizerSF.create(RECOGNIZER_PATH, "")
    return detector, recognizer


def embedding_from_image(image, detector, recognizer):
    if image is None or image.size == 0:
        return None
    height, width = image.shape[:2]
    detector.setInputSize((width, height))
    _, faces = detector.detect(image)
    if faces is None or len(faces) == 0:
        return None
    face = max(faces, key=lambda detection: detection[2] * detection[3])
    aligned = recognizer.alignCrop(image, face)
    feature = recognizer.feature(aligned).reshape(-1).astype(np.float32)
    norm = np.linalg.norm(feature)
    if norm == 0:
        return None
    return feature / norm


def decode_image_bytes(data):
    if not data:
        return None
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def extract_embedding_for_image(stream_or_bytes):
    data = stream_or_bytes.read() if hasattr(stream_or_bytes, "read") else stream_or_bytes
    image = decode_image_bytes(data)
    if image is None:
        return None
    detector, recognizer = create_face_models()
    return embedding_from_image(image, detector, recognizer)


def load_model_if_exists():
    if not os.path.isfile(MODEL_PATH):
        return None
    with np.load(MODEL_PATH, allow_pickle=False) as saved:
        labels = saved["labels"].astype(np.int64)
        embeddings = saved["embeddings"].astype(np.float32)
    if len(labels) == 0 or embeddings.ndim != 2 or len(labels) != len(embeddings):
        raise ValueError("Saved face embedding model is invalid.")
    return {"labels": labels, "embeddings": embeddings}


def predict_with_model(model, embedding):
    query = np.asarray(embedding, dtype=np.float32).reshape(-1)
    if query.size != model["embeddings"].shape[1]:
        raise ValueError("Face embedding has an unexpected size.")
    similarities = model["embeddings"] @ query
    best_index = int(np.argmax(similarities))
    return int(model["labels"][best_index]), float(similarities[best_index])


def train_model_background(database_path, progress_callback=None):
    detector, recognizer = create_face_models()
    with sqlite3.connect(database_path) as conn:
        student_ids = [row[0] for row in conn.execute(
            "SELECT DISTINCT student_id FROM student_faces ORDER BY student_id"
        )]
        if not student_ids:
            raise ValueError("Add a student and capture face images before training.")

        grouped_embeddings = {}
        total_students = len(student_ids)
        processed_images = 0
        skipped_images = 0

        for student_index, student_id in enumerate(student_ids, start=1):
            face_rows = conn.execute(
                "SELECT image FROM student_faces WHERE student_id = ? ORDER BY id", (student_id,)
            )
            for (image_bytes,) in face_rows:
                image = decode_image_bytes(image_bytes)
                embedding = embedding_from_image(image, detector, recognizer)
                if embedding is None:
                    skipped_images += 1
                    continue
                grouped_embeddings.setdefault(int(student_id), []).append(embedding)
                processed_images += 1
            if progress_callback:
                progress = int(student_index / total_students * 80)
                progress_callback(progress, f"Processed {student_index}/{total_students} students.")

    if not grouped_embeddings:
        raise ValueError("No usable faces found in the captured images.")

    labels = []
    embeddings = []
    for student_id, samples in grouped_embeddings.items():
        centroid = np.mean(np.stack(samples), axis=0)
        centroid_norm = np.linalg.norm(centroid)
        if centroid_norm == 0:
            continue
        labels.append(student_id)
        embeddings.append(centroid / centroid_norm)
    if not embeddings:
        raise ValueError("Could not create valid face embeddings.")

    if progress_callback:
        progress_callback(90, f"Built embeddings from {processed_images} images.")
    temp_path = MODEL_PATH + ".tmp.npz"
    np.savez_compressed(
        temp_path,
        labels=np.asarray(labels, dtype=np.int64),
        embeddings=np.asarray(embeddings, dtype=np.float32),
    )
    os.replace(temp_path, MODEL_PATH)
    if progress_callback:
        message = f"Training complete. Used {processed_images} images; skipped {skipped_images}."
        progress_callback(100, message)
