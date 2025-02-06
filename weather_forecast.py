import requests
import json
import os
import sys
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from datetime import datetime
import time

# 获取脚本所在目录路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'configs.json')
LOG_PATH = os.path.join(SCRIPT_DIR, 'log.txt')  # 日志文件路径

# 配置重试参数
MAX_RETRIES = 16  # 最大重试次数
RETRY_INTERVAL = 60  # 重试间隔（秒）

def log_message(message, success=True):
    """记录日志信息"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}\n"
    with open(LOG_PATH, 'a', encoding='utf-8') as log_file:
        log_file.write(log_entry)
    if success:
        print(f"成功：{message}")
    else:
        print(f"错误：{message}")

def load_config():
    """加载配置文件"""
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as file:
            return json.load(file)
    except FileNotFoundError:
        log_message(f"配置文件 {CONFIG_PATH} 未找到", success=False)
        sys.exit(1)
    except json.JSONDecodeError:
        log_message("配置文件格式不正确", success=False)
        sys.exit(1)

def get_weather_data(api_key, city_code):
    """从高德API获取天气数据"""
    url = f'https://restapi.amap.com/v3/weather/weatherInfo?city={city_code}&key={api_key}&extensions=all'
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        log_message(f"请求天气数据失败: {str(e)}", success=False)
        return None

def parse_weather_data(data):
    """解析天气数据并返回明天预报"""
    if not isinstance(data, dict):
        log_message("API返回数据格式异常", success=False)
        return None, None

    # 检查API返回状态码
    if data.get('infocode') != '10000':
        log_message(f"API错误: {data.get('info', '未知错误')}", success=False)
        return None, None

    forecasts = data.get('forecasts')
    if not forecasts:
        log_message("未找到天气预报数据", success=False)
        return None, None

    casts = forecasts[0].get('casts', [])  # 注意原API返回结构
    if len(casts) < 2:
        log_message("天气预报数据不足", success=False)
        return None, None

    return casts[1], forecasts[0].get('city')  # 返回明天预报

def check_weather_conditions(tomorrow_data, warning_weather, temp_min, temp_max):
    """检查天气条件是否触发预警"""
    dayweather = tomorrow_data.get('dayweather', '')
    nightweather = tomorrow_data.get('nightweather', '')
    daytemp = int(tomorrow_data.get('daytemp', 0))
    nighttemp = int(tomorrow_data.get('nighttemp', 0))

    # 检查恶劣天气类型
    is_warning = any(w in [dayweather, nightweather] for w in warning_weather)

    # 检查温度范围（修正逻辑）
    temp_check = (
        daytemp <= int(temp_min) or daytemp >= int(temp_max) or
        nighttemp <= int(temp_min) or nighttemp >= int(temp_max)
    )
    
    return is_warning or temp_check

def send_email(subject, body, config):
    """发送邮件通知"""
    sender = config['email']['sender']
    receiver = config['email']['receiver']
    smtp_server = 'smtp.qq.com'  # QQ邮箱的SMTP服务器
    smtp_port = 465  # QQ邮箱的SMTP端口（SSL）
    smtp_user = config['email']['smtp_user']
    smtp_password = config['email']['smtp_password']  # 使用QQ邮箱的授权码

    # 创建邮件对象
    msg = MIMEMultipart()
    msg['From'] = Header(sender)
    msg['To'] = Header(receiver)
    msg['Subject'] = Header(subject, 'utf-8')

    # 添加邮件正文
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    try:
        server = smtplib.SMTP_SSL(smtp_server, smtp_port)
        server.login(smtp_user, smtp_password)
        server.sendmail(sender, receiver, msg.as_string())
        log_message("邮件已发送成功！")
    except smtplib.SMTPException as e:
        log_message(f"邮件发送失败: {str(e)}", success=False)

def main():
    try:
        # 加载配置
        config = load_config()
        
        retries = 0
        while retries < MAX_RETRIES:
            # 获取天气数据
            weather_data = get_weather_data(config['apiKey'], config['cityCode'])
            if weather_data is None:
                log_message("获取天气数据失败，正在重试...")
                time.sleep(RETRY_INTERVAL)
                retries += 1
                continue
            
            # 解析数据
            tomorrow_data, city_name = parse_weather_data(weather_data)
            if tomorrow_data is None or city_name is None:
                log_message("解析天气数据失败，正在重试...")
                time.sleep(RETRY_INTERVAL)
                retries += 1
                continue
            
            # 检查预警条件
            if check_weather_conditions(
                tomorrow_data,
                config['warning_weather'],
                config['mintemp'],
                config['maxtemp']
            ):
                log_message("明日天气异常，触发预警！")
                # 构造邮件内容
                subject = "天气预警通知"
                body = (
                    f"明天{city_name}天气有变，注意！\n"
                    f"白天天气：{tomorrow_data['dayweather']}\n"
                    f"夜间天气：{tomorrow_data['nightweather']}\n"
                    f"白天温度：{tomorrow_data['daytemp']}°C\n"
                    f"夜间温度：{tomorrow_data['nighttemp']}°C\n"
                )
                
                # 发送邮件
                email_retries = 0
                while email_retries < MAX_RETRIES:
                    try:
                        send_email(subject, body, config)
                        break  # 如果邮件发送成功，则退出重试循环
                    except Exception as e:
                        log_message(f"邮件发送失败，正在重试... 错误信息: {str(e)}", success=False)
                        time.sleep(RETRY_INTERVAL)
                        email_retries += 1
                else:
                    log_message("达到邮件发送最大重试次数，邮件发送失败", success=False)
            else:
                log_message("明日天气正常")
            break  # 成功完成任务后退出循环
        else:
            log_message("达到最大重试次数，任务失败", success=False)
    except Exception as e:
        log_message(f"程序运行异常: {str(e)}", success=False)
if __name__ == "__main__":
    main()