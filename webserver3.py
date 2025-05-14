from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime
import sqlite3
import time

app = Flask(__name__)
CORS(app)  # Bật CORS để cho phép truy cập từ frontend

# Cấu hình database
def init_db():
    conn = sqlite3.connect('weather_data.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS weather
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  device_id TEXT,
                  temperature REAL,
                  humidity REAL,
                  pressure REAL,
                  soil_moisture INTEGER,
                  pressure_trend REAL,
                  absolute_humidity REAL,
                  dew_point REAL,
                  rain_probability REAL,
                  comfort_index INTEGER,
                  weather_description TEXT,
                  timestamp INTEGER,
                  created_at TEXT)''')
    conn.commit()
    conn.close()

init_db()

# Biến lưu dữ liệu mới nhất
latest_data = {
    'last_updated': None,
    'data': None
}

@app.route('/')
def index():
    return "ESP32 Weather Station Backend"

@app.route('/api/data', methods=['POST'])
def receive_data():
    try:
        # Debug raw data
        print("📥 Raw data received:", request.data)
        
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        # Validate required fields
        required_fields = ['temp', 'humi', 'pres', 'soil', 'ptrend', 
                          'ah', 'dew', 'rain', 'comfort', 'desc', 
                          'device_id', 'timestamp']
        
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400

        # Lưu vào database
        conn = sqlite3.connect('weather_data.db')
        c = conn.cursor()
        c.execute('''INSERT INTO weather 
                    (device_id, temperature, humidity, pressure, soil_moisture,
                     pressure_trend, absolute_humidity, dew_point, rain_probability,
                     comfort_index, weather_description, timestamp, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                 (data['device_id'], data['temp'], data['humi'], data['pres'],
                  data['soil'], data['ptrend'], data['ah'], data['dew'],
                  data['rain'], data['comfort'], data['desc'], data['timestamp'],
                  datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()

        # Cập nhật dữ liệu mới nhất
        latest_data['data'] = {
            'temp': data['temp'],
            'humi': data['humi'],
            'pres': data['pres'],
            'soil': data['soil'],
            'ptrend': data['ptrend'],
            'ah': data['ah'],
            'dew': data['dew'],
            'rain': data['rain'],
            'comfort': data['comfort'],
            'desc': data['desc'],
            'timestamp': int(data.get('timestamp', time.time()))
        }
        latest_data['last_updated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        return jsonify({"status": "success"})

    except Exception as e:
        print("❌ Error processing data:", str(e))
        return jsonify({"error": "Invalid request format"}), 400

@app.route('/get_current')
def get_current_data():
    if not latest_data.get('data'):
        return jsonify({"error": "No data available"}), 404
        
    return jsonify({
        "temperature": latest_data['data'].get('temp'),
        "humidity": latest_data['data'].get('humi'),
        "pressure": latest_data['data'].get('pres'),
        "soil_moisture": latest_data['data'].get('soil'),
        "pressure_trend": latest_data['data'].get('ptrend'),
        "absolute_humidity": latest_data['data'].get('ah'),
        "dew_point": latest_data['data'].get('dew'),
        "rain_probability": latest_data['data'].get('rain'),
        "comfort_index": latest_data['data'].get('comfort'),
        "weather_description": latest_data['data'].get('desc'),
        "last_updated": latest_data.get('last_updated')
    })

@app.route('/get_history/<metric>')
def get_history(metric):
    print(f"📊 Requested history for: {metric}")  # Thêm dòng này
# Thay đổi metric_map thành:
    metric_map = {
        'temperature': 'temperature',
        'humidity': 'humidity',
        'pressure': 'pressure',
        'soil_moisture': 'soil_moisture',
        'rain_probability': 'rain_probability',
        'comfort_index': 'comfort_index',
        'absolute_humidity': 'absolute_humidity',
        'dew_point': 'dew_point',
        'pressure_trend': 'pressure_trend'
    }
    
    if metric not in metric_map:
        print(f"❌ Invalid metric: {metric}")  # Thêm dòng này
        return jsonify({"error": "Metric không hợp lệ"}), 400

    conn = sqlite3.connect('weather_data.db')
    c = conn.cursor()
    
    try:
        # Lấy 24 giờ gần nhất, sắp xếp từ cũ đến mới
        cutoff = time.time() - 24*3600
        c.execute(f'''
            SELECT timestamp, {metric_map[metric]} 
            FROM weather 
            WHERE timestamp > ? 
            ORDER BY timestamp ASC
            LIMIT 100
        ''', (cutoff,))
        
        history = [{
            'timestamp': row[0],
            'value': row[1]
        } for row in c.fetchall()]
        
        return jsonify(history)
        
    except Exception as e:
        print(f"Lỗi khi lấy lịch sử {metric}:", str(e))
        return jsonify({"error": str(e)}), 500
        
    finally:
        conn.close()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)