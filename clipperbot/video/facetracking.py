import pathlib
import cv2
import numpy as np

# https://github.com/nagadomi/lbpcascade_animeface

root_path = pathlib.Path(__file__).parents[2]
CASCADE_FPATH = "facecascades/lbpcascade_animeface.xml"
CASCADE_FPATH = str(root_path.joinpath(CASCADE_FPATH))

cascade = cv2.CascadeClassifier(CASCADE_FPATH)


class NoFaceException(Exception):
    pass


def facedetect(png_data: bytes, faces_n=1, box_expand=1.5) -> bytes:
    "Returns a png of cropped faces stitched together as bytes."

    png_array = np.frombuffer(png_data, "uint8")
    image = cv2.imdecode(png_array, cv2.IMREAD_COLOR)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # gray = cv2.equalizeHist(gray)

    # faces is a list of (x, y, w, h)
    faces: np.ndarray = cascade.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(72, 72)
    )

    if len(faces) == 0:
        raise NoFaceException

    faces: list = faces.tolist()

    # Only keep the largest n faces
    faces.sort(
        key=lambda face: face[2] * face[3],
        reverse=True
    )
    faces = faces[:faces_n]

    # Expand the boxes
    bigger_faces = []
    for face in faces:
        x, y, w, h = face
        new_w = int(w * box_expand)
        new_h = int(h * box_expand)
        new_x = x - (new_w - w) // 2
        new_y = y - (new_h - h) // 2

        new_w = min(new_w, image.shape[1] - new_x)
        new_h = min(new_h, image.shape[0] - new_y)
        new_x = max(new_x, 0)
        new_y = max(new_y, 0)

        new_face = (new_x, new_y, new_w, new_h)
        bigger_faces.append(new_face)

    faces = bigger_faces

    # Sort based on x-Axis
    faces.sort(key=lambda face: face[0])

    new_w = sum(face[2] for face in faces)
    new_h = max(face[3] for face in faces)

    image = np.concatenate(
        (image, np.ones((image.shape[0], image.shape[1], 1), dtype=np.uint8) * 255),
        axis=2,
        dtype=np.uint8
    )

    result_image = np.zeros((new_h, new_w, 4), dtype=np.uint8)

    # paste all the face together
    current_x = 0
    for face in faces:
        x, y, w, h = face
        result_image[new_h-h : new_h, current_x : current_x+w] = image[y : y+h, x : x+w]
        current_x += w

    cv2.imwrite("tests/out.png", result_image)

    retval, png_data = cv2.imencode(".png", result_image)

    assert retval

    return png_data.tobytes()


if __name__ == "__main__":
    png_data = open("tests/ss_sample.png", "rb").read()
    facedetect(png_data)
