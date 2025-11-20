import mediapipe as mp
mp_pose = mp.solutions.pose
pose = mp_pose.Pose(static_image_mode=False, model_complexity=1, enable_segmentation=False, min_detection_confidence=0.5)

connections = [
    (11, 13), (13, 15),
    (12, 14), (14, 16),
    (11, 12),
    (23, 24),
    (11, 23), (12, 24),
    (23, 25), (25, 27),
    (24, 26), (26, 28),
]


"""""
Experiment: fast YOLOv8n person detector with threaded camera.
Not part of core GaitGuard pipeline – used only for benchmarking.
"""
# fast live "person" detector (nano, FP16, low latency)
import cv2, time                  # camera + timing 
import threading, queue           # run camera capture in background thread
from ultralytics import YOLO      # YOLOv8 model
import torch                      # check CUDA and put model on GPU

 
# OPEN CAMERA SETUP
# Opens webcam with ID index (0 = default camera).
# Sets width, height, fps, MJPEG mode and buffer size
# (number of frames to keep in memory).
def open_camera(index=0, w=640, h=480, fps=30, mjpeg=True, buffersize=1):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    # If CAP_DSHOW ever fails, we could try cv2.CAP_MSMF instead.

    if mjpeg:
        # Force camera to send frames in MJPEG format (usually lower latency).
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))

    # Setting camera resolution and FPS targets (640x480 @ 30 fps).
    # 640x480 is a good balance between speed and enough detail.
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    cap.set(cv2.CAP_PROP_FPS, fps)

    # buffersize = 1 means OpenCV keeps only recent frames in memory,
    # reducing latency.
    cap.set(cv2.CAP_PROP_BUFFERSIZE, buffersize)

    # Returns the configured VideoCapture object.
    return cap


# CameraSource – threaded, latest-frame-only reader
class CameraSource:
    # Wraps the camera in a class to manage it more cleanly.
    def __init__(self, cam_index=0, **kw):
        self.cap = open_camera(cam_index, **kw)
        if not self.cap.isOpened():
            raise RuntimeError("Camera not available")

        # Queue with maxsize=1 → we always keep only the most recent frame
        # from the background thread. This is memory-efficient and low latency.
        self.q = queue.Queue(maxsize=1)

        # Flag to control capture loop; when running=True we capture frames.
        self.running = False

    # Producer runs in a background thread to capture frames.
    # Reads frames continuously from camera.
    # If frame read fails → SKIP IT.
    # If queue already has a frame, remove it before putting the new one
    # (keeps only the most recent frame).
    def _producer(self):
        while self.running:
            ok, frame = self.cap.read()
            if not ok:
                continue

            if not self.q.empty():
                try:
                    self.q.get_nowait()
                except queue.Empty:
                    pass

            self.q.put(frame)

    # Sets running=True and starts daemon thread that runs _producer().
    def start(self):
        self.running = True
        # .start() at the end means thread starts running immediately.
        # daemon=True → thread will be killed when main program exits.
        threading.Thread(target=self._producer, daemon=True).start()

    def read_latest(self, timeout=1.0):
        # Main thread calls this to get the most recent frame.
        # timeout avoids blocking forever; if no frame arrives in 1 second,
        # returns None.
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    # Stops reading from camera and releases it cleanly.
    def stop(self):
        self.running = False
        self.cap.release()



def draw_skeleton(frame, keypoints):
    h, w, _ = frame.shape

    # draw bones
    for i, j in connections:
        if keypoints[i] and keypoints[j]:
            x1, y1 = int(keypoints[i][0] * w), int(keypoints[i][1] * h)
            x2, y2 = int(keypoints[j][0] * w), int(keypoints[j][1] * h)
            cv2.line(frame, (x1, y1), (x2, y2), (0, 255, 0), 3)

    # draw keypoints
    for kp in keypoints:
        if kp:
            x, y = int(kp[0] * w), int(kp[1] * h)
            cv2.circle(frame, (x, y), 6, (0, 0, 255), -1)



# MAIN BLOCK – YOLO person detector loop
if __name__ == "__main__":
    cuda = torch.cuda.is_available()
    if cuda:
        print("CUDA:", True, torch.cuda.get_device_name(0))

    # Use nano model for higher FPS.
    # NANO = smallest, fast variant; fits our goal for live detector.
    model = YOLO("yolov8n.pt")
    # For speed: fuse() merges convolution + batchnorm layers to reduce
    # overhead and speed up inference.
    model.fuse()

    # Move model to GPU and run one dummy prediction on a zero tensor.
    # This makes PyTorch/JIT and CUDA compile kernels and allocate memory
    # ahead of time, so the first real webcam frame won't have a long delay.
    # half=True → use FP16 (half precision), faster and uses less VRAM.
    if cuda:
        model.to("cuda")
        _ = model.predict(
            source=torch.zeros(1, 3, 480, 480).cuda(),
            imgsz=480,
            half=True,
            verbose=False,
        )


    # Create threaded camera source.
    src = CameraSource(0, w=640, h=480, fps=30, buffersize=1)
    src.start()

    t0, frames = time.time(), 0
    # We can try 480 → 416 → 384 if we want higher FPS.
    # Smaller → faster but less accuracy.
    IMG = 480
    # Confidence threshold: only detections above this value are kept.
    CONF = 0.25
    # Set to 2 to skip every other frame for higher FPS but fewer detections.
    STRIDE = 1

    try:
        # Infinite loop: read latest frame; if no frame → loop again.
        while True:
            frame = src.read_latest()
            if frame is None:
                continue

            results = model.predict(
                source=frame,
                device=0 if cuda else "cpu",
                imgsz=IMG,
                conf=CONF,
                iou=0.45,
                classes=[0],      # only person class
                half=cuda,        # use FP16 if using CUDA
                vid_stride=STRIDE,  # skip frames if > 1
                verbose=False,      # no console spam
            )

            frame_skel = frame.copy()

            detections = results[0]
            if len(detections.boxes) > 0:
                for box in detections.boxes:
                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)

                    # fallback per bbox fuori frame
                    x1 = max(0, x1); y1 = max(0, y1)
                    x2 = min(frame.shape[1], x2); y2 = min(frame.shape[0], y2)

                    person_crop = frame[y1:y2, x1:x2]
                    if person_crop.size == 0:
                        continue

                    # MediaPipe vuole RGB
                    rgb_crop = cv2.cvtColor(person_crop, cv2.COLOR_BGR2RGB)
                    res = pose.process(rgb_crop)

                    if res.pose_landmarks:
                        kps = []
                        for lm in res.pose_landmarks.landmark:
                            kx = (lm.x * (x2 - x1) + x1) / frame.shape[1]
                            ky = (lm.y * (y2 - y1) + y1) / frame.shape[0]
                            kps.append((kx, ky))
                        
                        # Disegna sul frame principale
                        draw_skeleton(frame_skel, kps)

            # FPS label
            frames += 1
            if frames % 10 == 0:
                fps = frames / (time.time() - t0)
                cv2.putText(frame_skel, f"FPS: {fps:.1f}", (10, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0,255,255), 2)

            cv2.imshow("GaitGuard 1.1 – YOLO + Skeleton", frame_skel)

            if cv2.waitKey(1) & 0xFF == 27:  # ESC
                break 

    finally:
        # finally block guarantees this runs even if there is an error
        # or ESC is pressed.
        src.stop()
        cv2.destroyAllWindows()
