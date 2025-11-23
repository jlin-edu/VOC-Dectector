from arduino.app_utils import *
import time
import json
import math
import csv
import os
import statistics
import urllib.request
from datetime import datetime

# CONFIGURATION
AI_BRAIN_FILE = "signature_file.json"
LOG_FILE = "air_quality_log.csv"
CALIBRATION_STEPS = 25
MATCH_THRESHOLD = 2.5                  # How strict the AI matching is
HISTORY_SIZE = 20                  # How many readings to keep for Trend Prediction
# CLOUD CONFIG
IO_USERNAME = "jlin7269"
IO_KEY = "KEY_HERE"                    # Not putting private key online since its private
CLOUD_INTERVAL = 15                    # Data every 15 seconds


class AirQualityAI:
    def __init__(self):
        self.brain = {}
        self.load_brain()

        self.last_cloud_time = 0

        self.calibrated = False
        self.cal_buffer = []
        self.baseline_ohms = None
        self.baseline_temp = 0
        self.baseline_hum = 0

        self.voc_history = []

    def load_brain(self):
        if os.path.exists(AI_BRAIN_FILE):
            try:
                with open(AI_BRAIN_FILE, "r") as f:
                    self.brain = json.load(f)
                print(f"Brain Loaded. Signatures: {list(self.brain.keys())}")
            except:
                print("Brain file corrupted.")
                self.brain = {"Normal Air": [0.0, 0.0, 0.1]}  # Fallback
        else:
            print("signature_file.json missing!")
            self.brain = {"Normal Air": [0.0, 0.0, 0.1]}

    def send_to_cloud(self, feed_name, value):
        """
        Sends data to Adafruit IO using standard HTTP requests.
        """
        url = f"https://io.adafruit.com/api/v2/{IO_USERNAME}/feeds/{feed_name}/data"
        headers = {"X-AIO-Key": IO_KEY, "Content-Type": "application/json"}
        payload = json.dumps({"value": value}).encode("utf-8")

        try:
            req = urllib.request.Request(url, data=payload, headers=headers)
            with urllib.request.urlopen(req) as response:
                pass  # Success
        except Exception as e:
            print(f"[CLOUD] Error sending {feed_name}: {e}")

    def sync_dashboard(self, temp, hum, voc, ai_msg):
        """
        Checks time and pushes data if interval has passed
        """
        if time.time() - self.last_cloud_time > CLOUD_INTERVAL:
            print(" >> Syncing to Cloud Dashboard...")
            self.send_to_cloud("temp", temp)
            self.send_to_cloud("voc", voc)
            self.send_to_cloud("ai-status", ai_msg)
            self.last_cloud_time = time.time()

    def calculate_voc(self, ohms):
        if self.baseline_ohms is None:
            return 0.1
        if ohms >= self.baseline_ohms:
            return 0.1
        return round(0.1 * (self.baseline_ohms / ohms), 3)

    def compensate_humidity(self, ohms, hum):
        if hum == 0:
            return ohms
        return ohms * (1 + (0.017 * (hum - 40)))

    # AI CLASSIFICATION
    def classify(self, temp, hum, voc):
        dt = temp - self.baseline_temp
        dh = hum - self.baseline_hum

        best_name = "Unknown"
        min_dist = 999.0

        for name, sig in self.brain.items():
            # Euclidean Distance
            dist = math.sqrt(
                (dt - sig[0]) ** 2 + (dh - sig[1]) ** 2 + (voc - sig[2]) ** 2
            )
            if dist < min_dist:
                min_dist = dist
                best_name = name

        return best_name, min_dist

    # TREND PREDICTION
    def get_trend(self, current_voc):
        self.voc_history.append(current_voc)
        if len(self.voc_history) > HISTORY_SIZE:
            self.voc_history.pop(0)

        if len(self.voc_history) < 5:
            return "Stabilizing", 0.0

        # Simple Slope: (End - Start) / Time
        slope = (self.voc_history[-1] - self.voc_history[0]) / len(self.voc_history)
        predicted_val = current_voc + (slope * 30)  # Predict 30s into future

        direction = "FLAT"
        if slope > 0.005:
            direction = "RISING"
        elif slope < -0.005:
            direction = "FALLING"

        return direction, predicted_val

    def update(self, bridge):
        resp = bridge.call("getAll")
        if not resp:
            return

        data = json.loads(resp)
        raw_ohms = data["gas"]
        temp = data["temp"]
        hum = data["hum"]
        press = data["press"]

        comp_ohms = self.compensate_humidity(raw_ohms, hum)

        if not self.calibrated:
            print(f"Warmup... {len(self.cal_buffer)}/{CALIBRATION_STEPS}")
            bridge.call("setFace", "1")  # Blue Face
            self.cal_buffer.append(comp_ohms)

            if not hasattr(self, "t_acc"):
                self.t_acc = []
            if not hasattr(self, "h_acc"):
                self.h_acc = []
            self.t_acc.append(temp)
            self.h_acc.append(hum)

            if len(self.cal_buffer) >= CALIBRATION_STEPS:
                self.baseline_ohms = sum(self.cal_buffer) / len(self.cal_buffer)
                self.baseline_temp = sum(self.t_acc) / len(self.t_acc)
                self.baseline_hum = sum(self.h_acc) / len(self.h_acc)
                self.calibrated = True
                print(f"*** BASELINE SET (Room: {self.baseline_temp:.1f}C) ***")
            return

        voc = self.calculate_voc(comp_ohms)

        trend_arrow, pred_val = self.get_trend(voc)
        event, dist = self.classify(temp, hum, voc)

        is_safe = True
        face_cmd = "0"  # Happy

        if dist < MATCH_THRESHOLD:
            if event == "Normal Air":
                status_msg = f"Normal Air"
                face_cmd = "0"
            else:
                status_msg = f"DETECTED: {event}!"
                face_cmd = "2"  # Danger
                is_safe = False
        else:
            # Not sure, but is VOC high?
            if voc > 0.3:
                status_msg = f"Unknown High VOC (Closest: {event})"
                face_cmd = "1"  # Neutral
                is_safe = False
            else:
                status_msg = f"Clean (Closest: {event})"
                face_cmd = "0"

        # Update Arduino
        bridge.call("setFace", face_cmd)

        # Dashboard Output
        print("-" * 50)
        print(f"ENV   : {temp:.1f}°C | {hum:.1f}% | {press:.0f} hPa")
        print(f"VOC   : {voc:.3f} mg/m3")
        print(f"TREND : {trend_arrow} (Pred: {pred_val:.2f})")
        print(f"AI    : {status_msg} (Conf: {dist:.2f})")

        # Log to CSV
        self.log_to_csv(temp, hum, voc, event, trend_arrow)
        self.sync_dashboard(temp, hum, voc, status_msg)

    def log_to_csv(self, temp, hum, voc, event, trend):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        file_exists = os.path.isfile(LOG_FILE)
        try:
            with open(LOG_FILE, mode="a", newline="") as file:
                writer = csv.writer(file)
                if not file_exists:
                    writer.writerow(
                        ["Timestamp", "Temp", "Hum", "VOC", "AI_Event", "Trend"]
                    )
                writer.writerow([ts, temp, hum, voc, event, trend])
        except:
            pass


if __name__ == "__main__":
    bridge = Bridge()
    ai = AirQualityAI()
    print("Connected. Full Dashboard Started.")

    while True:
        try:
            ai.update(bridge)
            time.sleep(1)
        except KeyboardInterrupt:
            bridge.call("setFace", "0")  # Reset on exit
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(1)
