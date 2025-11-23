from arduino.app_utils import *
import time
import json
import statistics
import csv 
import os
from datetime import datetime

# --- CONFIGURATION ---
CALIBRATION_READINGS = 30   
AI_LEARNING_SIZE = 30       
HISTORY_SIZE = 20
LOG_FILE = "air_quality_log.csv"

# Global Storage
baseline_resistance = None 
calibration_buffer = []     
ai_history = {"tvoc": []}
ai_model = {}               
is_ai_trained = False

# --- 1. NEW: CSV LOGGER SETUP ---
def log_data_to_csv(timestamp, temp, hum, press, ohms, tvoc, status):
    file_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, mode='a', newline='') as file:
        writer = csv.writer(file)
        # Write header if new file
        if not file_exists:
            writer.writerow(["Timestamp", "Temp(C)", "Hum(%)", "Press(hPa)", "Ohms", "TVOC", "Status"])
        
        writer.writerow([timestamp, temp, hum, press, ohms, tvoc, status])

# --- 2. NEW: HUMIDITY COMPENSATION MATH ---
def compensate_humidity(raw_ohms, current_hum):
    """
    Adjusts resistance based on humidity.
    MOX sensors drop resistance as humidity rises. We need to counteract this.
    Target Humidity is 40% (typical indoor average).
    """
    if current_hum == 0: return raw_ohms # Prevent math errors
    
    # If humidity > 40, resistance is artificially low -> We boost it up.
    # If humidity < 40, resistance is artificially high -> We lower it.
    # The factor '0.017' is a generic slope for MOX surfaces.
    
    correction_factor = 1 + (0.017 * (current_hum - 40))
    corrected_ohms = raw_ohms * correction_factor
    
    return corrected_ohms

def estimate_tvoc_mg_m3(corrected_ohms, baseline):
    if corrected_ohms >= baseline: return 0.1
    pollution_ratio = baseline / corrected_ohms
    return round(0.1 * pollution_ratio, 3)

def get_air_quality_label(tvoc_value):
    if tvoc_value < 0.3: return "Low (Healthy)"
    elif 0.3 <= tvoc_value < 0.5: return "Acceptable"
    elif 0.5 <= tvoc_value < 1.0: return "Marginal (Concern)"
    else: return "HIGH (Hazardous)"

# --- AI FUNCTIONS (Same as before) ---
def train_anomaly_detector():
    global ai_model
    ai_model = {
        "tvoc_mean": statistics.mean(ai_history["tvoc"]),
        "tvoc_stdev": statistics.stdev(ai_history["tvoc"])
    }
    if ai_model["tvoc_stdev"] < 0.01: ai_model["tvoc_stdev"] = 0.01
    print(f"\n[AI] Baseline TVOC: {ai_model['tvoc_mean']:.3f} +/- {ai_model['tvoc_stdev']:.3f}")

def detect_anomaly_zscore(tvoc):
    z_tvoc = abs(tvoc - ai_model["tvoc_mean"]) / ai_model["tvoc_stdev"]
    return z_tvoc > 3.0

def predict_trend_slope(history):
    if len(history) < 5: return 0, "Stabilizing"
    slope = (history[-1] - history[0]) / len(history)
    future_val = history[-1] + (slope * 30)
    
    trend = "SIDEWAYS"
    if slope > 0.002: trend = "RISING ↗"
    elif slope < -0.002: trend = "FALLING ↘"
    return max(0, round(future_val, 3)), trend

# --- MAIN PROCESS ---
def process_sensor_data(json_string):
    global baseline_resistance, is_ai_trained
    
    try:
        data = json.loads(json_string)
        raw_ohms = data["gas"]
        temp = data["temp"]
        hum = data["hum"]
        press = data["press"]
        
        # APPLY COMPENSATION
        comp_ohms = compensate_humidity(raw_ohms, hum)

        # 1. CALIBRATION
        if baseline_resistance is None:
            print(f"Sensor Warmup... ({len(calibration_buffer) + 1}/{CALIBRATION_READINGS})")
            calibration_buffer.append(comp_ohms)
            if len(calibration_buffer) >= CALIBRATION_READINGS:
                baseline_resistance = sum(calibration_buffer) / len(calibration_buffer)
                print(f"*** Baseline Set (Humidity Corrected): {baseline_resistance:.0f} Ohms ***")
            return

        # 2. CALCULATE
        tvoc = estimate_tvoc_mg_m3(comp_ohms, baseline_resistance)
        status = get_air_quality_label(tvoc)
        
        # 3. AI DATA
        ai_history["tvoc"].append(tvoc)
        if len(ai_history["tvoc"]) > 100: ai_history["tvoc"].pop(0)

        if not is_ai_trained and len(ai_history["tvoc"]) >= AI_LEARNING_SIZE:
            train_anomaly_detector()
            is_ai_trained = True

        anomaly_alert = ""
        forecast_msg = "Gathering data..."
        
        if is_ai_trained:
            if detect_anomaly_zscore(tvoc): anomaly_alert = " <<< [ABNORMAL SPIKE] >>>"
            recent_history = ai_history["tvoc"][-HISTORY_SIZE:] 
            future_val, trend_arrow = predict_trend_slope(recent_history)
            forecast_msg = f"Trend: {trend_arrow} (Pred: {future_val} mg/m3)"

        # 4. OUTPUT
        print("-" * 40)
        print(f"Env: {temp:.1f}°C | {hum:.1f}% | {press:.1f} hPa")
        print(f"Res: {int(raw_ohms)} (Raw) -> {int(comp_ohms)} (Compensated)")
        print(f"TVOC: {tvoc:.3f} mg/m3 [{status}]")
        print(f"AI: {forecast_msg}{anomaly_alert}")
        
        # 5. LOG TO CSV
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_data_to_csv(timestamp, temp, hum, press, comp_ohms, tvoc, status)

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    bridge = Bridge()
    print("Connected. Logging to 'air_quality_log.csv'...")
    while True:
        try:
            response = bridge.call("getAll")
            if response: process_sensor_data(response)
            time.sleep(2)
        except KeyboardInterrupt: break
        except Exception: time.sleep(1)
