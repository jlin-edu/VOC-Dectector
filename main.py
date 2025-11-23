from arduino.app_utils import *
import time
import json
import math
import csv
import os
import statistics
import urllib.request
from datetime import datetime

AI_BRAIN_FILE = "signature_file.json"
LOG_FILE = "air_quality_log.csv"

# Tuning
CALIBRATION_STEPS = 25
MATCH_THRESHOLD = 2.5  # AI distance threshold
HISTORY_SIZE = 20  # For trend prediction and Z-score
DRIFT_WINDOW = 300  # How many seconds to track for baseline drift

# Hysteresis Thresholds (Prevents flickering)
ALARM_HIGH = 0.45  # Trigger alarm above this VOC
ALARM_LOW = 0.25  # Turn off alarm below this VOC

# CLOUD CONFIG
IO_USERNAME = "jlin7269"
IO_KEY = "KEY"
CLOUD_INTERVAL = 15


class AirQualityAI:
    def __init__(self):
        self.smoothed_ohms = 0
        self.brain = {}
        self.load_brain()
        self.last_cloud_time = 0
        self.calibrated = False
        self.cal_buffer = []
        self.baseline_ohms = None
        self.baseline_temp = 0
        self.baseline_ah = 0

        self.voc_history = []  # For Trend & Z-Score
        self.long_term_buffer = []  # For Drift Correction
        self.alarm_active = False  # For Hysteresis State

    def load_brain(self):
        if os.path.exists(AI_BRAIN_FILE):
            try:
                with open(AI_BRAIN_FILE, "r") as f:
                    self.brain = json.load(f)
                print(f"Brain Loaded. Signatures: {list(self.brain.keys())}")
            except:
                print("Brain file corrupted.")
                self.brain = {"Normal Air": [0.0, 0.0, 0.1]}
        else:
            self.brain = {"Normal Air": [0.0, 0.0, 0.1]}

    def send_to_cloud(self, feed_name, value):
        url = f"https://io.adafruit.com/api/v2/{IO_USERNAME}/feeds/{feed_name}/data"
        headers = {"X-AIO-Key": IO_KEY, "Content-Type": "application/json"}
        payload = json.dumps({"value": value}).encode("utf-8")
        try:
            req = urllib.request.Request(url, data=payload, headers=headers)
            with urllib.request.urlopen(req) as response:
                pass
        except Exception as e:
            print(f"Error sending {feed_name}: {e} to Adafruit Cloud")

    def sync_dashboard(self, temp, hum, voc, ai_msg):
        if time.time() - self.last_cloud_time > CLOUD_INTERVAL:
            print(">>>>>> Syncing to Cloud Dashboard <<<<<<")
            self.send_to_cloud("temp", temp)
            self.send_to_cloud("voc", voc)
            self.send_to_cloud("ai-status", ai_msg)
            self.last_cloud_time = time.time()

    def get_absolute_humidity(self, temp, rh):
        """
        Converts Temp (C) and Relative Humidity (%) to Absolute Humidity (g/m^3).
        Sensor resistance depends on water MOLECULES, not percentage.
        """
        es = 6.112 * math.exp((17.67 * temp) / (temp + 243.5))
        vp = (rh / 100.0) * es
        ah = (vp * 216.7) / (temp + 273.15)
        return ah

    def compensate_humidity(self, ohms, temp, rh):
        """
        Adjusts resistance based on Absolute Humidity deviations.
        Standard AH is approx 9g/m^3 (20C @ 50% RH).
        """
        current_ah = self.get_absolute_humidity(temp, rh)

        if not self.calibrated:
            return ohms, current_ah
        # Linear approximation: Higher humidity = Lower Resistance
        # We compensate to "flatten" the humidity effect.
        comp_factor = 1 + (0.025 * (current_ah - self.baseline_ah))
        return ohms * comp_factor, current_ah

    def update_baseline_drift(self, current_ohms):
        """
        Feature: Automatic Baseline Correction
        If the sensor sees 'cleaner' air than the baseline for a long time,
        drift the baseline upwards to match reality.
        """
        self.long_term_buffer.append(current_ohms)
        if len(self.long_term_buffer) > DRIFT_WINDOW:
            self.long_term_buffer.pop(0)

            local_max = max(self.long_term_buffer)

            if local_max > self.baseline_ohms:
                old_base = self.baseline_ohms
                self.baseline_ohms = (self.baseline_ohms * 0.99) + (local_max * 0.01)
                if self.baseline_ohms - old_base > 50:
                    print(
                        f"Drift Correction: {old_base:.0f} -> {self.baseline_ohms:.0f}"
                    )

    def calculate_voc(self, ohms):
        if self.baseline_ohms is None:
            return 0.1
        if ohms >= self.baseline_ohms:
            return 0.1
        return round(0.1 * (self.baseline_ohms / ohms), 3)

    def get_z_score(self, current_voc):
        """
        Feature: Anomaly Detection
        Returns how many Standard Deviations the current reading is from the mean.
        """
        if len(self.voc_history) < 10:
            return 0.0

        mean = statistics.mean(self.voc_history)
        stdev = statistics.stdev(self.voc_history)

        if stdev == 0:
            return 0.0
        return (current_voc - mean) / stdev

    def classify(self, temp, hum, voc):
        dt = temp - self.baseline_temp
        dh = hum - (self.baseline_ah * 5)

        voc_weight = 50
        
        best_name = "Unknown"
        min_dist = 999.0

        for name, sig in self.brain.items():
            dist = math.sqrt(
                (dt - sig[0]) ** 2 + 
                (dh - sig[1]) ** 2 + 
                ((voc - sig[2]) * voc_weight) ** 2
            )
            if dist < min_dist:
                min_dist = dist
                best_name = name
        return best_name, min_dist

    def get_trend_and_update_history(self, current_voc):
        self.voc_history.append(current_voc)
        if len(self.voc_history) > HISTORY_SIZE:
            self.voc_history.pop(0)

        if len(self.voc_history) < 5:
            return "Stabilizing", 0.0

        slope = (self.voc_history[-1] - self.voc_history[0]) / len(self.voc_history)
        predicted_val = current_voc + (slope * 30)

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
        raw_ohms_reading = data["gas"]

        if self.smoothed_ohms == 0:
            self.smoothed_ohms = raw_ohms_reading
        else:
            self.smoothed_ohms = (self.smoothed_ohms * 0.7) + (raw_ohms_reading * 0.3)
        raw_ohms = self.smoothed_ohms
      
        temp = data["temp"]
        hum = data["hum"]
        press = data["press"]

        comp_ohms, current_ah = self.compensate_humidity(raw_ohms, temp, hum)

        if not self.calibrated:
            print(
                f"Warmup... {len(self.cal_buffer)}/{CALIBRATION_STEPS} | AH: {current_ah:.2f}g/m3"
            )
            bridge.call("setFace", "1")
            self.cal_buffer.append(comp_ohms)

            if not hasattr(self, "t_acc"):
                self.t_acc = []
            if not hasattr(self, "ah_acc"):
                self.ah_acc = []
            self.t_acc.append(temp)
            self.ah_acc.append(current_ah)

            if len(self.cal_buffer) >= CALIBRATION_STEPS:
                self.baseline_ohms = sum(self.cal_buffer) / len(self.cal_buffer)
                self.baseline_temp = sum(self.t_acc) / len(self.t_acc)
                self.baseline_ah = sum(self.ah_acc) / len(self.ah_acc)
                self.calibrated = True
                print(
                    f"*** BASELINE SET (Res: {self.baseline_ohms:.0f} | AH: {self.baseline_ah:.2f}) ***"
                )
            return

        self.update_baseline_drift(comp_ohms)
        voc = self.calculate_voc(comp_ohms)
        z_score = self.get_z_score(voc)
        trend_arrow, pred_val = self.get_trend_and_update_history(voc)
        event, dist = self.classify(temp, hum, voc)
        

        if voc > ALARM_HIGH:
            self.alarm_active = True
        elif voc < ALARM_LOW:
            self.alarm_active = False

        face_cmd = "0"
        status_msg = f"Clean (Pred: {pred_val:.2f})"

        if self.alarm_active:
            face_cmd = "2"
            status_msg = f"DANGER: {event} (VOC {voc:.2f})"
        elif abs(z_score) > 3.0:
            face_cmd = "1"
            status_msg = f"Anomaly Detected! (Z-Score: {z_score:.1f})"
        elif voc > 0.3:
            status_msg = f"Air Quality Degrading..."

        if dist < MATCH_THRESHOLD and event != "Normal Air":
            status_msg = f"AI MATCH: {event}"
            if not self.alarm_active:
                face_cmd = "1"

        bridge.call("setFace", face_cmd)

        print("-" * 50)
        print(f"ENV   : {temp:.1f}C | {hum:.1f}%RH | {current_ah:.2f}g/m3 (AH)")
        print(f"GAS   : {raw_ohms/1000:.1f}kΩ (Raw) -> {comp_ohms/1000:.1f}kΩ (Comp)")
        print(f"VOC   : {voc:.3f} | Z-Score: {z_score:.2f}")
        print(f"STATE : {'ALARM' if self.alarm_active else 'SAFE'} | {status_msg}")

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
                        ["Timestamp", "Temp", "Hum", "VOC", "Event", "Trend"]
                    )
                writer.writerow([ts, temp, hum, voc, event, trend])
        except:
            pass


if __name__ == "__main__":
    bridge = Bridge()
    ai = AirQualityAI()
    print("(AH Comp + Auto-Drift + Anomaly Detection)")

    while True:
        try:
            ai.update(bridge)
            time.sleep(1)
        except KeyboardInterrupt:
            bridge.call("setFace", "0")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(1)
