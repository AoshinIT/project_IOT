from machine import Pin, ADC, UART
import time
import dht
import random

DHT_PIN = 2
dht_sensor = dht.DHT22(Pin(DHT_PIN))

soil_adc = ADC(Pin(33))
soil_adc.atten(ADC.ATTN_11DB)

uart = UART(1, baudrate=9600, tx=17, rx=16)

TOUCH_PIN = 5
touch_sensor = Pin(TOUCH_PIN, Pin.IN)

TEMP_THRESHOLD_COLD = 25.0
HUMIDITY_THRESHOLD = 70.0
SOIL_MOISTURE_THRESHOLD = 600

X_data = []
y_data = []

MAX_DATA_SIZE = 20

is_reset = False

def handle_touch(pin):
    global is_reset
    time.sleep(0.5)
    is_reset = True
    print("Ngắt: Cảm ứng được kích hoạt, chuẩn bị reset hệ thống.")
    uart.write("RESET\n")

touch_sensor.irq(trigger=Pin.IRQ_RISING, handler=handle_touch)

def read_dht22():
    temperature = random.uniform(20, 40)
    humidity = random.uniform(30, 80)
    return temperature, humidity

def read_soil_sensor():
    soil_moisture = random.randint(400, 800)
    return soil_moisture

def store_data(X_data, y_data, new_X, new_y):
    X_data.append(new_X)
    y_data.append(new_y)
    if len(X_data) > MAX_DATA_SIZE:
        X_data.pop(0)
        y_data.pop(0)

def calculate_regression_coefficients(X, y):
    if len(X) < 4:
        print("Không đủ dữ liệu để tính hồi quy!")
        return [0, 0, 0, 0]

    def transpose(matrix):
        return list(map(list, zip(*matrix)))

    def matmul(A, B):
        result = [[0] * len(B[0]) for _ in range(len(A))]
        for i in range(len(A)):
            for j in range(len(B[0])):
                for k in range(len(B)):
                    result[i][j] += A[i][k] * B[k][j]
        return result

    def inverse(matrix):
        n = len(matrix)
        identity_matrix = [[float(i == j) for i in range(n)] for j in range(n)]
        augmented_matrix = [row[:] for row in matrix]

        for i in range(n):
            if augmented_matrix[i][i] == 0:
                for j in range(i + 1, n):
                    if augmented_matrix[j][i] != 0:
                        augmented_matrix[i], augmented_matrix[j] = augmented_matrix[j], augmented_matrix[i]
                        identity_matrix[i], identity_matrix[j] = identity_matrix[j], identity_matrix[i]
                        break
                else:
                    raise ValueError("Matrix is not invertible")

            diag = augmented_matrix[i][i]
            for j in range(n):
                augmented_matrix[i][j] /= diag
                identity_matrix[i][j] /= diag

            for j in range(n):
                if i != j:
                    factor = augmented_matrix[j][i]
                    for k in range(n):
                        augmented_matrix[j][k] -= factor * augmented_matrix[i][k]
                        identity_matrix[j][k] -= factor * identity_matrix[i][k]

        return identity_matrix

    X_T = transpose(X)
    X_T_X = matmul(X_T, X)
    X_T_y = matmul(X_T, [[yi] for yi in y])
    X_T_X_inv = inverse(X_T_X)
    b = matmul(X_T_X_inv, X_T_y)
    b = [row[0] for row in b]

    return b

def reset_system():
    global X_data, y_data, is_reset
    X_data.clear()
    y_data.clear()
    is_reset = False
    print("Hệ thống đã được reset!")

def send_initial_data():
    global is_reset
    print("Chờ 2 giây trước khi bắt đầu gửi dữ liệu lần đầu...")
    time.sleep(2)
    print("Bắt đầu gửi 10 lần đo đầu tiên...")
    for i in range(10):
        if is_reset:
            print("Ngắt được kích hoạt trong quá trình đo, reset ngay lập tức!")
            return

        temperature, humidity = read_dht22()
        soil_moisture = read_soil_sensor()
        rain_status = 1 if (temperature < TEMP_THRESHOLD_COLD or humidity > HUMIDITY_THRESHOLD or soil_moisture > SOIL_MOISTURE_THRESHOLD) else 0
        new_X = [1, temperature, humidity, soil_moisture]
        store_data(X_data, y_data, new_X, rain_status)

        data_to_send = f"{temperature:.2f},{humidity:.2f},{soil_moisture},{rain_status}\n"
        uart.write(data_to_send)
        print(f"Lần đo {i+1}: {data_to_send}")

        time.sleep(2)

    b = calculate_regression_coefficients(X_data, y_data)
    print(f"Phương trình hồi quy tuyến tính sau 10 lần đo: y = {b[0]} + {b[1]}*x1 + {b[2]}*x2 + {b[3]}*x3")

def main_workflow():
    global X_data, y_data, is_reset
    while True:
        if is_reset:
            reset_system()
            return

        send_initial_data()

        try:
            b = calculate_regression_coefficients(X_data, y_data)
            print(f"Hệ số hồi quy: {b}")
        except ValueError as e:
            print(f"Lỗi: {e}")
            reset_system()
            continue

        while True:
            if is_reset:
                reset_system()
                return

            temperature, humidity = read_dht22()
            soil_moisture = read_soil_sensor()
            rain_prediction = 1 if (b[0] + b[1] * temperature + b[2] * humidity + b[3] * soil_moisture) >= 0.5 else 0

            data_to_send = f"{temperature:.2f},{humidity:.2f},{soil_moisture},{rain_prediction}\n"
            uart.write(data_to_send)
            print(f"Gửi dữ liệu: {data_to_send}")

            new_X = [1, temperature, humidity, soil_moisture]
            store_data(X_data, y_data, new_X, rain_prediction)

            b = calculate_regression_coefficients(X_data, y_data)
            print(f"Phương trình hồi quy tuyến tính: y = {b[0]} + {b[1]}*x1 + {b[2]}*x2 + {b[3]}*x3")
            print(f"Mảng X sau khi cập nhật: {X_data}")
            print(f"Mảng Y sau khi cập nhật: {y_data}")

            time.sleep(2)

while True:
    main_workflow()
