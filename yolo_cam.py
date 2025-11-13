# yolo_cam.py  —  fast live "person" detector (nano, FP16, low-latency)
import cv2, time, threading, queue
from ultralytics import YOLO
import torch

# ---------- Camera ----------
def open_camera(index=0, w=640, h=480, fps=30, mjpeg=True, buffersize=1):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)  # try CAP_MSMF if DSHOW ever fails
    if mjpeg:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    cap.set(cv2.CAP_PROP_FPS, fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, buffersize)   # keep only latest frame
    return cap

class CameraSource:
    def __init__(self, cam_index=0, **kw):
        self.cap = open_camera(cam_index, **kw)
        if not self.cap.isOpened():
            raise RuntimeError("Camera not available")
        self.q = queue.Queue(maxsize=1)
        self.running = False

    def _producer(self):
        while self.running:
            ok, frame = self.cap.read()
            if not ok:
                continue
            if not self.q.empty():
                try: self.q.get_nowait()
                except queue.Empty: pass
            self.q.put(frame)

    def start(self):
        self.running = True
        threading.Thread(target=self._producer, daemon=True).start()

    def read_latest(self, timeout=1.0):
        try: return self.q.get(timeout=timeout)
        except queue.Empty: return None

    def stop(self):
        self.running = False
        self.cap.release()

# ---------- Main ----------
if __name__ == "__main__":
    cuda = torch.cuda.is_available()
    if cuda:
        print("CUDA:", True, torch.cuda.get_device_name(0))

    # Use nano model for higher FPS
    model = YOLO("yolov8n.pt")     # auto-downloads first run
    model.fuse()                   # fuse conv+bn for speed

    # Optional warmup to compile kernels (reduces first-frame stall)
    if cuda:
        model.to("cuda")
        _ = model.predict(source=torch.zeros(1, 3, 480, 480).cuda(), imgsz=480, half=True, verbose=False)

    # Camera source (640x480 is a good balance for laptop webcams)
    src = CameraSource(0, w=640, h=480, fps=30, buffersize=1)
    src.start()

    t0, frames = time.time(), 0
    IMG = 480          # try 480 → 416 → 384 if you want even higher FPS
    CONF = 0.25        # confidence threshold
    STRIDE = 1         # set to 2 to skip every other frame (more FPS)

    try:
        while True:
            frame = src.read_latest()
            if frame is None:
                continue

            # Inference: class 0 = person
            results = model.predict(
                source=frame,
                device=0 if cuda else "cpu",
                imgsz=IMG,
                conf=CONF,
                iou=0.45,
                classes=[0],
                half=cuda,          # FP16 on CUDA
                vid_stride=STRIDE,
                verbose=False
            )

            annotated = results[0].plot()

            # FPS overlay (smoothed)
            frames += 1
            if frames % 10 == 0:
                fps = frames / (time.time() - t0)
                cv2.putText(
                    annotated, f"FPS: {fps:.1f}", (10, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 2
                )

            cv2.imshow("GaitGuard — live detector", annotated)
            if cv2.waitKey(1) & 0xFF == 27:  # ESC
                break
    finally:
        src.stop()
        cv2.destroyAllWindows()
