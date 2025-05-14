from machine import Pin, ADC, I2C, deepsleep
import time
import bme280_float as bme280
import urequests
import network
import json
import math
import ubinascii
import gc
from collections import deque

## 1. CẤU HÌNH HỆ THỐNG
CONFIG = {
    # Cấu hình WiFi
    'wifi_ssid': "Xiaomi11T",
    'wifi_password': "heiina123",
    
    # Server nhận dữ liệu
    'flask_server': "http://192.168.254.185:5000/data",
    
    # Cấu hình cảm biến
    'sensors': {
        'soil_pin': 32,            # Chân đọc độ ẩm đất
        'i2c_scl': 22,             # Chân I2C SCL
        'i2c_sda': 21,             # Chân I2C SDA
        'sampling_interval': 5,    # Thời gian giữa các lần lấy mẫu (giây)
        'sample_count': 10,        # Số mẫu cần thu thập
        'deep_sleep': False,       # Chế độ ngủ sâu
        'soil_min': 1000,          # Giá trị tối thiểu hợp lệ của cảm biến đất
        'soil_max': 3000           # Giá trị tối đa hợp lệ của cảm biến đất
    },
    
    # Hệ số hồi quy cho dự báo mưa
    'regression_coeffs': {
        'intercept': -5.0,
        'ah': 0.3,                 # Ẩm tuyệt đối
        'temp_dew_reciprocal': 2.5,# Nghịch đảo chênh lệch nhiệt độ - điểm sương
        'soil': -0.002,            # Độ ẩm đất
        'pressure': -0.02,         # Áp suất khí quyển
        'pressure_trend': 0.5      # Xu hướng áp suất
    },
    
    # Ngưỡng dự báo thời tiết
    'weather_thresholds': [
        {'ah_min': 17, 'temp_dew_max': 1, 'rain_prob': 0.95, 'comfort': 'Rất ngột ngạt', 'description': 'Chắc chắn có mưa'},
        {'ah_min': 15, 'temp_dew_max': 2, 'rain_prob': 0.75, 'comfort': 'Khó chịu nhẹ', 'description': 'Khả năng cao có mưa'},
        {'ah_min': 12, 'temp_dew_max': 5, 'rain_prob': 0.30, 'comfort': 'Dễ chịu', 'description': 'Ít khả năng có mưa'},
        {'ah_min': 0,  'temp_dew_max': 99, 'rain_prob': 0.05, 'comfort': 'Khô ráo', 'description': 'Chắc chắn không mưa'}
    ],
    
    # Ngưỡng áp suất
    'pressure_thresholds': {
        'high': 1020,           # Áp suất cao
        'normal': 1013,         # Áp suất bình thường
        'low': 1000,            # Áp suất thấp
        'storm': 980,           # Áp suất bão
        'trend_threshold': -2.0 # Ngưỡng xu hướng giảm (hPa/3h)
    },
    
    # Giá trị mặc định khi đọc cảm biến thất bại
    'default_values': {
        'temperature': 25.0,
        'humidity': 50.0,
        'pressure': 1013.0,
        'soil_moisture': 2000
    }
}

## 2. LỚP XỬ LÝ DỮ LIỆU THỐNG KÊ
class DataProcessor:
    @staticmethod
    def get_median(values):
        """Tính giá trị trung vị"""
        if not values:
            return 0
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        mid = n // 2
        if n % 2 == 1:
            return sorted_vals[mid]
        return (sorted_vals[mid-1] + sorted_vals[mid]) / 2

    @staticmethod
    def filter_outliers(values):
        """Lọc giá trị ngoại lai bằng phương pháp IQR"""
        if len(values) < 4:
            return values
            
        q1 = DataProcessor.get_median(values[:len(values)//2])
        q3 = DataProcessor.get_median(values[len(values)//2:])
        iqr = q3 - q1
        
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        
        return [v for v in values if lower <= v <= upper]

## 3. LỚP QUẢN LÝ CẢM BIẾN
class SensorManager:
    def __init__(self):
        # Khởi tạo I2C với timeout
        self.i2c = I2C(0, scl=Pin(CONFIG['sensors']['i2c_scl']), 
                      sda=Pin(CONFIG['sensors']['i2c_sda']), 
                      timeout=1000)
        
        # Khởi tạo BME280 với kiểm tra lỗi
        try:
            self.bme = bme280.BME280(i2c=self.i2c)
            print("Khởi tạo BME280 thành công")
        except Exception as e:
            print("Lỗi khởi tạo BME280:", e)
            self.bme = None
        
        # Cấu hình cảm biến độ ẩm đất
        self.soil_sensor = ADC(Pin(CONFIG['sensors']['soil_pin']))
        self.soil_sensor.atten(ADC.ATTN_11DB)
        
        # Lịch sử áp suất 3 giờ (6 mẫu, mỗi mẫu 30 phút)
        self.pressure_history = deque((), 6)
        
        # Giá trị đọc trước đó
        self.last_valid_read = {
            'temperature': CONFIG['default_values']['temperature'],
            'humidity': CONFIG['default_values']['humidity'],
            'pressure': CONFIG['default_values']['pressure'],
            'soil_moisture': CONFIG['default_values']['soil_moisture']
        }

    def read_bme280(self):
        """Đọc giá trị từ BME280 với xử lý lỗi"""
        if not self.bme:
            print("Cảm biến BME280 chưa được khởi tạo")
            return (float('nan'), float('nan'), float('nan'))
        
        try:
            temp, pressure, humidity = self.bme.values
            temp_val = float(temp[:-1])    # Bỏ 'C'
            pressure_val = float(pressure[:-3]) # Bỏ 'hPa'
            humidity_val = float(humidity[:-1])  # Bỏ '%'
            
            # Kiểm tra giá trị hợp lệ
            if -40 <= temp_val <= 85 and 300 <= pressure_val <= 1100 and 0 <= humidity_val <= 100:
                self.last_valid_read['temperature'] = temp_val
                self.last_valid_read['humidity'] = humidity_val
                self.last_valid_read['pressure'] = pressure_val
                return (temp_val, pressure_val, humidity_val)
            
            print("Giá trị BME280 ngoài phạm vi hợp lệ")
            return (float('nan'), float('nan'), float('nan'))
            
        except Exception as e:
            print("Lỗi đọc BME280:", e)
            return (float('nan'), float('nan'), float('nan'))

    def read_soil_moisture(self):
        """Đọc độ ẩm đất với kiểm tra giá trị hợp lệ"""
        try:
            value = self.soil_sensor.read()
            # Kiểm tra giá trị hợp lệ
            if CONFIG['sensors']['soil_min'] <= value <= CONFIG['sensors']['soil_max']:
                self.last_valid_read['soil_moisture'] = value
                return value
            print(f"Giá trị độ ẩm đất không hợp lệ: {value}")
            return float('nan')
        except Exception as e:
            print("Lỗi đọc cảm biến đất:", e)
            return float('nan')

    def collect_samples(self):
        """Thu thập và xử lý nhiều mẫu dữ liệu"""
        temps, hums, pressures, soils = [], [], [], []

        print(f"Bắt đầu thu thập {CONFIG['sensors']['sample_count']} mẫu...")
        
        for i in range(CONFIG['sensors']['sample_count']):
            # Đọc cảm biến
            temp, pressure, humidity = self.read_bme280()
            soil = self.read_soil_moisture()

            # Ghi lại giá trị hợp lệ
            if not math.isnan(temp):
                temps.append(temp)
            if not math.isnan(humidity):
                hums.append(humidity)
            if not math.isnan(pressure):
                pressures.append(pressure)
            if not math.isnan(soil):
                soils.append(soil)

            # Hiển thị thông tin debug
            print(f"[{i+1:02}] T: {temp:.1f}°C, H: {humidity:.1f}%, P: {pressure:.1f} hPa, Soil: {soil}")

            # Chờ giữa các lần đọc
            if i < CONFIG['sensors']['sample_count'] - 1:
                time.sleep(CONFIG['sensors']['sampling_interval'])

        # Xử lý dữ liệu với fallback
        def process_values(values, key):
            if not values:
                print(f"⚠️ Không có dữ liệu {key} hợp lệ, sử dụng giá trị cuối cùng: {self.last_valid_read[key]}")
                return self.last_valid_read[key]
            
            # Lọc nhiễu
            filtered = DataProcessor.filter_outliers(values)
            if not filtered:
                print(f"⚠️ Tất cả giá trị {key} bị loại bỏ do nhiễu, sử dụng giá trị cuối cùng: {self.last_valid_read[key]}")
                return self.last_valid_read[key]
                
            return sum(filtered)/len(filtered)

        result = {
            'temperature': process_values(temps, 'temperature'),
            'humidity': process_values(hums, 'humidity'),
            'pressure': process_values(pressures, 'pressure'),
            'soil_moisture': process_values(soils, 'soil_moisture')
        }

        # Cập nhật xu hướng áp suất
        self.pressure_history.append(result['pressure'])
        if len(self.pressure_history) > 6:
            self.pressure_history.popleft()
        result['pressure_trend'] = self.calculate_pressure_trend()

        print("Kết thúc thu thập dữ liệu")
        return result

    def calculate_pressure_trend(self):
        """Tính xu hướng thay đổi áp suất (hPa/3h)"""
        if len(self.pressure_history) < 2:
            return 0.0
        return self.pressure_history[-1] - self.pressure_history[0]

## 4. LỚP PHÂN TÍCH THỜI TIẾT
class WeatherAnalyzer:
    @staticmethod
    def calculate_dew_point(temp, hum):
        """Tính điểm sương (°C)"""
        if temp is None or hum is None or math.isnan(temp) or math.isnan(hum):
            return float('nan')
        a, b = 17.27, 237.7
        alpha = ((a * temp) / (b + temp)) + math.log(hum/100.0)
        return (b * alpha) / (a - alpha)

    @staticmethod
    def calculate_absolute_humidity(temp, hum):
        """Tính ẩm tuyệt đối (g/m³)"""
        if temp is None or hum is None or math.isnan(temp) or math.isnan(hum):
            return float('nan')
        mw, r = 18.016, 8.3144
        temp_k = temp + 273.15
        Pws = 6.116441 * 10**((7.591386 * temp)/(temp + 240.7263))
        Pw = (hum / 100.0) * Pws
        return (Pw * 100 * mw) / (r * temp_k)

    def analyze_weather(self, sensor_data):
        """Phân tích dữ liệu thời tiết toàn diện"""
        # Tính các thông số cơ bản
        dew_point = self.calculate_dew_point(sensor_data['temperature'], sensor_data['humidity'])
        temp_dew_diff = sensor_data['temperature'] - dew_point
        ah = self.calculate_absolute_humidity(sensor_data['temperature'], sensor_data['humidity'])
        
        # Dự báo mưa
        rain_prob = self.predict_rain_probability(
            ah, temp_dew_diff, 
            sensor_data['soil_moisture'],
            sensor_data['pressure'],
            sensor_data['pressure_trend']
        )
        
        # Đánh giá mức độ thoải mái
        comfort = self.evaluate_comfort_level(ah, temp_dew_diff, sensor_data['pressure'])
        
        # Tạo cảnh báo
        alerts = self.generate_weather_alerts(sensor_data, rain_prob)
        
        # Xác định mô tả thời tiết
        weather_desc = self.get_weather_description(rain_prob)
        
        return {
            'dew_point': dew_point,
            'absolute_humidity': ah,
            'rain_probability': rain_prob,
            'comfort_index': comfort,
            'alerts': alerts,
            'weather_description': weather_desc,
            'temp_dew_diff': temp_dew_diff
        }

    def predict_rain_probability(self, ah, temp_dew_diff, soil, pressure, pressure_trend):
        """Dự báo xác suất mưa (0-1)"""
        if any(math.isnan(x) for x in [ah, temp_dew_diff, soil, pressure]):
            print("Không thể dự báo mưa do dữ liệu không hợp lệ")
            return float('nan')
            
        # Tính toán xác suất mưa
        prob = (
            CONFIG['regression_coeffs']['intercept'] +
            CONFIG['regression_coeffs']['ah'] * ah +
            CONFIG['regression_coeffs']['temp_dew_reciprocal'] * (1/max(0.1, temp_dew_diff)) +
            CONFIG['regression_coeffs']['soil'] * soil +
            CONFIG['regression_coeffs']['pressure'] * (1013 - pressure) +
            CONFIG['regression_coeffs']['pressure_trend'] * min(0, pressure_trend)
        )
        
        # Giới hạn trong khoảng 0-1
        return max(0.0, min(1.0, prob))

    def evaluate_comfort_level(self, ah, temp_dew_diff, pressure):
        """Đánh giá chỉ số thoải mái (0-100)"""
        comfort = 50  # Giá trị trung bình
        
        # Đánh giá theo độ ẩm và nhiệt độ
        for threshold in CONFIG['weather_thresholds']:
            if ah >= threshold['ah_min'] and temp_dew_diff <= threshold['temp_dew_max']:
                if threshold['ah_min'] == 17:
                    comfort = 10 + min(20, temp_dew_diff * 10)
                elif threshold['ah_min'] == 15:
                    comfort = 30 + min(30, (temp_dew_diff + 2) * 10)
                elif threshold['ah_min'] == 12:
                    comfort = 60 + min(30, temp_dew_diff * 6)
                else:
                    comfort = 90 - min(20, ah)
                break
        
        # Hiệu chỉnh theo áp suất
        if pressure < CONFIG['pressure_thresholds']['low']:
            comfort -= 15
        elif pressure > CONFIG['pressure_thresholds']['high']:
            comfort += 5
            
        return max(0, min(100, round(comfort)))

    def generate_weather_alerts(self, data, rain_prob):
        """Tạo các cảnh báo thời tiết"""
        alerts = []
        
        # Cảnh báo mưa
        if not math.isnan(rain_prob):
            if rain_prob > 0.8:
                alerts.append("CẢNH BÁO: Khả năng mưa rất cao (>80%)")
            elif rain_prob > 0.6:
                alerts.append("Lưu ý: Khả năng mưa cao (>60%)")
        
        # Cảnh báo áp suất
        if not math.isnan(data['pressure']):
            if data['pressure'] < CONFIG['pressure_thresholds']['storm']:
                alerts.append("⚠️ Áp suất cực thấp - Nguy cơ bão lớn")
            elif data['pressure'] < CONFIG['pressure_thresholds']['low']:
                alerts.append("⚠️ Áp suất thấp - Thời tiết xấu")
            elif data['pressure_trend'] < CONFIG['pressure_thresholds']['trend_threshold']:
                alerts.append("⚠️ Áp suất giảm nhanh - Mưa có thể đến sớm")
        
        # Cảnh báo nhiệt độ
        if not math.isnan(data['temperature']):
            if data['temperature'] > 35:
                alerts.append("🔥 Nhiệt độ nguy hiểm: >35°C")
            elif data['temperature'] < 10:
                alerts.append("❄️ Nhiệt độ thấp: <10°C")
        
        return alerts

    def get_weather_description(self, rain_prob):
        """Xác định mô tả thời tiết dựa trên xác suất mưa"""
        if math.isnan(rain_prob):
            return "Không thể xác định"
            
        for threshold in sorted(CONFIG['weather_thresholds'], key=lambda x: x['rain_prob'], reverse=True):
            if rain_prob >= threshold['rain_prob']:
                return threshold['description']
        return "Thời tiết ổn định"

## 5. LỚP QUẢN LÝ MẠNG
class NetworkManager:
    def __init__(self):
        self.wlan = network.WLAN(network.STA_IF)
        self.wlan.active(True)
        self.retry_count = 0
        self.max_retries = 3

    def connect_wifi(self):
        """Kết nối WiFi với cơ chế retry thông minh"""
        if self.wlan.isconnected():
            return True
        
        print("\nĐang kết nối WiFi...")
        self.wlan.connect(CONFIG['wifi_ssid'], CONFIG['wifi_password'])
        
        for i in range(20):  # Tăng timeout lên 20 giây
            if self.wlan.isconnected():
                print(f"Kết nối thành công sau {i+1} giây!")
                print("Địa chỉ IP:", self.wlan.ifconfig()[0])
                self.retry_count = 0
                return True
            time.sleep(1)
        
        print("Kết nối WiFi thất bại sau 20 giây")
        self.retry_count += 1
        return False

    def send_data(self, data):
        """Gửi dữ liệu đến server với xử lý lỗi đầy đủ"""
        if not self.connect_wifi():
            return False
            
        try:
            headers = {'Content-Type': 'application/json'}
            payload = json.dumps(data)
            print("\nĐang gửi dữ liệu đến server...")
            
            # Thêm timeout và kiểm tra kết nối
            response = urequests.post(
                CONFIG['flask_server'],
                data=payload,
                headers=headers,
                timeout=10
            )
            
            print("Server phản hồi:", response.status_code, response.text)
            response.close()
            return True
            
        except OSError as e:
            if e.errno == 113:  # ECONNABORTED
                print("Lỗi kết nối: Server từ chối hoặc timeout")
            else:
                print("Lỗi mạng:", e)
        except Exception as e:
            print("Lỗi khi gửi dữ liệu:", e)
            
        # Thử lại nếu cần
        if self.retry_count < self.max_retries:
            print(f"Thử lại ({self.retry_count + 1}/{self.max_retries})...")
            time.sleep(2)
            return self.send_data(data)
        
        return False

## 6. HÀM CHÍNH
def main():
    print("\n=== KHỞI ĐỘNG HỆ THỐNG DỰ BÁO THỜI TIẾT ===")
    print("Phiên bản: 2.0 - Ngày cập nhật: 15/11/2023")
    
    try:
        # Khởi tạo các thành phần
        print("\nĐang khởi tạo hệ thống...")
        sensors = SensorManager()
        analyzer = WeatherAnalyzer()
        network = NetworkManager()
        
        # Thu thập dữ liệu cảm biến
        print("\n[1/3] Đang thu thập dữ liệu cảm biến...")
        sensor_data = sensors.collect_samples()
        
        # Phân tích thời tiết
        print("\n[2/3] Đang phân tích dữ liệu thời tiết...")
        weather_data = analyzer.analyze_weather(sensor_data)
        
        # Tạo báo cáo hoàn chỉnh
        report = sensor_data.copy()
        report.update(weather_data)

        report['rain_probability_percent'] = (
            round(weather_data['rain_probability'] * 100, 1)
            if not math.isnan(weather_data['rain_probability'])
            else float('nan')
        )
        report['device_id'] = ubinascii.hexlify(network.wlan.config('mac')).decode()
        report['timestamp'] = int(time.time())
        
        # Hiển thị kết quả
        print("\n=== KẾT QUẢ PHÂN TÍCH ===")
        print(f"🌡️ Nhiệt độ: {report['temperature']:.1f}°C")
        print(f"💧 Độ ẩm: {report['humidity']:.1f}%")
        print(f"📊 Áp suất: {report['pressure']:.1f} hPa")
        print(f"📉 Xu hướng áp suất: {report['pressure_trend']:.1f} hPa/3h")
        print(f"🌱 Độ ẩm đất: {report['soil_moisture']}")
        print(f"🌫️ Điểm sương: {report['dew_point']:.1f}°C")
        print(f"💦 Ẩm tuyệt đối: {report['absolute_humidity']:.1f} g/m³")
        print(f"🌧️ Xác suất mưa: {report['rain_probability_percent']}%")
        print(f"😊 Chỉ số thoải mái: {report['comfort_index']}/100")
        print(f"📝 Mô tả: {report['weather_description']}")
        
        if report['alerts']:
            print("\n⚠️ CẢNH BÁO:")
            for alert in report['alerts']:
                print("-", alert)
        
        # Gửi dữ liệu
        print("\n[3/3] Đang gửi dữ liệu...")
        if network.send_data(report):
            print("✅ Gửi dữ liệu thành công!")
        else:
            print("❌ Gửi dữ liệu thất bại!")
        
    except Exception as e:
        print("\n⛔ LỖI HỆ THỐNG:", e)
    finally:
        # Dọn dẹp bộ nhớ
        gc.collect()
        print("\nHoàn tất chu kỳ hoạt động")

if __name__ == '__main__':
    while True:
        main()
        if CONFIG['sensors']['deep_sleep']:
            print("Đang chuyển sang chế độ ngủ sâu...")
            deepsleep(60 * 1000)  # Ngủ 1 phút
        else:
            print("Chờ 60 giây trước khi bắt đầu chu kỳ mới...")
            time.sleep(60)  # Chờ 1 phút